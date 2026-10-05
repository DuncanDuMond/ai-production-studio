import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL)"
            )

            db.execute(
                "CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), title TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS documents_project ON documents(project_id)")

            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), status TEXT NOT NULL, created_at TEXT NOT NULL, latency_ms REAL NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS runs_project ON runs(project_id,created_at)")

    def save_run(self, item):
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?)",
                (
                    item["id"],
                    item["project_id"],
                    item["status"],
                    item["created_at"],
                    item["latency_ms"],
                    json.dumps(item, allow_nan=False),
                ),
            )
        return item

    def runs(self, project_id):
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM runs WHERE project_id=? ORDER BY created_at DESC LIMIT 200",
                    (project_id,),
                )
            ]

    def run(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def project_exists(self, project_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is not None

    def documents(self, project_id):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM documents WHERE project_id=? ORDER BY created_at", (project_id,)
                )
            ]

    def create_document(self, project_id, title, content):
        item = {
            "id": uuid4().hex,
            "project_id": project_id,
            "title": title,
            "content": content,
            "created_at": timestamp(),
        }
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute("SELECT count(*) FROM documents WHERE project_id=?", (project_id,)).fetchone()[0]
                >= 100
            ):
                raise ValueError("Workspace document limit reached (100).")
            db.execute("INSERT INTO documents VALUES (:id,:project_id,:title,:content,:created_at)", item)
        return item

    def delete_document(self, project_id, document_id):
        with self.connect() as db:
            return (
                db.execute(
                    "DELETE FROM documents WHERE project_id=? AND id=?", (project_id, document_id)
                ).rowcount
                > 0
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def projects(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT p.*, (SELECT count(*) FROM documents d WHERE d.project_id=p.id) AS document_count, (SELECT count(*) FROM runs r WHERE r.project_id=p.id) AS run_count FROM projects p ORDER BY created_at DESC"
                )
            ]

    def create_project(self, name, description):
        item = {"id": uuid4().hex, "name": name, "description": description, "created_at": timestamp()}
        with self.connect() as db:
            db.execute("INSERT INTO projects VALUES (:id,:name,:description,:created_at)", item)
        return item | {"document_count": 0, "run_count": 0}

    def summary(self):
        with self.connect() as db:
            projects = db.execute("SELECT count(*) FROM projects").fetchone()[0]
            documents = db.execute("SELECT count(*) FROM documents").fetchone()[0]
            row = db.execute(
                "SELECT count(*) AS runs, coalesce(sum(status='passed'),0) AS passed_runs, coalesce(sum(status='blocked'),0) AS blocked_runs, avg(latency_ms) AS average_latency_ms FROM runs"
            ).fetchone()
        return {"projects": projects, "documents": documents, **dict(row)}
