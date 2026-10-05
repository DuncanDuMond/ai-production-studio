from importlib import import_module

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    with TestClient(import_module("studio.app").create_app(db_path=tmp_path / "test.db")) as value:
        yield value


def test_document_lifecycle_and_workspace_isolation(client):
    first = client.post("/api/projects", json={"name": "First"}).json()["id"]
    second = client.post("/api/projects", json={"name": "Second"}).json()["id"]
    doc = client.post(
        f"/api/projects/{first}/documents", json={"title": "Notes", "content": "Retrieval evidence."}
    )
    assert doc.status_code == 201
    assert (
        client.get(f"/api/projects/{first}/documents").json()["items"][0]["content"] == "Retrieval evidence."
    )
    assert client.get(f"/api/projects/{second}/documents").json() == {"items": []}
    assert client.get("/api/summary").json()["documents"] == 1
    assert client.delete(f"/api/projects/{second}/documents/{doc.json()['id']}").status_code == 404
    assert client.delete(f"/api/projects/{first}/documents/{doc.json()['id']}").status_code == 204
    assert client.get(f"/api/projects/{first}/documents").json() == {"items": []}
    assert client.get("/api/projects/missing/documents").status_code == 404
    assert (
        client.post(f"/api/projects/{first}/documents", json={"title": "", "content": "x"}).status_code == 422
    )
    assert client.post("/api/projects", json={"name": "   "}).status_code == 422


