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


class FakeCrmDB:
    """Emula 'contacts', 'attachments' e 'jwt_blocklist' para os testes do crm-service
    correrem sem Postgres real. Todas as linhas são guardadas com company_id — o
    isolamento entre empresas é a própria razão de ser deste fake (ver test_isolation.py)."""

    def __init__(self):
        self.contacts = {}
        self.attachments = {}
        self._contact_seq = itertools.count(1)
        self._attachment_seq = itertools.count(1)
        self.blocklist = {}

    def connection_factory(self):
        return FakeConnection(self.handle)

    def handle(self, sql, p):
        if sql.startswith("SELECT jti FROM jwt_blocklist"):
            jti, = p
            return [{"jti": jti}] if jti in self.blocklist else []

        if sql.startswith("SELECT * FROM contacts WHERE company_id = %s ORDER BY created_at DESC"):
            company_id, = p
            rows = [c for c in self.contacts.values() if c["company_id"] == company_id]
            return sorted(rows, key=lambda c: -c["id"])

        if sql.startswith("INSERT INTO contacts"):
            cid = next(self._contact_seq)
            (name, email, phone, company, notes, stage, pipeline_value,
             channel, owner, service_type, lead_date, company_id) = p
            row = {
                "id": cid, "name": name, "email": email, "phone": phone, "company": company,
                "notes": notes, "stage": stage, "pipeline_value": pipeline_value, "channel": channel,
                "owner": owner, "service_type": service_type, "lead_date": lead_date, "created_at": cid,
                "company_id": company_id,
            }
            self.contacts[cid] = row
            return [row]

        if sql.startswith("SELECT * FROM contacts WHERE id = %s AND company_id = %s"):
            cid, company_id = p
            row = self.contacts.get(cid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("UPDATE contacts SET name=%s, email=%s, phone=%s, company=%s, notes=%s,"):
            (name, email, phone, company, notes, stage, pipeline_value,
             channel, owner, service_type, lead_date, cid, company_id) = p
            row = self.contacts.get(cid)
            if not row or row["company_id"] != company_id:
                return []
            row.update({
                "name": name, "email": email, "phone": phone, "company": company, "notes": notes,
                "stage": stage, "pipeline_value": pipeline_value, "channel": channel,
                "owner": owner, "service_type": service_type, "lead_date": lead_date,
            })
            return [row]

        if sql.startswith("UPDATE contacts SET stage=%s WHERE id=%s AND company_id=%s"):
            stage, cid, company_id = p
            row = self.contacts.get(cid)
            if not row or row["company_id"] != company_id:
                return []
            row["stage"] = stage
            return [row]

        if sql.startswith("DELETE FROM contacts WHERE id = %s AND company_id = %s"):
            cid, company_id = p
            row = self.contacts.get(cid)
            if row and row["company_id"] == company_id:
                self.contacts.pop(cid)
            return []

        if sql.startswith("SELECT 1 FROM contacts WHERE id = %s AND company_id = %s"):
            cid, company_id = p
            row = self.contacts.get(cid)
            return [{"1": 1}] if row and row["company_id"] == company_id else []

        if sql.startswith("INSERT INTO attachments"):
            contact_id, filename, object_name, content_type, company_id = p
            aid = next(self._attachment_seq)
            row = {
                "id": aid, "contact_id": contact_id, "filename": filename,
                "object_name": object_name, "content_type": content_type, "created_at": aid,
                "company_id": company_id,
            }
            self.attachments[aid] = row
            return [row]

        if sql.startswith("SELECT * FROM attachments WHERE contact_id = %s AND company_id = %s"):
            contact_id, company_id = p
            rows = [a for a in self.attachments.values()
                    if a["contact_id"] == contact_id and a["company_id"] == company_id]
            return sorted(rows, key=lambda a: -a["id"])

        raise AssertionError(f"SQL não reconhecido pelo FakeCrmDB: {sql!r} params={p!r}")


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeCrmDB()
    monkeypatch.setattr(app_module, "get_connection", db.connection_factory)
    monkeypatch.setattr(app_module, "upload_bytes", lambda data, object_name, content_type: None)
    monkeypatch.setattr(app_module, "get_presigned_url", lambda object_name: f"https://minio.local/{object_name}")
    return db


@pytest.fixture
def client(fake_db):
    return TestClient(app_module.app)
