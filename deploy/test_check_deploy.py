"""Offline regression tests for deployment policy validation."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeploymentPolicyTests(unittest.TestCase):
    def check_document(self, document):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(ROOT / "scripts/check_deploy.py"), "--compose", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_requires_explicit_safe_service_configuration(self):
        safe = {
            "image": "example/image:1.2.3@sha256:" + "a" * 64,
            "profiles": ["optional"],
            "ports": ["127.0.0.1:1234:1234"],
        }
        for change, expected in [
            ({"image": "example/image:latest"}, "digest"),
            ({"profiles": []}, "profile"),
            ({"privileged": True}, "privileged"),
            ({"network_mode": "host"}, "host network"),
            ({"environment": {"POSTGRES_PASSWORD": "postgres"}}, "required interpolation"),
            ({"environment": {"NEXTAUTH_SECRET": "${SECRET:-changeme}"}}, "required interpolation"),
            ({"environment": {"STUDIO_API_TOKEN": "public"}}, "required interpolation"),
            ({"environment": {"ENCRYPTION_KEY": "0" * 64}}, "required interpolation"),
            ({"environment": {"LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY": "public"}}, "required interpolation"),
        ]:
            with self.subTest(change=change):
                result = self.check_document({"services": {"test": safe | change}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stdout + result.stderr)
        result = self.check_document({"services": {"test": safe}})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def check_collector(self, document):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collector.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(ROOT / "scripts/check_deploy.py"), "--collector", str(path)],
                capture_output=True, text=True, check=False,
            )

    def test_core_collector_routes_require_exact_paths_and_auth(self):
        good = {
            "receivers": {"otlp": {"protocols": {"http": {"endpoint": "0.0.0.0:4318"}}}},
            "processors": {"memory_limiter": {}, "batch": {}},
            "exporters": {"otlphttp/langfuse": {
                "traces_endpoint": "http://langfuse-web:3000/api/public/otel/v1/traces",
                "headers": {
                    "Authorization": "Basic ${env:LANGFUSE_BASIC_AUTH}",
                    "x-langfuse-ingestion-version": "4",
                },
            }},
            "service": {"pipelines": {"traces": {
                "receivers": ["otlp"], "processors": ["memory_limiter", "batch"],
                "exporters": ["otlphttp/langfuse"],
            }}},
        }
        result = self.check_collector(good)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for change, expected in [
            ({"extensions": {"basicauth/client": {}}}, "core"),
            ({"exporters": {"otlphttp/langfuse": {"endpoint": "http://langfuse-web:3000"}}}, "endpoint"),
            ({"exporters": {"otlphttp/langfuse": {
                "traces_endpoint": "http://langfuse-web:3000/api/public/otel/v1/traces",
            }}}, "Basic"),
        ]:
            with self.subTest(change=change):
                result = self.check_collector(good | change)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stdout + result.stderr)

    def test_repository_and_broken_langfuse_stack(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_deploy.py")],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("runtime NOT tested", result.stdout)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            shutil.copytree(ROOT / "deploy", target / "deploy", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "docs", target / "docs")
            for name in ("Dockerfile", ".env.example", "uv.lock"):
                shutil.copy(ROOT / name, target / name)
            import yaml

            path = target / "deploy/compose.langfuse.yml"
            document = yaml.safe_load(path.read_text())
            del document["services"]["langfuse-worker"]
            path.write_text(yaml.safe_dump(document), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/check_deploy.py"), "--root", str(target)],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("langfuse-worker", result.stdout + result.stderr)

    def test_rejects_public_port(self):
        result = self.check_document({"services": {"unsafe": {"ports": ["8765:8765"]}}})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("loopback", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