def test_auth_origin_size_limits_and_production_fail_closed(tmp_path, monkeypatch):
    module = import_module("studio.app")
    monkeypatch.setenv("STUDIO_API_TOKEN", "x" * 40)
    with TestClient(module.create_app(db_path=tmp_path / "secure.db")) as secured:
        assert secured.get("/api/health").status_code == 200
        assert secured.get("/api/projects").status_code == 401
        headers = {"Authorization": "Bearer " + "x" * 40}
        assert secured.get("/api/projects", headers=headers).status_code == 200
        assert (
            secured.post(
                "/api/projects", json={"name": "x"}, headers=headers | {"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert (
            secured.post(
                "/api/projects", json={"name": "x"}, headers=headers | {"Origin": "http://testserver"}
            ).status_code
            == 201
        )
        assert secured.post("/api/projects", content=b"x" * 300000, headers=headers).status_code == 413
        response = secured.get("/api/settings", headers=headers)
        assert response.json()["authentication_required"] is True
        assert "x" * 40 not in response.text
        assert response.headers["x-content-type-options"] == "nosniff"
        assert secured.get("/api/health", headers={"Host": "evil.test"}).status_code == 400
    monkeypatch.delenv("STUDIO_API_TOKEN")
    monkeypatch.setenv("STUDIO_ENV", "production")
    with pytest.raises(ValueError, match="STUDIO_API_TOKEN"):
        module.create_app(db_path=tmp_path / "bad.db")


def test_runs_persist_and_export_with_bounded_execution(tmp_path, monkeypatch):
    module = import_module("studio.app")
    fixture = {
        "status": "passed",
        "output": {"answer": "Unit-test response", "citations": [], "confidence": 1.0},
        "evaluations": [],
        "guardrails": {"passed": True, "detail": "Unit-test fixture"},
        "steps": [],
        "trace_id": None,
        "error": None,
    }

    def runner(prompt, documents, **kwargs):
        return fixture

    app = module.create_app(db_path=tmp_path / "runs.db", runner=runner)
    with TestClient(app) as tested:
        project_id = tested.post("/api/projects", json={"name": "Runs"}).json()["id"]
        result = tested.post(f"/api/projects/{project_id}/runs", json={"prompt": "Check", "mode": "local"})
        assert result.status_code == 201
        run = result.json()
        assert run["latency_ms"] >= 0
        assert tested.get(f"/api/runs/{run['id']}").json() == run
        assert tested.get(f"/api/projects/{project_id}/runs").json()["items"][0] == run
        export = tested.get(f"/api/runs/{run['id']}/export")
        assert export.json() == run
        assert "attachment" in export.headers["content-disposition"]
        assert tested.get("/api/summary").json()["passed_runs"] == 1
        assert tested.get("/api/projects").json()["items"][0]["run_count"] == 1
        assert tested.get("/api/runs/missing").status_code == 404
        assert (
            tested.post(
                f"/api/projects/{project_id}/runs",
                json={"prompt": "Check", "mode": "live", "allow_paid": True},
            ).status_code
            == 403
        )
        assert (
            tested.post(f"/api/projects/{project_id}/runs", json={"prompt": "", "top_k": 999}).status_code
            == 422
        )
        app.state.run_slots.acquire()
        app.state.run_slots.acquire()
        try:
            assert (
                tested.post(f"/api/projects/{project_id}/runs", json={"prompt": "Check"}).status_code == 429
            )
        finally:
            app.state.run_slots.release()
            app.state.run_slots.release()
    with TestClient(module.create_app(db_path=tmp_path / "runs.db")) as tested:
        assert tested.get(f"/api/runs/{run['id']}").json() == run


def test_health_reports_live_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ENABLE_LIVE", "true")
    with TestClient(import_module("studio.app").create_app(db_path=tmp_path / "live.db")) as tested:
        assert tested.get("/api/health").json()["mode"] == "live-enabled"
        project_id = tested.post("/api/projects", json={"name": "Live consent"}).json()["id"]
        assert (
            tested.post(
                f"/api/projects/{project_id}/runs", json={"prompt": "Check", "mode": "live"}
            ).status_code
            == 403
        )
        for malformed_consent in ("yes", "true", 1):
            assert (
                tested.post(
                    f"/api/projects/{project_id}/runs",
                    json={
                        "prompt": "Check",
                        "mode": "live",
                        "allow_paid": malformed_consent,
                    },
                ).status_code
                == 422
            )


def test_settings_respect_explicit_otel_disable(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_OTLP_ENDPOINT", "http://collector:4318/v1/traces")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    with TestClient(import_module("studio.app").create_app(db_path=tmp_path / "otel.db")) as tested:
        assert tested.get("/api/settings").json()["telemetry_enabled"] is False


def test_ui_and_integration_catalog_are_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "Studio" in page.text
    assert client.get("/static/app.js").status_code == 200
    catalog = client.get("/api/integrations")
    assert catalog.status_code == 200
    assert catalog.json()["total"] == 10
    assert {x["id"] for x in catalog.json()["items"]} == {
        "otel",
        "langfuse",
        "phoenix",
        "ragas",
        "deepeval",
        "guardrails",
        "pydantic-ai",
        "instructor",
        "litellm",
        "haystack",
    }


def test_projects_persist_across_app_restarts(tmp_path):
    module = import_module("studio.app")
    db = tmp_path / "studio.db"
    with TestClient(module.create_app(db_path=db)) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/projects").json() == {"items": []}
        response = client.post("/api/projects", json={"name": "Studio QA", "description": "Test workspace"})
        assert response.status_code == 201
        project = response.json()
        assert project["name"] == "Studio QA"
        assert project["document_count"] == 0
    with TestClient(module.create_app(db_path=db)) as client:
        assert client.get("/api/projects").json()["items"][0]["id"] == project["id"]
        assert client.get("/api/summary").json()["projects"] == 1


def test_published_compose_services_have_dedicated_ingress():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1]
    base = yaml.safe_load((root / "deploy/compose.yml").read_text(encoding="utf-8"))
    assert base["networks"]["default"]["internal"] is True
    assert base["networks"]["ingress"]["driver_opts"][
        "com.docker.network.bridge.enable_ip_masquerade"
    ] == "false"
    for filename, services in (
        ("compose.yml", ("studio",)),
        ("compose.phoenix.yml", ("phoenix",)),
        ("compose.langfuse.yml", ("langfuse-web", "minio")),
        ("compose.telemetry-phoenix.yml", ("collector",)),
        ("compose.telemetry-langfuse.yml", ("collector",)),
        ("compose.telemetry-both.yml", ("collector",)),
    ):
        config = yaml.safe_load((root / "deploy" / filename).read_text(encoding="utf-8"))
        for name in services:
            assert set(config["services"][name]["networks"]) == {"default", "ingress"}


def test_container_smoke_help_uses_stdlib_cli():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/smoke_container.py"), "--help"],
        cwd=root, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    for option in ("--image", "--collector-image", "--evidence", "--scratch-dir"):
        assert option in result.stdout
    assert "ai-production-studio:update" in result.stdout


def test_isolated_docker_smoke_opt_in():
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    if os.getenv("STUDIO_DOCKER_E2E") != "1":
        pytest.skip("Set STUDIO_DOCKER_E2E=1 to run real isolated Docker acceptance")
    root = Path(__file__).resolve().parents[1]
    scratch = Path(os.getenv("STUDIO_SMOKE_SCRATCH", str(
        Path.home() / "AppData/Local/hermes/cache/scratch/studio-update"
    )))
    scratch.mkdir(parents=True, exist_ok=True)
    evidence = scratch / "docker-smoke-pytest.json"
    result = subprocess.run(
        [sys.executable, str(root / "scripts/smoke_container.py"),
         "--image", os.getenv("STUDIO_SMOKE_IMAGE", "ai-production-studio:update"),
         "--scratch-dir", str(scratch), "--evidence", str(evidence)],
        cwd=root, capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(evidence.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    for check in ("production_auth", "hardening", "local_pipeline", "reference_match",
                  "reference_nonmatch", "run_export", "strict_consent", "persistence",
                  "collector_trace", "telemetry_privacy", "cleanup"):
        assert report["checks"][check] is True
    assert len(report["trace_ids"]) == 2
    assert report["resources_remaining"] == []


def test_container_smoke_rejects_empty_image_before_docker():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/smoke_container.py"),
         "--image", "", "--evidence", "unused-evidence.json"],
        cwd=root, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 2
    assert "invalid application image" in result.stderr


def test_ci_runs_real_docker_smoke_before_unsuppressed_audit():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1]
    steps = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]["checks"]["steps"]
    build = next((i for i, step in enumerate(steps) if "docker build" in step.get("run", "")), None)
    smoke = next((i for i, step in enumerate(steps) if "scripts/smoke_container.py" in step.get("run", "")), None)
    audit = next(i for i, step in enumerate(steps) if "pip-audit" in step.get("run", ""))
    assert build is not None and smoke is not None
    assert build < smoke < audit
    assert "--evidence" in steps[smoke]["run"]
    assert "--scratch-dir" in steps[smoke]["run"]
    assert "--ignore-vuln" not in steps[audit]["run"]
    assert "||" not in steps[audit]["run"]
    assert steps[audit].get("continue-on-error", False) is False
    assert steps[audit]["if"] == "${{ always() }}"


@pytest.fixture
def smoke_owned_resource():
    import json
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/smoke_container.py"))

    def make(kind):
        name = "registered-" + kind
        state = {"resource": None, "commands": [], "removed": [],
                 "failure_type": namespace["SmokeFailure"]}

        class ResourceDaemon(namespace["Docker"]):
            def run(self, *args, **kwargs):
                state["commands"].append(args)
                if "create" in args:
                    state["resource"] = {"name": "/" + name if kind == "container" else name,
                                         "owner": "this-attempt", "id": "original-id"}
                    output = name if kind == "volume" else "original-id"
                elif "inspect" in args:
                    resource = state["resource"]
                    if resource is None or args[-1] not in (resource["name"].lstrip("/"), resource["id"]):
                        return SimpleNamespace(returncode=1, stdout="", stderr="not found")
                    output = json.dumps(resource) if "studio.smoke.owner" in args[-2] else resource["id"]
                elif "rm" in args:
                    state["removed"].append(args[-1])
                    resource = state["resource"]
                    if resource is not None and args[-1] in (resource["name"].lstrip("/"), resource["id"]):
                        state["resource"] = None
                    output = ""
                elif "ls" in args:
                    resource = state["resource"]
                    output = ""
                    if resource is not None:
                        output = resource["name"].lstrip("/")
                        if "{{.ID}}" in args[-1]:
                            output = resource["id"] + " " + output
                else:
                    raise AssertionError("unexpected Docker operation")
                return SimpleNamespace(returncode=0, stdout=output, stderr="")

        docker = ResourceDaemon("not-a-real-token")
        docker.create(kind, name, "--label", "studio.smoke.owner=this-attempt")
        return docker, state, name

    return make


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
def test_smoke_registered_cleanup_preserves_unowned_replacement(smoke_owned_resource, kind):
    docker, state, name = smoke_owned_resource(kind)
    replacement = {"name": "/" + name if kind == "container" else name,
                   "owner": "someone-else", "id": "replacement-id"}
    state["resource"] = replacement
    assert docker.cleanup() == [name], "replacement must be reported as unverified cleanup"
    assert state["removed"] == [], "registered names must not authorize deleting replacements"
    assert state["resource"] == replacement


@pytest.mark.parametrize("kind", ["container", "network"])
def test_smoke_registered_cleanup_removes_renamed_owned_identity(smoke_owned_resource, kind):
    docker, state, name = smoke_owned_resource(kind)
    state["resource"]["name"] = "/renamed-owned" if kind == "container" else "renamed-owned"
    assert docker.cleanup() == [], "legitimate rename must not prevent owned-ID cleanup"
    assert state["removed"] == ["original-id"]
    assert state["resource"] is None


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
def test_smoke_registered_cleanup_uses_verified_identity(smoke_owned_resource, kind):
    docker, state, name = smoke_owned_resource(kind)
    before = len(state["commands"])
    assert docker.cleanup() == []
    assert state["removed"] == [name if kind == "volume" else "original-id"]
    assert state["resource"] is None
    cleanup_commands = state["commands"][before:]
    removal = next(i for i, command in enumerate(cleanup_commands) if "rm" in command)
    assert "inspect" in cleanup_commands[removal - 1]
    assert "studio.smoke.owner" in cleanup_commands[removal - 1][-2]
    assert cleanup_commands[removal - 1][-1] == (name if kind == "volume" else "original-id")


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
@pytest.mark.parametrize("owner", ["someone-else", None, ""])
def test_smoke_registered_cleanup_rechecks_owner(smoke_owned_resource, kind, owner):
    docker, state, name = smoke_owned_resource(kind)
    state["resource"]["owner"] = owner
    assert docker.cleanup() == [name]
    assert state["removed"] == []
    assert state["resource"] is not None


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
@pytest.mark.parametrize("failure", ["inspect-error", "inspect-timeout", "list-error", "list-timeout"])
def test_smoke_registered_cleanup_fails_closed_on_daemon_uncertainty(
    smoke_owned_resource, monkeypatch, kind, failure,
):
    from types import SimpleNamespace

    docker, state, name = smoke_owned_resource(kind)
    run = docker.run

    def unavailable(*args, **kwargs):
        operation = "inspect" if "inspect" in args else "list" if "ls" in args else "rm"
        if failure == operation + "-timeout":
            raise state["failure_type"]("docker command unavailable or timed out")
        if failure == operation + "-error":
            return SimpleNamespace(returncode=1, stdout="", stderr="output withheld")
        return run(*args, **kwargs)

    monkeypatch.setattr(docker, "run", unavailable)
    assert docker.cleanup() == [name]
    if failure.startswith("inspect"):
        assert state["removed"] == []
        assert state["resource"] is not None


@pytest.mark.parametrize("kind", ["container", "network"])
def test_smoke_registered_cleanup_detects_unremoved_renamed_identity(smoke_owned_resource, monkeypatch, kind):
    from types import SimpleNamespace

    docker, state, name = smoke_owned_resource(kind)
    state["resource"]["name"] = "/renamed-owned" if kind == "container" else "renamed-owned"
    run = docker.run

    def failed_remove(*args, **kwargs):
        if "rm" in args:
            return SimpleNamespace(returncode=1, stdout="", stderr="output withheld")
        return run(*args, **kwargs)

    monkeypatch.setattr(docker, "run", failed_remove)
    assert docker.cleanup() == [name], "absence of the old name cannot prove ID removal"
    assert state["resource"] is not None


def test_smoke_cleanup_checks_stopped_containers_too():
    import json
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    root = Path(__file__).resolve().parents[1]
    namespace = runpy.run_path(str(root / "scripts/smoke_container.py"))

    class UnremovableStoppedContainer(namespace["Docker"]):
        def run(self, *args, **kwargs):
            if "inspect" in args:
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    "name": "/owned-stopped", "owner": "this-attempt", "id": "owned-stopped-id",
                }), stderr="")
            # Fault injection: rm fails; a stopped container appears only with --all.
            return SimpleNamespace(returncode=1 if "rm" in args else 0,
                                   stdout="owned-stopped-id owned-stopped\n" if "--all" in args else "", stderr="")

    docker = UnremovableStoppedContainer("not-a-real-token")
    docker.resources = [("container", "owned-stopped", "this-attempt", "owned-stopped-id")]
    assert docker.cleanup() == ["owned-stopped"]


