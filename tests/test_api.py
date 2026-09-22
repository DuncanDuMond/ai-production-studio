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
