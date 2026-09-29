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


class FakeDocumentsDB:
    """Emula folders/documents/etiquetas/workflow do documents-service, todos
    escopados por company_id (ver test_isolation.py). require_permission é
    contornado via monkeypatch de resolve_folder_permission — a lógica de
    herança de permissões tem testes unitários dedicados em test_permissions.py,
    que chamam core.db.resolve_folder_permission directamente. ensure_top_level_folders
    e ensure_default_tags NÃO são mockadas: correm a sério contra este fake, tal
    como fariam contra Postgres, criando a árvore de pastas/etiquetas por omissão
    na primeira vez que cada empresa acede a /folders ou /tags."""

    def __init__(self):
        self.folders = {}
        self.documents = {}
        self.document_tags = set()
        self.doc_tags = {}
        self.workflow_log = []
        self.notifications = []
        self.doc_templates = {}
        self.document_permissions = {}
        self.usuarios = {"user-1": {"nome": "Utilizador Teste"}}
        self.blocklist = {}
        self._seq = {"folders": itertools.count(1), "documents": itertools.count(1),
                     "doc_tags": itertools.count(1), "notifications": itertools.count(1),
                     "templates": itertools.count(1), "permissions": itertools.count(1)}

    def connection_factory(self):
        return FakeConnection(self.handle)

    def handle(self, sql, p):
        if sql.startswith("SELECT jti FROM jwt_blocklist"):
            jti, = p
            return [{"jti": jti}] if jti in self.blocklist else []

        # --- folders ---
        if sql.startswith("SELECT * FROM folders WHERE company_id = %s ORDER BY is_system DESC, name"):
            company_id, = p
            rows = [f for f in self.folders.values() if f["company_id"] == company_id]
            return sorted(rows, key=lambda f: (not f["is_system"], f["name"]))

        # get_or_create_top_folder (usada por ensure_top_level_folders/ensure_employee_folder/ensure_department_folder)
        if sql.startswith("SELECT id FROM folders WHERE name = %s AND parent_id IS NULL AND is_system = true AND company_id = %s"):
            name, company_id = p
            for f in self.folders.values():
                if f["name"] == name and f["parent_id"] is None and f["is_system"] and f["company_id"] == company_id:
                    return [{"id": f["id"]}]
            return []

        if sql.startswith("INSERT INTO folders (name, parent_id, kind, is_system, company_id) VALUES"):
            name, company_id = p
            fid = next(self._seq["folders"])
            self.folders[fid] = {
                "id": fid, "name": name, "parent_id": None, "kind": "system", "employee_id": None,
                "department_id": None, "is_system": True, "company_id": company_id,
            }
            return [{"id": fid}]

        if sql.startswith("INSERT INTO folders (name, parent_id, kind, company_id) VALUES"):
            name, parent_id, company_id = p
            fid = next(self._seq["folders"])
            row = {"id": fid, "name": name, "parent_id": parent_id, "kind": "custom",
                   "employee_id": None, "department_id": None, "is_system": False, "company_id": company_id}
            self.folders[fid] = row
            return [row]

        if sql.startswith("INSERT INTO folders (name, parent_id, kind, employee_id, company_id) VALUES"):
            name, parent_id, employee_id, company_id = p
            fid = next(self._seq["folders"])
            row = {"id": fid, "name": name, "parent_id": parent_id, "kind": "employee",
                   "employee_id": employee_id, "department_id": None, "is_system": False, "company_id": company_id}
            self.folders[fid] = row
            return [row]

        if sql.startswith("INSERT INTO folders (name, parent_id, kind, department_id, company_id) VALUES"):
            name, parent_id, department_id, company_id = p
            fid = next(self._seq["folders"])
            row = {"id": fid, "name": name, "parent_id": parent_id, "kind": "department",
                   "employee_id": None, "department_id": department_id, "is_system": False, "company_id": company_id}
            self.folders[fid] = row
            return [row]

        if sql.startswith("INSERT INTO folders (name, parent_id, kind, company_id) VALUES (%s, %s, 'employee_sub', %s)") \
                or sql.startswith("INSERT INTO folders (name, parent_id, kind, company_id) VALUES (%s, %s, 'dept_category', %s)") \
                or sql.startswith("INSERT INTO folders (name, parent_id, kind, company_id) VALUES (%s, %s, 'dept_subcategory', %s)"):
            name, parent_id, company_id = p
            fid = next(self._seq["folders"])
            kind = "employee_sub" if "employee_sub" in sql else ("dept_category" if "dept_category" in sql else "dept_subcategory")
            row = {"id": fid, "name": name, "parent_id": parent_id, "kind": kind,
                   "employee_id": None, "department_id": None, "is_system": False, "company_id": company_id}
            self.folders[fid] = row
            return [{"id": fid}] if "RETURNING id" in sql else []

        if sql.startswith("SELECT * FROM folders WHERE kind = 'employee' AND employee_id = %s AND company_id = %s"):
            employee_id, company_id = p
            for f in self.folders.values():
                if f["kind"] == "employee" and f["employee_id"] == employee_id and f["company_id"] == company_id:
                    return [f]
            return []

        if sql.startswith("SELECT * FROM folders WHERE kind = 'department' AND department_id = %s AND company_id = %s"):
            department_id, company_id = p
            for f in self.folders.values():
                if f["kind"] == "department" and f["department_id"] == department_id and f["company_id"] == company_id:
                    return [f]
            return []

        if sql.startswith("SELECT name FROM folders WHERE parent_id = %s AND kind = 'employee_sub'"):
            parent_id, = p
            return [{"name": f["name"]} for f in self.folders.values()
                    if f["parent_id"] == parent_id and f["kind"] == "employee_sub"]

        if sql.startswith("SELECT id, name FROM folders WHERE parent_id = %s AND kind = 'dept_category'"):
            parent_id, = p
            return [{"id": f["id"], "name": f["name"]} for f in self.folders.values()
                    if f["parent_id"] == parent_id and f["kind"] == "dept_category"]

        if sql.startswith("SELECT name FROM folders WHERE parent_id = %s AND kind = 'dept_subcategory'"):
            parent_id, = p
            return [{"name": f["name"]} for f in self.folders.values()
                    if f["parent_id"] == parent_id and f["kind"] == "dept_subcategory"]

        if sql.startswith("SELECT * FROM folders WHERE parent_id = %s ORDER BY name"):
            parent_id, = p
            rows = [f for f in self.folders.values() if f["parent_id"] == parent_id]
            return sorted(rows, key=lambda f: f["name"])

        if sql.startswith("SELECT 1 FROM folders WHERE id = %s AND company_id = %s"):
            fid, company_id = p
            row = self.folders.get(fid)
            return [{"1": 1}] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT * FROM folders WHERE id = %s AND company_id = %s"):
            fid, company_id = p
            row = self.folders.get(fid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("UPDATE folders SET name=%s WHERE id=%s"):
            name, fid, company_id = p
            row = self.folders.get(fid)
            if not row or row["company_id"] != company_id:
                return []
            row["name"] = name
            return [row]

        if sql.startswith("SELECT 1 FROM folders WHERE parent_id = %s"):
            fid, = p
            return [{"1": 1}] if any(f["parent_id"] == fid for f in self.folders.values()) else []

        if sql.startswith("SELECT 1 FROM documents WHERE folder_id = %s LIMIT 1"):
            fid, = p
            return [{"1": 1}] if any(d["folder_id"] == fid and not d["deleted_at"] for d in self.documents.values()) else []

        if sql.startswith("DELETE FROM folders WHERE id = %s AND company_id = %s"):
            fid, company_id = p
            row = self.folders.get(fid)
            if row and row["company_id"] == company_id:
                self.folders.pop(fid)
            return []

        # --- documents ---
        if sql.startswith("INSERT INTO documents"):
            (folder_id, name, object_name, content_type, size_bytes, category, department_id,
             contact_id, project_id, owner_id, owner_name, expiry_date, company_id) = p
            did = next(self._seq["documents"])
            row = {
                "id": did, "folder_id": folder_id, "name": name, "object_name": object_name,
                "content_type": content_type, "size_bytes": size_bytes, "category": category,
                "department_id": department_id, "contact_id": contact_id, "project_id": project_id,
                "owner_id": owner_id, "owner_name": owner_name, "status": "ativo", "expiry_date": expiry_date,
                "signature_status": None, "signed_by": None, "signed_at": None, "is_favorite": False,
                "deleted_at": None, "current_version": 1, "workflow_state": "enviado", "company_id": company_id,
            }
            self.documents[did] = row
            return [row]

        if sql.startswith("INSERT INTO document_versions"):
            return []

        if sql.startswith("INSERT INTO doc_notifications"):
            user_id, notif_type, document_id, message, company_id = p
            nid = next(self._seq["notifications"])
            self.notifications.append({"id": nid, "user_id": user_id, "type": notif_type,
                                        "document_id": document_id, "message": message, "is_read": False,
                                        "company_id": company_id})
            return []

        if sql.startswith("SELECT * FROM documents WHERE id = %s AND company_id = %s"):
            did, company_id = p
            row = self.documents.get(did)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT folder_id FROM documents WHERE id = %s AND company_id = %s"):
            did, company_id = p
            row = self.documents.get(did)
            return [{"folder_id": row["folder_id"]}] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT folder_id, owner_id, name FROM documents WHERE id = %s AND company_id = %s"):
            did, company_id = p
            row = self.documents.get(did)
            return [{"folder_id": row["folder_id"], "owner_id": row["owner_id"], "name": row["name"]}] \
                if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT folder_id, current_version FROM documents WHERE id = %s AND company_id = %s"):
            did, company_id = p
            row = self.documents.get(did)
            return [{"folder_id": row["folder_id"], "current_version": row["current_version"]}] \
                if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT t.id, t.name, t.color FROM document_tags dt JOIN doc_tags t ON t.id = dt.tag_id WHERE dt.document_id = %s"):
            did, = p
            return [dict(self.doc_tags[tid]) for (d, tid) in self.document_tags if d == did]

        if sql.startswith("UPDATE documents SET is_favorite = NOT is_favorite"):
            did, company_id = p
            row = self.documents.get(did)
            if not row or row["company_id"] != company_id:
                return []
            row["is_favorite"] = not row["is_favorite"]
            return [row]

        if sql.startswith("UPDATE documents SET status = CASE WHEN status = 'arquivado'"):
            did, = p
            row = self.documents.get(did)
            if not row:
                return []
            row["status"] = "ativo" if row["status"] == "arquivado" else "arquivado"
            return [row]

        if sql.startswith("UPDATE documents SET signature_status=%s"):
            signature_status, signer_name, did, company_id = p
            row = self.documents.get(did)
            if not row or row["company_id"] != company_id:
                return []
            row.update({"signature_status": signature_status, "signed_by": signer_name})
            return [row]

        if sql.startswith("UPDATE documents SET deleted_at = NOW() WHERE id = %s"):
            did, = p
            row = self.documents.get(did)
            if row:
                row["deleted_at"] = "now"
            return []

        if sql.startswith("UPDATE documents SET deleted_at = NULL"):
            did, = p
            row = self.documents.get(did)
            if not row:
                return []
            row["deleted_at"] = None
            return [row]

        if sql.startswith("UPDATE documents SET workflow_state=%s, updated_at=NOW() WHERE id=%s"):
            state, did, company_id = p
            row = self.documents.get(did)
            if not row or row["company_id"] != company_id:
                return []
            row["workflow_state"] = state
            return [row]

        if sql.startswith("UPDATE documents SET name=%s, folder_id=%s"):
            name, folder_id, category, department_id, contact_id, project_id, expiry_date, did = p
            row = self.documents.get(did)
            if not row:
                return []
            row.update({"name": name, "folder_id": folder_id, "category": category, "department_id": department_id,
                        "contact_id": contact_id, "project_id": project_id, "expiry_date": expiry_date})
            return [row]

        if sql.startswith("UPDATE documents SET object_name=%s"):
            object_name, content_type, size_bytes, current_version, did = p
            row = self.documents.get(did)
            if not row:
                return []
            row.update({"object_name": object_name, "content_type": content_type,
                        "size_bytes": size_bytes, "current_version": current_version})
            return [row]

        if sql.startswith("INSERT INTO document_workflow_log"):
            document_id, from_state, to_state, user_id, user_name, notes, company_id = p
            self.workflow_log.append({"document_id": document_id, "from_state": from_state,
                                       "to_state": to_state, "user_id": user_id, "user_name": user_name,
                                       "notes": notes, "company_id": company_id})
            return []

        if sql.startswith("SELECT * FROM document_workflow_log WHERE document_id = %s"):
            did, = p
            rows = [w for w in self.workflow_log if w["document_id"] == did]
            return list(reversed(rows))

        if sql.startswith("SELECT nome FROM usuarios WHERE id = %s"):
            uid, = p
            u = self.usuarios.get(uid)
            return [{"nome": u["nome"]}] if u else []

        # --- doc_tags ---
        if sql.startswith("SELECT * FROM doc_tags WHERE company_id = %s ORDER BY name"):
            company_id, = p
            rows = [t for t in self.doc_tags.values() if t["company_id"] == company_id]
            return sorted(rows, key=lambda t: t["name"])

        if sql.startswith("SELECT 1 FROM doc_tags WHERE name = %s AND company_id = %s"):
            name, company_id = p
            return [{"1": 1}] if any(t["name"] == name and t["company_id"] == company_id
                                      for t in self.doc_tags.values()) else []

        if sql.startswith("INSERT INTO doc_tags"):
            name, color, company_id = p
            tid = next(self._seq["doc_tags"])
            row = {"id": tid, "name": name, "color": color, "company_id": company_id}
            self.doc_tags[tid] = row
            return [row] if "RETURNING" in sql else []

        if sql.startswith("SELECT * FROM documents WHERE company_id = %s"):
            company_id = p[0]
            rest = list(p[1:])
            rows = [d for d in self.documents.values() if d["company_id"] == company_id]
            if "deleted_at IS NOT NULL" in sql:
                rows = [d for d in rows if d["deleted_at"]]
            elif "deleted_at IS NULL" in sql:
                rows = [d for d in rows if not d["deleted_at"]]
            if "folder_id = %s" in sql:
                rows = [d for d in rows if d["folder_id"] == rest.pop(0)]
            if "name ILIKE %s" in sql:
                needle = rest.pop(0).strip("%").lower()
                rows = [d for d in rows if needle in d["name"].lower()]
            if "category = %s" in sql:
                rows = [d for d in rows if d["category"] == rest.pop(0)]
            if "department_id = %s" in sql:
                rows = [d for d in rows if d["department_id"] == rest.pop(0)]
            if "status = %s" in sql:
                rows = [d for d in rows if d["status"] == rest.pop(0)]
            if "is_favorite = true" in sql:
                rows = [d for d in rows if d["is_favorite"]]
            if "expiry_date IS NOT NULL AND expiry_date <=" in sql:
                rows = [d for d in rows if d["expiry_date"] and d["expiry_date"] <= rest.pop(0)]
            if "document_tags WHERE tag_id" in sql:
                tag_id = rest.pop(0)
                doc_ids = {d for (d, t) in self.document_tags if t == tag_id}
                rows = [d for d in rows if d["id"] in doc_ids]
            return sorted(rows, key=lambda d: -d["id"])

        # --- doc_templates ---
        if sql.startswith("SELECT * FROM doc_templates WHERE id = %s AND company_id = %s"):
            tid, company_id = p
            row = self.doc_templates.get(tid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT * FROM doc_templates WHERE category = %s AND company_id = %s"):
            category, company_id = p
            rows = [t for t in self.doc_templates.values() if t["category"] == category and t["company_id"] == company_id]
            return sorted(rows, key=lambda t: t["name"])

        if sql.startswith("SELECT * FROM doc_templates WHERE company_id = %s ORDER BY name"):
            company_id, = p
            rows = [t for t in self.doc_templates.values() if t["company_id"] == company_id]
            return sorted(rows, key=lambda t: t["name"])

        if sql.startswith("INSERT INTO doc_templates"):
            name, category, object_name, content_type, size_bytes, created_by, created_by_name, company_id = p
            tid = next(self._seq["templates"])
            row = {"id": tid, "name": name, "category": category, "object_name": object_name,
                   "content_type": content_type, "size_bytes": size_bytes, "created_by": created_by,
                   "created_by_name": created_by_name, "company_id": company_id}
            self.doc_templates[tid] = row
            return [row]

        if sql.startswith("DELETE FROM doc_templates WHERE id = %s AND company_id = %s"):
            tid, company_id = p
            row = self.doc_templates.get(tid)
            if row and row["company_id"] == company_id:
                self.doc_templates.pop(tid)
            return []

        # --- permissions ---
        if sql.startswith("SELECT * FROM document_permissions WHERE folder_id = %s AND company_id = %s"):
            fid, company_id = p
            rows = [r for r in self.document_permissions.values() if r["folder_id"] == fid and r["company_id"] == company_id]
            return sorted(rows, key=lambda r: r["id"])

        if sql.startswith("SELECT * FROM document_permissions WHERE company_id = %s"):
            company_id, = p
            rows = [r for r in self.document_permissions.values() if r["company_id"] == company_id]
            return sorted(rows, key=lambda r: (r["folder_id"], r["id"]))

        if sql.startswith("SELECT 1 FROM document_permissions WHERE folder_id = %s AND scope_type = 'department'"):
            fid, scope_value = p
            return [{"1": 1}] if any(r["folder_id"] == fid and r["scope_type"] == "department" and r["scope_value"] == scope_value
                                      for r in self.document_permissions.values()) else []

        if sql.startswith("INSERT INTO document_permissions (scope_type, scope_value, folder_id, can_view, can_edit, can_delete, company_id)"):
            scope_type, scope_value, folder_id, can_view, can_edit, can_delete, company_id = p
            pid = next(self._seq["permissions"])
            row = {"id": pid, "scope_type": scope_type, "scope_value": scope_value, "folder_id": folder_id,
                   "can_view": can_view, "can_edit": can_edit, "can_delete": can_delete, "company_id": company_id}
            self.document_permissions[pid] = row
            return [row]

        if sql.startswith("INSERT INTO document_permissions"):
            scope_type, scope_value, folder_id, company_id = p
            pid = next(self._seq["permissions"])
            row = {"id": pid, "scope_type": scope_type, "scope_value": scope_value, "folder_id": folder_id,
                   "can_view": True, "can_edit": True, "can_delete": False, "company_id": company_id}
            self.document_permissions[pid] = row
            return []

        if sql.startswith("DELETE FROM document_permissions WHERE id = %s AND company_id = %s"):
            pid, company_id = p
            row = self.document_permissions.get(pid)
            if row and row["company_id"] == company_id:
                self.document_permissions.pop(pid)
            return []

        raise AssertionError(f"SQL não reconhecido pelo FakeDocumentsDB: {sql!r} params={p!r}")


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDocumentsDB()
    monkeypatch.setattr(app_module, "get_connection", db.connection_factory)
    monkeypatch.setattr(app_module, "upload_bytes", lambda data, object_name, content_type: None)
    monkeypatch.setattr(app_module, "delete_object", lambda object_name: None)
    monkeypatch.setattr(app_module, "get_presigned_url", lambda object_name: f"https://minio.local/{object_name}")
    # A recursão de permissões por pastas tem testes unitários dedicados em
    # test_permissions.py; aqui o utilizador de teste tem sempre acesso total.
    monkeypatch.setattr(app_module, "resolve_folder_permission",
                         lambda cur, user_id, folder_id: {"can_view": True, "can_edit": True, "can_delete": True})
    return db


@pytest.fixture
def client(fake_db):
    return TestClient(app_module.app)
