"""Vertical integration tests; no provider credentials or external services."""
import importlib
import importlib.metadata
import os
import unittest
from pathlib import Path
from unittest.mock import patch


class EngineTests(unittest.TestCase):
    def setUp(self):
        # Bootstrap opt-outs before SDK imports and before patch.dict snapshots.
        importlib.import_module("studio.engine")
        self.env = patch.dict(os.environ, {
            "STUDIO_ENABLE_LIVE": "false",
            "STUDIO_LITELLM_BASE_URL": "",
            "STUDIO_LITELLM_API_KEY": "",
            "STUDIO_MODEL": "",
            "STUDIO_OTLP_ENDPOINT": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        # A swallowed analytics error still fails the test via call-count checks.
        # Live tests override SDK send(), so no real transport is allowed there either.
        for target in ("requests.sessions.Session.request", "httpx.HTTPTransport.handle_request",
                       "httpx.AsyncHTTPTransport.handle_async_request"):
            guard = patch(target, side_effect=AssertionError("Unexpected network access"))
            mocked = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(mocked.assert_not_called)

    def test_catalog_reports_ten_installed_capabilities_not_connectivity(self):
        self.assertTrue(Path("studio/engine.py").is_file(), "engine module is missing")
        engine = importlib.import_module("studio.engine")
        catalog = engine.integration_catalog()
        self.assertEqual(len(catalog), 10)
        self.assertEqual(next(item for item in catalog if item['id'] == 'otel')['repo'],
                         'https://github.com/open-telemetry/opentelemetry-collector')
        self.assertEqual({item["id"] for item in catalog}, {
            "otel", "langfuse", "phoenix", "ragas", "deepeval", "guardrails",
            "pydantic-ai", "instructor", "litellm", "haystack",
        })
        for item in catalog:
            self.assertTrue({"id", "name", "role", "repo", "version", "status", "detail"} <= item.keys())
            self.assertIn(item["status"], {"available", "unavailable", "unconfigured"})
            self.assertNotIn("connected", item["detail"].lower())
        pydantic = next(item for item in catalog if item["id"] == "pydantic-ai")
        try:
            expected = importlib.metadata.version("pydantic-ai-slim")
        except importlib.metadata.PackageNotFoundError:
            expected = None
        self.assertEqual(pydantic["version"], expected)
        for item in catalog:
            if item["id"] in {"otel", "langfuse", "phoenix", "instructor", "litellm"}:
                self.assertIn(item["status"], {"unconfigured", "unavailable"})

    def test_local_run_retrieves_real_documents_and_exercises_typed_schema(self):
        engine = importlib.import_module("studio.engine")
        self.assertTrue(callable(getattr(engine, "execute_run", None)), "execute_run is missing")
        documents = [
            {"id": "doc-a", "title": "Apples", "content": "Apples are red fruit."},
            {"id": "doc-b", "title": "Saturn", "content": "Saturn has icy rings."},
        ]
        result = engine.execute_run("Saturn rings", documents, top_k=1)
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["output"]["citations"], [documents[1]])
        self.assertEqual(result["output"]["answer"], "Local deterministic fixture (not AI-generated):\nSaturn has icy rings.")
        self.assertEqual(result["output"]["confidence"], 0.0)
        self.assertTrue(result["guardrails"]["passed"])
        self.assertTrue({"haystack", "pydantic-ai", "guardrails"} <= {
            step["framework"] for step in result["steps"] if step["status"] == "passed"
        })
        for step in result["steps"]:
            self.assertGreaterEqual(step["duration_ms"], 0)
        self.assertIsNone(result["error"])

    def test_reference_metrics_execute_real_ragas_and_deepeval(self):
        from studio.engine import execute_run

        docs = [{"id": "s", "title": "Saturn", "content": "Saturn has icy rings."}]
        expected = "Local deterministic fixture (not AI-generated):\nSaturn has icy rings."
        for reference, expected_score in [(expected, 1.0), ("unrelated reference", 0.0)]:
            with self.subTest(reference=reference):
                result = execute_run("Saturn", docs, reference=reference)
                self.assertEqual(len(result["evaluations"]), 2, result)
                self.assertEqual({e["framework"] for e in result["evaluations"]}, {"ragas", "deepeval"})
                for metric in result["evaluations"]:
                    self.assertEqual(metric["score"], expected_score)
                    self.assertEqual(metric["threshold"], 1.0)
                    self.assertEqual(metric["passed"], expected_score == 1)
                self.assertEqual(result["status"], "passed" if expected_score else "blocked")

    def test_otel_local_spans_exclude_content_and_exception_messages(self):
        self.assertTrue(Path("studio/telemetry.py").is_file(), "telemetry module is missing")
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from studio import telemetry
        from studio.engine import execute_run

        exporter = InMemorySpanExporter()
        provider = telemetry.create_provider(exporter=exporter)
        self.addCleanup(provider.shutdown)
        with patch.object(telemetry, "get_provider", return_value=provider):
            result = execute_run("SECRET PROMPT", [{"id": "a", "title": "secret title", "content": "SECRET DATA"}])
            with self.assertRaises(ValueError):
                with telemetry.span("studio.failure", {"prompt": "SECRET PROMPT"}):
                    raise ValueError("SECRET ERROR")
        spans = exporter.get_finished_spans()
        self.assertTrue(spans)
        self.assertRegex(result["trace_id"], r"^[0-9a-f]{32}$")
        root = next(s for s in spans if s.name == "studio.run")
        self.assertEqual(f"{root.context.trace_id:032x}", result["trace_id"])
        self.assertEqual(root.attributes["studio.mode"], "local")
        self.assertEqual(root.attributes["studio.document_count"], 1)
        serialized = repr([(s.name, dict(s.attributes), s.events, s.status.description) for s in spans])
        self.assertNotIn("SECRET", serialized)
        self.assertNotIn("secret title", serialized)
        self.assertTrue({"studio.retrieve", "studio.generate", "studio.validate"} <= {s.name for s in spans})

    def test_otlp_exporter_is_only_enabled_by_explicit_studio_endpoint(self):
        from studio import telemetry

        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://ignored.invalid"}):
            provider = telemetry.create_provider()
            self.addCleanup(provider.shutdown)
            self.assertEqual(len(provider._active_span_processor._span_processors), 0)
        with patch.dict(
            os.environ,
            {
                "STUDIO_OTLP_ENDPOINT": "http://127.0.0.1:4318/v1/traces",
                "OTEL_SDK_DISABLED": "false",
            },
        ):
            provider = telemetry.create_provider()
            self.addCleanup(provider.shutdown)
            self.assertEqual(len(provider._active_span_processor._span_processors), 1)

    def test_disabled_otel_has_no_exporter_and_invalid_spans_have_no_trace_id(self):
        from opentelemetry.trace import INVALID_SPAN

        from studio import telemetry

        for disabled in ("1", "true", "TRUE ", "yes", "on"):
            with self.subTest(disabled=disabled), patch.dict(
                os.environ,
                {
                    "STUDIO_OTLP_ENDPOINT": "http://127.0.0.1:4318/v1/traces",
                    "OTEL_SDK_DISABLED": disabled,
                },
            ):
                self.assertFalse(telemetry.export_enabled())
                provider = telemetry.create_provider()
                self.addCleanup(provider.shutdown)
                self.assertEqual(len(provider._active_span_processor._span_processors), 0)
        with patch.dict(
            os.environ,
            {"STUDIO_OTLP_ENDPOINT": "", "OTEL_SDK_DISABLED": "false"},
        ):
            self.assertFalse(telemetry.export_enabled())
        self.assertIsNone(telemetry.trace_id(INVALID_SPAN))

    def test_live_needs_server_enablement_config_and_per_request_consent(self):
        from studio.engine import execute_run

        complete = {
            "STUDIO_ENABLE_LIVE": "true", "STUDIO_LITELLM_BASE_URL": "https://proxy.invalid/v1",
            "STUDIO_LITELLM_API_KEY": "test-secret", "STUDIO_MODEL": "configured-model",
        }
        for config, consent in [({}, True), (complete, False),
                                 ({**complete, "STUDIO_MODEL": ""}, True)]:
            with self.subTest(configured=bool(config), consent=consent):
                with patch.dict(os.environ, config):
                    result = execute_run("hello", [], mode="live", allow_paid=consent)
                self.assertEqual(result["status"], "blocked", result)
                self.assertIsNone(result["output"])
                self.assertTrue(result["error"])
                self.assertEqual(result["steps"][0]["name"], "preflight")

    def test_live_routes_use_real_sdks_with_transport_only_fixtures(self):
        import json

        import httpx

        from studio.engine import execute_run

        docs = [{"id": "a", "title": "Saturn", "content": "Saturn has rings."}]
        config = {
            "STUDIO_ENABLE_LIVE": "true", "STUDIO_LITELLM_BASE_URL": "https://proxy.invalid/v1",
            "STUDIO_LITELLM_API_KEY": "transport-test-key", "STUDIO_MODEL": "configured-model",
        }
        payload = {"answer": "Transport fixture, not provider evidence.", "citations": docs, "confidence": 0.2}
        for route in ("pydantic-ai", "instructor"):
            with self.subTest(route=route):
                seen = []
                def response(request):
                    body = json.loads(request.content)
                    seen.append((request, body))
                    name = body["tools"][0]["function"]["name"]
                    return httpx.Response(200, request=request, json={
                        "id": "transport-fixture", "object": "chat.completion", "created": 0,
                        "model": "configured-model", "choices": [{"index": 0, "finish_reason": "tool_calls",
                            "message": {"role": "assistant", "content": None, "tool_calls": [{
                                "id": "call-fixture", "type": "function", "function": {
                                    "name": name, "arguments": json.dumps(payload),
                                },
                            }]} }],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    })
                async def async_send(client, request, **kwargs):
                    return response(request)
                def sync_send(client, request, **kwargs):
                    return response(request)
                with patch.dict(os.environ, config), patch.object(httpx.AsyncClient, "send", async_send), \
                        patch.object(httpx.Client, "send", sync_send):
                    result = execute_run("Saturn", docs, mode="live", engine=route, allow_paid=True)
                self.assertEqual(len(seen), 1, result)
                self.assertEqual(result["status"], "passed", result)
                self.assertEqual(result["output"], payload)
                request, body = seen[0]
                self.assertEqual(str(request.url), "https://proxy.invalid/v1/chat/completions")
                self.assertEqual(body["model"], "configured-model")
                self.assertEqual(request.headers["authorization"], "Bearer transport-test-key")
                self.assertIn("Saturn has rings.", request.content.decode())
                self.assertEqual(next(s for s in result["steps"] if s["name"] == "generate")["framework"], route)

    def test_guardrails_rejects_schema_errors_and_forged_citations(self):
        from studio.engine import _validate_output

        doc = {"id": "a", "title": "Source", "content": "Original evidence."}
        for citation in [{**doc, "id": "not-retrieved"}, {**doc, "content": "Altered evidence."},
                         {**doc, "title": "Invented title"}]:
            with self.subTest(citation=citation):
                result = _validate_output({"answer": "answer", "citations": [citation], "confidence": 0.2}, [doc])
                self.assertFalse(result["passed"], result)
        self.assertFalse(_validate_output({"answer": "answer", "citations": [doc], "confidence": 2}, [doc])["passed"])
        self.assertFalse(_validate_output({"answer": "answer", "citations": [doc, doc], "confidence": 0}, [doc])["passed"])

    def test_live_provider_failure_is_redacted_and_never_falls_back(self):
        import httpx

        from studio.engine import execute_run

        async def broken_async(client, request, **kwargs):
            return httpx.Response(401, request=request, json={"error": {"message": "PRIVATE KEY AND PROMPT"}})
        def broken_sync(client, request, **kwargs):
            return httpx.Response(401, request=request, json={"error": {"message": "PRIVATE KEY AND PROMPT"}})
        config = {
            "STUDIO_ENABLE_LIVE": "true", "STUDIO_LITELLM_BASE_URL": "https://proxy.invalid/v1",
            "STUDIO_LITELLM_API_KEY": "private-key", "STUDIO_MODEL": "configured-model",
        }
        for route in ("pydantic-ai", "instructor"):
            with self.subTest(route=route), patch.dict(os.environ, config), \
                    patch.object(httpx.AsyncClient, "send", broken_async), \
                    patch.object(httpx.Client, "send", broken_sync):
                result = execute_run("PRIVATE PROMPT", [], mode="live", engine=route, allow_paid=True)
                self.assertEqual(result["status"], "failed", result)
                self.assertIsNone(result["output"])
                self.assertTrue(result["error"])
                self.assertNotIn("PRIVATE", str(result))
                self.assertNotIn("private-key", str(result))
                self.assertEqual(result["steps"][-1]["name"], "generate")
                self.assertEqual(result["steps"][-1]["status"], "failed")

    def test_no_reference_reports_skipped_metrics_not_invented_scores(self):
        from studio.engine import execute_run

        result = execute_run("Saturn", [{"id": "s", "title": "Saturn", "content": "Saturn has rings."}])
        self.assertEqual(len(result["evaluations"]), 2)
        for metric in result["evaluations"]:
            self.assertIsNone(metric["score"])
            self.assertIsNone(metric["passed"])
            self.assertIn("reference", metric["detail"].lower())
        self.assertEqual({s["framework"] for s in result["steps"] if s["status"] == "skipped"}, {"ragas", "deepeval"})

    def test_invalid_inputs_and_unsupported_modes_fail_closed(self):
        from studio.engine import execute_run

        doc = {"id": "a", "title": "Source", "content": "Evidence."}
        for kwargs in [{"mode": "INVALID-SECRET"}, {"engine": "INVALID-SECRET"},
                       {"mode": "local", "engine": "instructor"}, {"top_k": 0}, {"top_k": 11},
                       {"top_k": True}, {"prompt": " "}, {"prompt": "x" * 8001},
                       {"reference": "x" * 8001}, {"documents": [doc, doc]}]:
            with self.subTest(case=list(kwargs)):
                arguments = {"prompt": "Evidence", "documents": [doc], **kwargs}
                result = execute_run(**arguments)
                self.assertEqual(result["status"], "blocked", result)
                self.assertIsNone(result["output"])
                self.assertNotIn("INVALID-SECRET", str(result))
                self.assertEqual(result["steps"][0]["name"], "preflight")

    def test_live_rejects_unsafe_proxy_url_before_network(self):
        import httpx

        from studio.engine import execute_run

        for url in ("file:///tmp/private", "https://user:secret@proxy.invalid/v1", "https://proxy.invalid/v1?key=secret",
                    "https://proxy.invalid/v1#secret", "not-a-url"):
            with self.subTest(url=url), patch.dict(os.environ, {
                "STUDIO_ENABLE_LIVE": "true", "STUDIO_LITELLM_BASE_URL": url,
                "STUDIO_LITELLM_API_KEY": "key", "STUDIO_MODEL": "configured-model",
            }), patch.object(httpx.AsyncClient, "send", side_effect=AssertionError("Network forbidden")) as send:
                result = execute_run("test", [], mode="live", allow_paid=True)
                self.assertEqual(result["status"], "blocked", result)
                self.assertNotIn("secret", str(result))
                send.assert_not_called()

    def test_guardrails_never_temporarily_enables_usage_metrics(self):
        from guardrails import Guard

        from studio.engine import _validate_output

        original = Guard.configure
        configurations = []
        def record(guard, **kwargs):
            configurations.append(kwargs.get("allow_metrics_collection"))
            return original(guard, **kwargs)
        with patch.object(Guard, "configure", record):
            result = _validate_output({"answer": "fixture", "citations": [], "confidence": 0}, [])
        self.assertTrue(result["passed"])
        self.assertTrue(configurations)
        self.assertTrue(all(value is False for value in configurations), configurations)

    def test_large_retrieved_context_is_blocked_before_generation(self):
        from studio.engine import execute_run

        result = execute_run("Saturn", [{"id": "big", "title": "Big", "content": "Saturn " * 4000}])
        self.assertEqual(result["status"], "blocked", result)
        self.assertIsNone(result["output"])
        self.assertIn("context", result["error"].lower())
        self.assertNotIn("generate", [s["name"] for s in result["steps"]])

    def test_guardrails_does_not_accept_payload_by_silently_pruning_fields(self):
        from studio.engine import _validate_output

        payload = {"answer": "fixture", "citations": [], "confidence": 0, "unexpected": "private"}
        result = _validate_output(payload, [])
        self.assertFalse(result["passed"], result)
        self.assertNotIn("private", str(result))


if __name__ == "__main__":
    unittest.main()
