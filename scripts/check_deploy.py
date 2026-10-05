"""Offline YAML/policy checks; this is NOT Docker/Compose runtime validation."""

import argparse
import json
import re
from pathlib import Path

import yaml


def validate_compose(document):
    errors = []
    for name, service in document.get("services", {}).items():
        image = service.get("image", "")
        if not service.get("build") and not re.search(r"@sha256:[0-9a-f]{64}$", image):
            errors.append(f"{name}: image must be pinned to a retrieved sha256 digest")
        if not service.get("profiles"):
            errors.append(f"{name}: an explicit opt-in profile is required")
        if service.get("privileged"):
            errors.append(f"{name}: privileged mode is forbidden")
        if service.get("network_mode") == "host":
            errors.append(f"{name}: host network is forbidden")
        for key, value in service.get("environment", {}).items():
            secret = re.search(
                r"(PASSWORD|SECRET|API_KEY|MASTER_KEY|BASIC_AUTH|REDIS_AUTH|REDISCLI_AUTH|SALT|"
                r"ENCRYPTION_KEY|ACCESS_KEY|SECRET_KEY|API_TOKEN)$", key
            )
            optional = key == "STUDIO_LITELLM_API_KEY" and value == "${STUDIO_LITELLM_API_KEY:-}"
            if secret and not optional and not re.search(r"\$\{[A-Z][A-Z0-9_]*:\?[^}]+\}", str(value)):
                errors.append(f"{name}.{key}: secrets need required interpolation, not defaults")
        for port in service.get("ports", []):
            host = port.get("host_ip") if isinstance(port, dict) else str(port).split(":")[0]
            if host != "127.0.0.1":
                errors.append(f"{name}: published ports must bind loopback 127.0.0.1")
    return errors


def validate_collector(document):
    errors = []
    allowed = {"receivers": {"otlp"}, "processors": {"batch", "memory_limiter"},
               "exporters": {"otlphttp"}, "extensions": set(), "connectors": set()}
    for section, types in allowed.items():
        for name in document.get(section, {}):
            if name.split("/")[0] not in types:
                errors.append(f"{section}.{name}: not an approved core component")
    destinations = {
        "otlphttp/phoenix": ("http://phoenix:6006/v1/traces", "Bearer ${env:PHOENIX_API_KEY}"),
        "otlphttp/langfuse": (
            "http://langfuse-web:3000/api/public/otel/v1/traces", "Basic ${env:LANGFUSE_BASIC_AUTH}"
        ),
    }
    for name, config in document.get("exporters", {}).items():
        if name not in destinations:
            errors.append(f"{name}: unknown destination endpoint")
            continue
        endpoint, authorization = destinations[name]
        if config.get("traces_endpoint") != endpoint:
            errors.append(f"{name}: incorrect OTLP traces endpoint")
        if config.get("headers", {}).get("Authorization") != authorization:
            errors.append(f"{name}: expected {authorization.split()[0]} authorization from environment")
        if name.endswith("langfuse") and config.get("headers", {}).get("x-langfuse-ingestion-version") != "4":
            errors.append("langfuse: v4 ingestion header required")
    pipelines = document.get("service", {}).get("pipelines", {})
    if set(pipelines) != {"traces"}:
        errors.append("Only an explicit traces pipeline is permitted")
    for pipeline in pipelines.values():
        for section in ("receivers", "processors", "exporters"):
            for name in pipeline.get(section, []):
                if name not in document.get(section, {}):
                    errors.append(f"Pipeline refers to undefined {section}.{name}")
    return errors


