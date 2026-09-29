import itertools
import os

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("MINIO_ROOT_USER", "test")
os.environ.setdefault("MINIO_ROOT_PASSWORD", "test")

import pytest
from fastapi.testclient import TestClient

import app as app_module


class FakeCursor:
    def __init__(self, handler):
        self._handler = handler
        self._rows = []

    def execute(self, sql, params=None):
        self._rows = self._handler(" ".join(sql.split()), tuple(params) if params else ())

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, handler):
        self._handler = handler

    def cursor(self, cursor_factory=None):
        return FakeCursor(self._handler)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeProjectsDB:
    """Emula as tabelas do projects-service, todas escopadas por company_id —
    o isolamento entre empresas é reforçado em cada query (ver test_isolation.py)."""

    def __init__(self):
        self.projects = {}
        self.tasks = {}
        self.tags = {}
        self.task_tags = set()
        self.task_comments = {}
        self.task_attachments = {}
        self.usuarios = {"user-1": {"nome": "Utilizador Teste"}}
        self.blocklist = {}
        self._seq = {"projects": itertools.count(1), "tasks": itertools.count(1),
                     "tags": itertools.count(1), "task_comments": itertools.count(1),
                     "task_attachments": itertools.count(1)}

    def connection_factory(self):
        return FakeConnection(self.handle)

    def handle(self, sql, p):
        if sql.startswith("SELECT jti FROM jwt_blocklist"):
            jti, = p
            return [{"jti": jti}] if jti in self.blocklist else []

        # --- projects ---
        if sql.startswith("SELECT * FROM projects WHERE company_id = %s ORDER BY created_at DESC"):
            company_id, = p
            rows = [r for r in self.projects.values() if r["company_id"] == company_id]
            return sorted(rows, key=lambda r: -r["id"])

        if sql.startswith("INSERT INTO projects"):
            name, description, color, status, due_date, company_id = p
            pid = next(self._seq["projects"])
            row = {"id": pid, "name": name, "description": description, "color": color,
                   "status": status, "due_date": due_date, "created_at": pid, "company_id": company_id}
            self.projects[pid] = row
            return [row]

        if sql.startswith("SELECT * FROM projects WHERE id = %s AND company_id = %s"):
            pid, company_id = p
            row = self.projects.get(pid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT 1 FROM projects WHERE id = %s AND company_id = %s"):
            pid, company_id = p
            row = self.projects.get(pid)
            return [{"1": 1}] if row and row["company_id"] == company_id else []

        if sql.startswith("UPDATE projects SET name=%s"):
            name, description, color, status, due_date, pid, company_id = p
            row = self.projects.get(pid)
            if not row or row["company_id"] != company_id:
                return []
            row.update({"name": name, "description": description, "color": color,
                        "status": status, "due_date": due_date})
            return [row]

        if sql.startswith("DELETE FROM projects WHERE id = %s AND company_id = %s"):
            pid, company_id = p
            row = self.projects.get(pid)
            if row and row["company_id"] == company_id:
                self.projects.pop(pid)
            return []

        # --- tasks ---
        if sql.startswith("SELECT * FROM tasks WHERE project_id = %s AND company_id = %s"):
            pid, company_id = p
            rows = [t for t in self.tasks.values() if t["project_id"] == pid and t["company_id"] == company_id]
            return sorted(rows, key=lambda r: (r["position"], -r["id"]))

        if sql.startswith("SELECT * FROM tasks WHERE company_id = %s ORDER BY position"):
            company_id, = p
            rows = [t for t in self.tasks.values() if t["company_id"] == company_id]
            return sorted(rows, key=lambda r: (r["position"], -r["id"]))

        if sql.startswith("SELECT tt.task_id, t.id, t.name, t.color FROM task_tags tt JOIN tags t ON t.id = tt.tag_id WHERE t.company_id = %s"):
            company_id, = p
            return [dict(self.tags[tid], task_id=task_id) for (task_id, tid) in self.task_tags
                    if self.tags[tid]["company_id"] == company_id]

        if sql.startswith("SELECT t.id, t.name, t.color FROM task_tags tt JOIN tags t ON t.id = tt.tag_id WHERE tt.task_id = %s"):
            task_id, = p
            return [dict(self.tags[tid]) for (t, tid) in self.task_tags if t == task_id]

        if sql.startswith("SELECT 1 FROM tasks WHERE id = %s AND company_id = %s"):
            tid, company_id = p
            row = self.tasks.get(tid)
            return [{"1": 1}] if row and row["company_id"] == company_id else []

        if sql.startswith("INSERT INTO tasks"):
            project_id, title, description, status, priority, assignee_id, due_date, company_id = p
            tid = next(self._seq["tasks"])
            row = {"id": tid, "project_id": project_id, "title": title, "description": description,
                   "status": status, "priority": priority, "assignee_id": assignee_id,
                   "due_date": due_date, "position": 0, "completed_at": None, "created_at": tid,
                   "company_id": company_id}
            self.tasks[tid] = row
            return [row]

        if sql.startswith("SELECT * FROM tasks WHERE id = %s AND company_id = %s"):
            tid, company_id = p
            row = self.tasks.get(tid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("UPDATE tasks SET project_id=%s, title=%s, description=%s, status=%s,"):
            project_id, title, description, status, priority, assignee_id, due_date, tid, company_id = p
            row = self.tasks.get(tid)
            if not row or row["company_id"] != company_id:
                return []
            row.update({"project_id": project_id, "title": title, "description": description,
                        "status": status, "priority": priority, "assignee_id": assignee_id, "due_date": due_date})
            row["completed_at"] = "now" if status == "done" else None
            return [row]

        if sql.startswith("UPDATE tasks SET status=%s, position=%s"):
            status, position, tid, company_id = p
            row = self.tasks.get(tid)
            if not row or row["company_id"] != company_id:
                return []
            row["status"] = status
            row["position"] = position
            row["completed_at"] = "now" if status == "done" else None
            return [row]

        if sql.startswith("UPDATE tasks SET status=%s, completed_at="):
            status, tid, company_id = p
            row = self.tasks.get(tid)
            if not row or row["company_id"] != company_id:
                return []
            row["status"] = status
            row["completed_at"] = "now" if status == "done" else None
            return [row]

        if sql.startswith("DELETE FROM tasks WHERE id = %s AND company_id = %s"):
            tid, company_id = p
            row = self.tasks.get(tid)
            if row and row["company_id"] == company_id:
                self.tasks.pop(tid)
            return []

        # --- tags ---
        if sql.startswith("SELECT * FROM tags WHERE project_id = %s AND company_id = %s"):
            pid, company_id = p
            rows = [t for t in self.tags.values() if t["project_id"] == pid and t["company_id"] == company_id]
            return sorted(rows, key=lambda r: r["name"])

        if sql.startswith("SELECT 1 FROM tags WHERE id = %s AND company_id = %s"):
            tid, company_id = p
            row = self.tags.get(tid)
            return [{"1": 1}] if row and row["company_id"] == company_id else []

        if sql.startswith("INSERT INTO tags"):
            project_id, name, color, company_id = p
            tid = next(self._seq["tags"])
            row = {"id": tid, "project_id": project_id, "name": name, "color": color, "company_id": company_id}
            self.tags[tid] = row
            return [row]

        if sql.startswith("DELETE FROM tags WHERE id = %s AND company_id = %s"):
            tid, company_id = p
            row = self.tags.get(tid)
            if row and row["company_id"] == company_id:
                self.tags.pop(tid)
            return []

        if sql.startswith("DELETE FROM task_tags WHERE task_id = %s"):
            task_id, = p
            self.task_tags = {k for k in self.task_tags if k[0] != task_id}
            return []

        if sql.startswith("INSERT INTO task_tags"):
            task_id, tag_id = p
            self.task_tags.add((task_id, tag_id))
            return []

        # --- comments ---
        if sql.startswith("SELECT * FROM task_comments WHERE task_id = %s"):
            task_id, = p
            rows = [c for c in self.task_comments.values() if c["task_id"] == task_id]
            return sorted(rows, key=lambda r: r["id"])

        if sql.startswith("SELECT nome FROM usuarios WHERE id = %s"):
            uid, = p
            u = self.usuarios.get(uid)
            return [{"nome": u["nome"]}] if u else []

        if sql.startswith("INSERT INTO task_comments"):
            task_id, author_id, author_name, body, company_id = p
            cid = next(self._seq["task_comments"])
            row = {"id": cid, "task_id": task_id, "author_id": author_id,
                   "author_name": author_name, "body": body, "created_at": cid, "company_id": company_id}
            self.task_comments[cid] = row
            return [row]

        # --- attachments ---
        if sql.startswith("INSERT INTO task_attachments"):
            task_id, filename, object_name, content_type, company_id = p
            aid = next(self._seq["task_attachments"])
            row = {"id": aid, "task_id": task_id, "filename": filename,
                   "object_name": object_name, "content_type": content_type, "created_at": aid,
                   "company_id": company_id}
            self.task_attachments[aid] = row
            return [row]

        if sql.startswith("SELECT * FROM task_attachments WHERE task_id = %s"):
            task_id, = p
            rows = [a for a in self.task_attachments.values() if a["task_id"] == task_id]
            return sorted(rows, key=lambda r: -r["id"])

        if sql.startswith("SELECT object_name FROM task_attachments WHERE id = %s AND company_id = %s"):
            aid, company_id = p
            a = self.task_attachments.get(aid)
            return [{"object_name": a["object_name"]}] if a and a["company_id"] == company_id else []

        if sql.startswith("DELETE FROM task_attachments WHERE id = %s AND company_id = %s"):
            aid, company_id = p
            row = self.task_attachments.get(aid)
            if row and row["company_id"] == company_id:
                self.task_attachments.pop(aid)
            return []

        raise AssertionError(f"SQL não reconhecido pelo FakeProjectsDB: {sql!r} params={p!r}")


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeProjectsDB()
    monkeypatch.setattr(app_module, "get_connection", db.connection_factory)
    monkeypatch.setattr(app_module, "upload_bytes", lambda data, object_name, content_type: None)
    monkeypatch.setattr(app_module, "delete_object", lambda object_name: None)
    monkeypatch.setattr(app_module, "get_presigned_url", lambda object_name: f"https://minio.local/{object_name}")
    return db


@pytest.fixture
def client(fake_db):
    return TestClient(app_module.app)
