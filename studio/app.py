import os
import threading
import time
from collections import deque
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from studio.models import DocumentInput, ProjectInput, RunInput
from studio.security import SecurityMiddleware
from studio.store import Store, timestamp
from studio.telemetry import export_enabled


def create_app(db_path=None, runner=None):
    token = os.getenv("STUDIO_API_TOKEN", "")
    if os.getenv("STUDIO_ENV", "local") == "production" and len(token) < 32:
        raise ValueError("Production requires STUDIO_API_TOKEN with at least 32 characters.")
    app = FastAPI(title="AI Production Studio", version="0.1.0", docs_url=None, redoc_url=None)
    app.add_middleware(SecurityMiddleware, token=token)
    hosts = ["localhost", "127.0.0.1", "[::1]", "testserver"]
    hosts.extend(h.strip() for h in os.getenv("STUDIO_ALLOWED_HOSTS", "").split(",") if h.strip())
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    store = Store(db_path or os.getenv("STUDIO_DB_PATH", "data/studio.db"))
    app.state.store = store
    app.state.run_slots = threading.BoundedSemaphore(2)
    run_times = deque()
    rate_lock = threading.Lock()

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "mode": "live-enabled" if settings()["live_enabled"] else "local",
            "version": "0.1.0",
        }

    @app.get("/api/settings")
    def settings():
        return {
            "live_enabled": os.getenv("STUDIO_ENABLE_LIVE", "false").lower() == "true",
            "model": os.getenv("STUDIO_MODEL", "studio-default"),
            "telemetry_enabled": export_enabled(),
            "authentication_required": bool(token),
        }

    @app.get("/api/projects")
    def projects():
        return {"items": store.projects()}

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectInput):
        return store.create_project(body.name, body.description)

    def require_project(project_id):
        if not store.project_exists(project_id):
            raise HTTPException(404, "Workspace not found.")

    @app.get("/api/projects/{project_id}/documents")
    def documents(project_id: str):
        require_project(project_id)
        return {"items": store.documents(project_id)}

    @app.post("/api/projects/{project_id}/documents", status_code=201)
    def create_document(project_id: str, body: DocumentInput):
        require_project(project_id)
        try:
            return store.create_document(project_id, body.title, body.content)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.delete("/api/projects/{project_id}/documents/{document_id}", status_code=204)
    def delete_document(project_id: str, document_id: str):
        require_project(project_id)
        if not store.delete_document(project_id, document_id):
            raise HTTPException(404, "Document not found.")
        return Response(status_code=204)

    @app.get("/api/projects/{project_id}/runs")
    def runs(project_id: str):
        require_project(project_id)
        return {"items": store.runs(project_id)}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        item = store.run(run_id)
        if item is None:
            raise HTTPException(404, "Run not found.")
        return item

    @app.get("/api/runs/{run_id}/export")
    def export_run(run_id: str):
        item = get_run(run_id)
        return JSONResponse(
            item, headers={"Content-Disposition": f'attachment; filename="run-{item["id"]}.json"'}
        )

    @app.post("/api/projects/{project_id}/runs", status_code=201)
    def create_run(project_id: str, body: RunInput):
        require_project(project_id)
        if body.mode == "live" and (not settings()["live_enabled"] or not body.allow_paid):
            raise HTTPException(
                403, "Live calls require STUDIO_ENABLE_LIVE=true and explicit paid-call consent."
            )
        if not app.state.run_slots.acquire(blocking=False):
            raise HTTPException(429, "Two runs are already active. Retry after they finish.")
        try:
            with rate_lock:
                now = time.monotonic()
                while run_times and run_times[0] < now - 60:
                    run_times.popleft()
                if len(run_times) >= 30:
                    raise HTTPException(429, "Run limit reached: 30 per minute for this studio.")
                run_times.append(now)
            executor = runner
            if executor is None:
                from studio.engine import execute_run

                executor = execute_run
            started = time.perf_counter()
            try:
                result = executor(
                    body.prompt,
                    store.documents(project_id),
                    mode=body.mode,
                    engine=body.engine,
                    top_k=body.top_k,
                    reference=body.reference,
                    allow_paid=body.allow_paid,
                )
            except Exception:
                # Exception text can contain provider credentials or submitted content.
                result = {
                    "status": "failed",
                    "output": None,
                    "evaluations": [],
                    "guardrails": {"passed": False, "detail": "Execution failed."},
                    "steps": [],
                    "trace_id": None,
                    "error": "Execution failed. Check server dependency/configuration diagnostics.",
                }
            item = {
                **result,
                "id": uuid4().hex,
                "project_id": project_id,
                "prompt": body.prompt,
                "mode": body.mode,
                "engine": body.engine,
                "created_at": timestamp(),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            return store.save_run(item)
        finally:
            app.state.run_slots.release()

    @app.get("/api/summary")
    def summary():
        return store.summary()

    @app.get("/api/integrations")
    def integrations():
        from studio.engine import integration_catalog

        items = integration_catalog()
        return {"items": items, "total": len(items)}

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static, check_dir=False), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app


app = create_app()