def validate_repository(root):
    errors = []
    services = {}
    required_environment = set()
    provenance = json.loads((root / "docs/upstreams.json").read_text(encoding="utf-8"))
    known_images = {item["reference"] for item in provenance["images"]}
    for path in sorted((root / "deploy").glob("compose*.yml")):
        content = path.read_text(encoding="utf-8")
        document = yaml.safe_load(content)
        errors.extend(f"{path.name}: {error}" for error in validate_compose(document))
        required_environment.update(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", content))
        services.update(document["services"])
        for name, service in document["services"].items():
            if service.get("image") and service["image"] not in known_images:
                errors.append(f"{name}: image has no retrieved provenance")
        if path.name == "compose.yml" and not document.get("networks", {}).get("default", {}).get("internal"):
            errors.append("Default network must be internal; only the explicit proxy has egress")
    expected = {"studio", "litellm", "collector", "phoenix", "langfuse-web", "langfuse-worker",
                "postgres", "clickhouse", "redis", "minio"}
    for missing in sorted(expected - services.keys()):
        errors.append(f"Missing required service: {missing}")
    infrastructure = {"postgres", "clickhouse", "redis", "minio"}
    for name in ("langfuse-web", "langfuse-worker"):
        depends = services.get(name, {}).get("depends_on", {})
        for dependency in infrastructure:
            if depends.get(dependency, {}).get("condition") != "service_healthy":
                errors.append(f"{name}: missing healthy dependency {dependency}")
    for name, service in services.items():
        for dependency, condition in service.get("depends_on", {}).items():
            if dependency not in services:
                errors.append(f"{name}: undefined dependency {dependency}")
            elif condition.get("condition") == "service_healthy" and not services[dependency].get("healthcheck"):
                errors.append(f"{dependency}: healthy dependency has no healthcheck")
    for path in sorted((root / "deploy").glob("otel-*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        errors.extend(f"{path.name}: {error}" for error in validate_collector(document))
    example = (root / ".env.example").read_text(encoding="utf-8")
    available_environment = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", example, re.MULTILINE))
    for missing in sorted(required_environment - available_environment):
        errors.append(f".env.example: missing mandatory variable {missing}")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    for marker in ("uv.lock", "--locked", "--no-dev", "USER 10001:10001", "HEALTHCHECK"):
        if marker not in dockerfile:
            errors.append(f"Dockerfile: missing {marker}")
    for image in re.findall(r"^FROM\s+(\S+)", dockerfile, re.MULTILINE):
        if image not in known_images:
            errors.append("Dockerfile base image has no retrieved provenance")
    if not (root / "uv.lock").is_file():
        errors.append("uv.lock is required to build")
    wanted = {
        "https://github.com/open-telemetry/opentelemetry-collector",
        "https://github.com/langfuse/langfuse", "https://github.com/Arize-ai/phoenix",
        "https://github.com/vibrantlabsai/ragas", "https://github.com/confident-ai/deepeval",
        "https://github.com/guardrails-ai/guardrails", "https://github.com/pydantic/pydantic-ai",
        "https://github.com/567-labs/instructor", "https://github.com/BerriAI/litellm",
        "https://github.com/deepset-ai/haystack",
    }
    upstreams = provenance["upstreams"]
    if len(upstreams) != 10 or {item["requested_repo_url"] for item in upstreams} != wanted:
        errors.append("Provenance must contain all 10 exact requested repositories once")
    for item in upstreams:
        for key in ("observed_release", "release_url", "readme_source_url", "license", "retrieved_at"):
            if not item.get(key):
                errors.append(f"{item.get('id')}: missing source field {key}")
        if item["id"] == "phoenix" and item["license"]["classification"] != "source-available":
            errors.append("Phoenix must be classified source-available, not OSI open source")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", type=Path)
    parser.add_argument("--collector", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        if args.collector:
            errors = validate_collector(yaml.safe_load(args.collector.read_text(encoding="utf-8")))
        elif args.compose:
            errors = validate_compose(yaml.safe_load(args.compose.read_text(encoding="utf-8")))
        else:
            errors = validate_repository(args.root)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as error:
        errors = [f"Invalid deployment document: {error}"]
    for error in errors:
        print(error)
    if not errors:
        print("Static deployment checks passed (runtime NOT tested).")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