def test_smoke_cleanup_continues_after_one_resource_timeout():
    import json
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    root = Path(__file__).resolve().parents[1]
    namespace = runpy.run_path(str(root / "scripts/smoke_container.py"))
    removed = []

    class CleanupTimeout(namespace["Docker"]):
        def run(self, *args, **kwargs):
            if "inspect" in args:
                name = args[-1].removesuffix("-id")
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    "name": "/" + name if args[0] == "container" else name,
                    "owner": "this-attempt", "id": args[-1],
                }), stderr="")
            if "rm" in args:
                removed.append(args[-1])
                if args[-1] == "owned-timeout-id":
                    raise namespace["SmokeFailure"]("docker command timed out")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    docker = CleanupTimeout("not-a-real-token")
    docker.resources = [("network", "owned-network", "this-attempt", "owned-network-id"),
                        ("container", "owned-timeout", "this-attempt", "owned-timeout-id")]
    try:
        remaining = docker.cleanup()
    except namespace["SmokeFailure"]:
        remaining = None
    assert remaining == ["owned-timeout"]
    assert removed == ["owned-timeout-id", "owned-network-id"]


def test_smoke_cleanup_reconciles_daemon_created_resource_after_client_timeout():
    import json
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/smoke_container.py"))
    for kind in ("container", "network", "volume"):
        state = {"present": False, "removed": []}
        name = f"owned-timeout-{kind}"

        class AmbiguousCreate(namespace["Docker"]):
            def run(self, *args, **kwargs):
                if "create" in args:
                    state["present"] = True  # Daemon committed; client lost the response.
                    raise namespace["SmokeFailure"]("docker command unavailable or timed out")
                if "inspect" in args:
                    assert args[-1] == name
                    return SimpleNamespace(returncode=0, stdout=json.dumps({
                        "name": "/" + name if kind == "container" else name,
                        "owner": "this-attempt", "id": "owned-id",
                    }), stderr="")
                if "rm" in args:
                    state["removed"].append(args[-1])
                    state["present"] = False
                return SimpleNamespace(returncode=0, stdout=name if state["present"] else "", stderr="")

        docker = AmbiguousCreate("not-a-real-token")
        with pytest.raises(namespace["SmokeFailure"], match="timed out"):
            docker.create(kind, name, "--label", "studio.smoke.owner=this-attempt")
        assert docker.cleanup() == []
        assert not state["present"], f"daemon-created {kind} leaked after client timeout"
        assert state["removed"] == [name if kind == "volume" else "owned-id"]


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
@pytest.mark.parametrize("identity", ["other-owner", "missing-owner", "wrong-name"])
def test_smoke_cleanup_leaves_unowned_name_collision_unremoved(kind, identity):
    import json
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/smoke_container.py"))
    name = "colliding-resource"
    removed = []

    class NameCollision(namespace["Docker"]):
        def run(self, *args, **kwargs):
            if "create" in args:
                raise namespace["SmokeFailure"]("docker create failed (output withheld)")
            if "rm" in args:
                removed.append(args[-1])
            resource = {"name": "/" + name if kind == "container" else name,
                        "owner": "this-attempt", "id": "unrelated-id"}
            if identity == "wrong-name":
                resource["name"] = "other-resource"
            else:
                resource["owner"] = "someone-else" if identity == "other-owner" else None
            return SimpleNamespace(returncode=0, stdout=json.dumps(resource), stderr="")

    docker = NameCollision("not-a-real-token")
    with pytest.raises(namespace["SmokeFailure"]):
        docker.create(kind, name, "--label", "studio.smoke.owner=this-attempt")
    remaining = docker.cleanup()
    assert removed == [], "cleanup must never delete an unrelated colliding resource"
    assert remaining == [name], "ownership mismatch must not be reported as clean"


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
@pytest.mark.parametrize("failure", ["inspect-timeout", "inspect-error", "list-timeout", "list-error"])
def test_smoke_cleanup_reports_inaccessible_pending_resource(kind, failure):
    import json
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/smoke_container.py"))
    name = "unverified-resource"
    removed = []

    class InaccessibleResource(namespace["Docker"]):
        def run(self, *args, **kwargs):
            if "create" in args:
                raise namespace["SmokeFailure"]("docker command unavailable or timed out")
            if "rm" in args:
                removed.append(args[-1])
            operation = "inspect" if "inspect" in args else "list" if "ls" in args else "rm"
            if failure == operation + "-timeout":
                raise namespace["SmokeFailure"]("docker command unavailable or timed out")
            if failure == operation + "-error" or failure == "inspect-error":
                # A daemon error affects both inspect and the absence fallback list.
                return SimpleNamespace(returncode=1, stdout="", stderr="output withheld")
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "name": "/" + name if kind == "container" else name,
                "owner": "this-attempt", "id": "owned-id",
            }) if operation == "inspect" else "", stderr="")

    docker = InaccessibleResource("not-a-real-token")
    with pytest.raises(namespace["SmokeFailure"]):
        docker.create(kind, name, "--label", "studio.smoke.owner=this-attempt")
    assert docker.cleanup() == [name], "inaccessible daemon must not establish ownership or absence"
    if failure.startswith("inspect"):
        assert removed == [], "never delete when ownership cannot be inspected"


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
def test_smoke_cleanup_verifies_truly_absent_pending_attempt(kind):
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/smoke_container.py"))
    name = "never-created"
    commands = []

    class AbsentResource(namespace["Docker"]):
        def run(self, *args, **kwargs):
            commands.append(args)
            if "create" in args:
                raise namespace["SmokeFailure"]("docker create failed (output withheld)")
            if "inspect" in args:
                return SimpleNamespace(returncode=1, stdout="", stderr="not found")
            # Include a similar unrelated name; absence comparison must be exact.
            return SimpleNamespace(returncode=0, stdout=name + "-unrelated\n", stderr="")

    docker = AbsentResource("not-a-real-token")
    with pytest.raises(namespace["SmokeFailure"]):
        docker.create(kind, name, "--label", "studio.smoke.owner=this-attempt")
    assert docker.cleanup() == [], "an accessible full list can prove exact-name absence"
    assert not any("rm" in command for command in commands)
    listings = [command for command in commands if "ls" in command]
    assert len(listings) == 1
    if kind == "container":
        assert "--all" in listings[0], "absence must include stopped containers"


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
@pytest.mark.parametrize("labels", [(), ("--label", "other.key=value"), ("--label", "studio.smoke.owner=")])
def test_smoke_create_requires_owner_label_before_any_docker_command(kind, labels):
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/smoke_container.py"))
    commands = []

    class NoCreate(namespace["Docker"]):
        def run(self, *args, **kwargs):
            commands.append(args)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    docker = NoCreate("not-a-real-token")
    with pytest.raises(namespace["SmokeFailure"], match="owner label"):
        docker.create(kind, "must-not-create", *labels)
    assert commands == []
    assert docker.cleanup() == []


