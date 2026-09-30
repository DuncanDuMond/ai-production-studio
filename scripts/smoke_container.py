"""Isolated offline Docker acceptance; no project database or provider credentials.

Example: python scripts/smoke_container.py --evidence /managed/scratch/smoke.json
Only uniquely named resources created here are removed. Docker output and payloads
are captured, never printed; evidence contains assertions and identifiers only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

COLLECTOR_IMAGE = (
    "ghcr.io/open-telemetry/opentelemetry-collector-releases/"
    "opentelemetry-collector:0.160.0@sha256:"
    "e495787f07dbe432ce763ebaf5bc3d113850e9eee2250ade7a3da6a882d0d69a"
)
COLLECTOR_CONFIG = """receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
exporters:
  debug:
    verbosity: detailed
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [debug]
"""


class SmokeFailure(RuntimeError):
    """Contains only a fixed diagnostic label, never subprocess/HTTP bodies."""


def require(condition, label):
    if not condition:
        raise SmokeFailure(label)


def default_scratch():
    if os.name == "nt":
        return Path.home() / "AppData/Local/hermes/cache/scratch"
    return Path(os.getenv("RUNNER_TEMP") or os.getenv("TMPDIR") or str(Path.home() / ".cache/hermes/scratch"))


def app_image(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:+-]*(?:@sha256:[a-f0-9]{64})?", value):
        raise argparse.ArgumentTypeError("invalid application image")
    return value


def pinned_image(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[a-f0-9]{64}", value):
        raise argparse.ArgumentTypeError("Collector image requires an exact sha256 digest")
    return value


def parser():
    result = argparse.ArgumentParser(
        description=__doc__, formatter_class=lambda prog: argparse.ArgumentDefaultsHelpFormatter(prog, width=120),
    )
    result.add_argument("--image", type=app_image, default="ai-production-studio:update",
                        help="Already built application image")
    result.add_argument("--collector-image", type=pinned_image, default=COLLECTOR_IMAGE,
                        help="Exact pinned core Collector image; no other services")
    result.add_argument("--evidence", type=Path, required=True, help="Redacted JSON evidence destination")
    result.add_argument("--scratch-dir", type=Path, default=default_scratch(), help="Managed temporary config root")
    return result


class Docker:
    def __init__(self, token):
        self.env = os.environ.copy()
        self.env.update(STUDIO_API_TOKEN=token, MSYS_ARG_CONV_EXCL="*")
        self.resources = []
        self.pending = {}

    def run(self, *args, timeout=60, check=True):
        try:
            result = subprocess.run(["docker", *args], env=self.env, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise SmokeFailure("docker command unavailable or timed out") from None
        if check and result.returncode:
            raise SmokeFailure(f"docker {args[0]} failed (output withheld)")
        return result

    def create(self, kind, name, *args):
        owner = next((args[i + 1].removeprefix("studio.smoke.owner=")
                      for i, arg in enumerate(args[:-1])
                      if arg == "--label" and args[i + 1].startswith("studio.smoke.owner=")), "")
        require(bool(owner), "resource requires a nonempty studio.smoke.owner label")
        self.pending[(kind, name)] = owner
        if kind == "container":
            result = self.run("create", "--name", name, *args)
        else:
            result = self.run(kind, "create", *args, name)
        # Volumes lack immutable IDs and create is idempotent for existing names.
        identity = name if kind == "volume" else result.stdout.strip()
        require(bool(identity), "created resource identity could not be verified")
        resource = self.inspect_ownership(kind, identity)
        exact_name = "/" + name if kind == "container" else name
        require(resource is not None and resource.get("name") == exact_name
                and resource.get("owner") == owner
                and (kind == "volume" or resource.get("id") == identity),
                "created resource ownership could not be verified")
        self.resources.append((kind, name, owner, identity))
        del self.pending[(kind, name)]

    def inspect_ownership(self, kind, name):
        # Project only identity and ownership; never read container environment.
        identity = '' if kind == "volume" else ',"id":{{json .Id}}'
        label_path = ".Config.Labels" if kind == "container" else ".Labels"
        result = self.run(kind, "inspect", "--format",
                          '{"name":{{json .Name}},"owner":{{json (index '
                          + label_path + ' "studio.smoke.owner")}}' + identity + '}',
                          name, check=False)
        if result.returncode:
            return None
        resource = json.loads(result.stdout)
        require(isinstance(resource, dict), "resource ownership response is invalid")
        if kind != "volume":
            require(isinstance(resource.get("id"), str) and bool(resource["id"]),
                    "resource ownership identity is invalid")
        return resource

    def cleanup(self):
        remaining = []
        for (kind, name), owner in reversed(list(self.pending.items())):
            try:
                resource = self.inspect_ownership(kind, name)
                if resource is not None:
                    exact_name = "/" + name if kind == "container" else name
                    if resource.get("name") == exact_name and resource.get("owner") == owner:
                        target = name if kind == "volume" else resource["id"]
                        args = (kind, "rm", "-f", target) if kind == "container" else (kind, "rm", target)
                        self.run(*args, check=False)
                        listing_args = ("--all",) if kind == "container" else ()
                        listing = self.run(kind, "ls", *listing_args, "--format",
                                           "{{.Names}}" if kind == "container" else "{{.Name}}")
                        if listing.returncode or name in listing.stdout.splitlines():
                            remaining.append(name)
                    else:
                        remaining.append(name)
                else:
                    # Inspect failure is not proof of absence (it may be a daemon error).
                    listing_args = ("--all",) if kind == "container" else ()
                    listing = self.run(kind, "ls", *listing_args, "--format",
                                       "{{.Names}}" if kind == "container" else "{{.Name}}")
                    if listing.returncode or name in listing.stdout.splitlines():
                        remaining.append(name)
            except (SmokeFailure, ValueError, KeyError, TypeError):
                remaining.append(name)
        for kind, name, owner, identity in reversed(self.resources):
            try:
                # Inspect the retained ID, not a mutable name. Owned renames are valid.
                # Volumes have no immutable ID: check their label immediately before rm.
                resource = self.inspect_ownership(kind, identity)
                if (resource is None or resource.get("owner") != owner
                        or (kind == "volume" and resource.get("name") != name)
                        or (kind != "volume" and resource.get("id") != identity)):
                    remaining.append(name)
                    continue
                args = (kind, "rm", "-f", identity) if kind == "container" else (kind, "rm", identity)
                self.run(*args, check=False)
                # Verify both ID absence (including failed removal after rename) and
                # original-name absence (including a same-name replacement).
                listing_args = ("--all", "--no-trunc") if kind == "container" else (
                    ("--no-trunc",) if kind == "network" else ())
                listing_format = "{{.Name}}" if kind == "volume" else (
                    "{{.ID}} {{.Names}}" if kind == "container" else "{{.ID}} {{.Name}}")
                listing = self.run(kind, "ls", *listing_args, "--format", listing_format)
                rows = listing.stdout.splitlines()
                if kind == "volume":
                    unverified = name in rows
                else:
                    entries = [row.split(" ", 1) for row in rows]
                    unverified = any(len(entry) != 2 or entry[0] == identity or entry[1] == name
                                     for entry in entries)
                if listing.returncode or unverified:
                    remaining.append(name)
            except (SmokeFailure, ValueError, KeyError, TypeError):
                # Treat unverified resources as remaining, but attempt every owned resource.
                remaining.append(name)
        return remaining


def wait_for(predicate, label, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.5)
    raise SmokeFailure(label)


def exercise(docker, options, report, config, token):
    suffix = secrets.token_hex(8)
    network, volume, app, collector = (f"studio-smoke-{suffix}-{part}" for part in
                                      ("net", "data", "app", "collector"))
    label = f"studio.smoke.owner={suffix}"
    report["resource_names"] = [network, volume, app, collector]
    ingress = f"studio-smoke-{suffix}-ingress"
    report["resource_names"].append(ingress)
    report["image_id"] = docker.run("image", "inspect", "--format", "{{.Id}}", options.image).stdout.strip()
    docker.create("network", network, "--internal", "--label", label)
    internal = docker.run("network", "inspect", "--format", "{{.Internal}}", network).stdout.strip()
    require(internal == "true", "Collector network must prohibit external egress")
    # Docker 29 suppresses published ports on internal-only networks. An app-only
    # ingress bridge enables loopback DNAT; disable outbound IP masquerading.
    # The Collector remains exclusively on the private internal network.
    docker.create("network", ingress, "--label", label, "--opt",
                  "com.docker.network.bridge.enable_ip_masquerade=false")
    docker.create("volume", volume, "--label", label)
    common = ("--network", network, "--label", label, "--read-only", "--cap-drop", "ALL",
              "--security-opt", "no-new-privileges:true", "--memory", "2g",
              "--log-opt", "max-size=10m", "--log-opt", "max-file=1")
    docker.create("container", collector, *common, "--network-alias", "collector", "--user", "10001:10001",
                  "--mount", f"type=bind,source={config.as_posix()},target=/etc/smoke.yaml,readonly",
                  options.collector_image, "--config=/etc/smoke.yaml")
    docker.run("start", collector)
    wait_for(lambda: "Everything is ready" in docker.run("logs", collector).stdout
             + docker.run("logs", collector).stderr, "Collector readiness timed out", timeout=45)
    docker.create("container", app, *common, "--network", ingress, "--publish", "127.0.0.1::8765",
                  "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m", "--mount",
                  f"type=volume,source={volume},target=/app/data",
                  "-e", "STUDIO_API_TOKEN", "-e", "STUDIO_ENV=production",
                  "-e", "STUDIO_ENABLE_LIVE=false", "-e", "STUDIO_DB_PATH=/app/data/studio.db",
                  "-e", "STUDIO_OTLP_ENDPOINT=http://collector:4318/v1/traces",
                  "-e", "OTEL_SDK_DISABLED=false", "-e", "HAYSTACK_TELEMETRY_ENABLED=false",
                  "-e", "DEEPEVAL_TELEMETRY_OPT_OUT=true", "-e", "RAGAS_DO_NOT_TRACK=true",
                  "-e", "GUARDRAILS_TELEMETRY_ENABLED=false", "-e", "DO_NOT_TRACK=1", options.image)
    docker.run("start", app)

    def base_url():
        bindings = json.loads(docker.run("container", "inspect", "--format",
                                        '{{json (index .NetworkSettings.Ports "8765/tcp")}}', app).stdout)
        require(len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1", "non-loopback port mapping")
        port = int(bindings[0]["HostPort"])
        require(port > 0, "ephemeral port not assigned")
        report["loopback_port"] = port
        return f"http://127.0.0.1:{port}"

    url = base_url()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, body=None, *, authenticated=True, expected=200, timeout=90):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                     headers=headers)
        try:
            response = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            require(response.status == expected, f"HTTP contract failed: {path.rsplit('/', 1)[-1]} status")
            payload = json.loads(response.read())
            return payload, response.headers

    def ready():
        try:
            return request("/api/health", timeout=3)[0]["status"] == "ok"
        except (OSError, SmokeFailure):
            return False

    wait_for(ready, "application readiness timed out", timeout=90)
    require(request("/api/health")[0]["mode"] == "local", "live mode unexpectedly enabled")
    request("/api/projects", authenticated=False, expected=401)
    settings = request("/api/settings")[0]
    require(settings["authentication_required"] and settings["telemetry_enabled"]
            and not settings["live_enabled"], "production auth/telemetry settings mismatch")
    report["checks"]["production_auth"] = True
    hardening = json.loads(docker.run("container", "inspect", "--format",
                          '{"readonly":{{.HostConfig.ReadonlyRootfs}},"caps":{{json .HostConfig.CapDrop}},'
                          '"security":{{json .HostConfig.SecurityOpt}}}', app).stdout)
    uid = docker.run("exec", app, "python", "-c", "import os; print(os.getuid())").stdout.strip()
    require(uid.isdigit() and int(uid) != 0 and hardening["readonly"] and "ALL" in hardening["caps"]
            and "no-new-privileges:true" in hardening["security"], "container hardening mismatch")
    report["checks"]["hardening"] = True
    project = request("/api/projects", {"name": "Isolated smoke"}, expected=201)[0]
    prefix = f"/api/projects/{project['id']}"
    marker = "PRIVATE_SMOKE_" + secrets.token_hex(16)
    prompt, document = f"Find retrieval evidence {marker}", f"Retrieval evidence document {marker}."
    doc = request(prefix + "/documents", {"title": "Local evidence", "content": document}, expected=201)[0]
    expected_answer = "Local deterministic fixture (not AI-generated):\n" + document
    runs = []
    for reference, status, score, check in (
        (expected_answer, "passed", 1.0, "reference_match"),
        ("NONMATCH_" + secrets.token_hex(16), "blocked", 0.0, "reference_nonmatch"),
    ):
        run = request(prefix + "/runs", {"prompt": prompt, "mode": "local", "engine": "pydantic-ai",
                                         "top_k": 1, "reference": reference}, expected=201)[0]
        require(run["status"] == status and run["error"] is None, "real local run failed")
        require(run["guardrails"]["passed"] and run["output"]["answer"] == expected_answer
                and run["output"]["citations"] == [{"id": doc["id"], "title": doc["title"], "content": document}],
                "local generation/retrieval/provenance mismatch")
        require({step["framework"] for step in run["steps"]} ==
                {"haystack", "pydantic-ai", "guardrails", "ragas", "deepeval"}, "framework stages missing")
        metrics = {metric["framework"]: metric for metric in run["evaluations"]}
        require(set(metrics) == {"ragas", "deepeval"} and all(
            metric["score"] == score and metric["passed"] is (score == 1.0) for metric in metrics.values()
        ), "reference metrics mismatch")
        trace = run["trace_id"]
        require(isinstance(trace, str) and re.fullmatch(r"[0-9a-f]{32}", trace)
                and int(trace, 16) != 0, "invalid run trace identifier")
        require(request(f"/api/runs/{run['id']}")[0] == run, "run read-back differs")
        exported, headers = request(f"/api/runs/{run['id']}/export")
        require(exported == run and "attachment" in headers["Content-Disposition"], "run export differs")
        report["checks"][check] = True
        runs.append(run)
    report["checks"].update(local_pipeline=True, run_export=True)
    report["trace_ids"] = [run["trace_id"] for run in runs]
    for malformed in ("yes", "true", 1, 0, None, [], {}):
        request(prefix + "/runs", {"prompt": prompt, "mode": "live", "allow_paid": malformed}, expected=422)
    for consent in (False, True):
        request(prefix + "/runs", {"prompt": prompt, "mode": "live", "allow_paid": consent}, expected=403)
    require(request(prefix + "/runs")[0]["items"] == list(reversed(runs)), "denied calls were persisted")
    report["checks"]["strict_consent"] = True

    def collector_traces():
        logs = docker.run("logs", collector)
        text = logs.stdout + logs.stderr
        require(not any(value in text for value in (prompt, document, marker, token)), "Collector privacy leak")
        return "studio.run" in text and all(trace in text for trace in report["trace_ids"])

    wait_for(collector_traces, "run trace absent from actual Collector logs", timeout=45)
    report["checks"].update(collector_trace=True, telemetry_privacy=True)
    docker.run("restart", "--time", "10", app)
    url = base_url()
    wait_for(ready, "application restart readiness timed out", timeout=60)
    require(request(prefix + "/documents")[0]["items"] == [doc], "document lost across restart")
    for run in runs:
        require(request(f"/api/runs/{run['id']}")[0] == run, "run lost across restart")
        require(request(f"/api/runs/{run['id']}/export")[0] == run, "persisted export differs")
    require(any(item["id"] == project["id"] for item in request("/api/projects")[0]["items"]),
            "project lost across restart")
    logs = docker.run("logs", app)
    require(not any(value in logs.stdout + logs.stderr for value in (prompt, document, marker, token)),
            "application log privacy leak")
    require(collector_traces(), "Collector trace disappeared")
    report["checks"]["persistence"] = True


def main(argv=None):
    options = parser().parse_args(argv)
    require(bool(options.image.strip()) and not options.image.startswith("-"), "invalid application image")
    options.scratch_dir = options.scratch_dir.expanduser().resolve()
    options.evidence = options.evidence.expanduser().resolve()
    options.scratch_dir.mkdir(parents=True, exist_ok=True)
    options.evidence.parent.mkdir(parents=True, exist_ok=True)
    report = {"status": "failed", "image": options.image, "collector_image": options.collector_image,
              "checks": {}, "trace_ids": [], "resources_remaining": [], "no_live_calls": True}
    token = secrets.token_urlsafe(48)
    docker = Docker(token)
    try:
        with tempfile.TemporaryDirectory(prefix="studio-smoke-", dir=options.scratch_dir) as directory:
            config = Path(directory) / "collector.yaml"
            config.write_text(COLLECTOR_CONFIG, encoding="utf-8")
            exercise(docker, options, report, config, token)
        report["status"] = "passed"
    except Exception as exc:
        report["error"] = str(exc) if isinstance(exc, SmokeFailure) else f"{type(exc).__name__}: details withheld"
    finally:
        try:
            report["resources_remaining"] = docker.cleanup()
            report["checks"]["cleanup"] = not report["resources_remaining"]
        except Exception:
            report["checks"]["cleanup"] = False
            report["error"] = "cleanup could not be verified; inspect only listed resource_names"
        if not report["checks"].get("cleanup"):
            report["status"] = "failed"
        options.evidence.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, separators=(",", ":")))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