def test_smoke_create_does_not_claim_existing_unowned_volume_on_success():
    import json
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/smoke_container.py"))
    removed = []

    class ExistingVolume(namespace["Docker"]):
        def run(self, *args, **kwargs):
            if "rm" in args:
                removed.append(args[-1])
            # Docker volume create is idempotent, even for another owner's existing name.
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "name": "existing-volume", "owner": "someone-else",
            }) if "inspect" in args else "existing-volume\n", stderr="")

    docker = ExistingVolume("not-a-real-token")
    with pytest.raises(namespace["SmokeFailure"], match="ownership"):
        docker.create("volume", "existing-volume", "--label", "studio.smoke.owner=this-attempt")
    assert docker.cleanup() == ["existing-volume"]
    assert removed == []


@pytest.mark.parametrize("payload", ["invalid-json", "null", "[]", '{"name":"/uncertain","owner":"this-attempt","id":""}'])
def test_smoke_cleanup_rejects_uncertain_inspect_identity(payload):
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/smoke_container.py"))
    removed = []

    class UncertainIdentity(namespace["Docker"]):
        def run(self, *args, **kwargs):
            if "create" in args:
                raise namespace["SmokeFailure"]("docker command unavailable or timed out")
            if "rm" in args:
                removed.append(args[-1])
            return SimpleNamespace(returncode=0, stdout=payload if "inspect" in args else "", stderr="")

    docker = UncertainIdentity("not-a-real-token")
    with pytest.raises(namespace["SmokeFailure"]):
        docker.create("container", "uncertain", "--label", "studio.smoke.owner=this-attempt")
    assert docker.cleanup() == ["uncertain"]
    assert removed == []
