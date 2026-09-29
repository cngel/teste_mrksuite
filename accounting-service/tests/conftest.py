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

DOC_PREFIX = {"lancamento": "LC"}


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

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _apply_filters(rows, sql, p, specs, start_idx=1):
    result = list(rows)
    idx = start_idx
    for substr, fn in specs:
        if substr in sql:
            value = p[idx]
            idx += 1
            result = [r for r in result if fn(r, value)]
    return result


class FakeAccountingDB:
    """Emula as tabelas do accounting-service, todas escopadas por company_id."""

    def __init__(self):
        self.counters = {}
        self.accounts = {}
        self.entries = {}
        self.entry_lines = {}
        self.documents = {}
        self.blocklist = {}
        self._seq = {"accounts": itertools.count(1), "entries": itertools.count(1),
                     "lines": itertools.count(1), "documents": itertools.count(1)}

    def connection_factory(self):
        return FakeConnection(self.handle)

    def _line_with_account(self, line):
        account = self.accounts.get(line["account_id"], {})
        out = dict(line)
        out["account_code"] = account.get("code")
        out["account_name"] = account.get("name")
        return out

    def handle(self, sql, p):
        if sql.startswith("SELECT jti FROM jwt_blocklist"):
            jti, = p
            return [{"jti": jti}] if jti in self.blocklist else []

        # --- contador de numeração ---
        if sql.startswith("INSERT INTO cont_counters"):
            doc_type, = p
            self.counters.setdefault(doc_type, 1)
            return []
        if sql.startswith("SELECT next_seq FROM cont_counters WHERE doc_type=%s"):
            doc_type, = p
            return [{"next_seq": self.counters[doc_type]}]
        if sql.startswith("UPDATE cont_counters SET next_seq = next_seq + 1"):
            doc_type, = p
            self.counters[doc_type] += 1
            return []

        # --- plano de contas: seed (is_system=true, RETURNING id) ---
        if sql.startswith("SELECT id FROM cont_accounts WHERE code=%s AND company_id=%s"):
            code, company_id = p
            match = [a for a in self.accounts.values() if a["code"] == code and a["company_id"] == company_id]
            return [{"id": match[0]["id"]}] if match else []

        if sql.startswith("INSERT INTO cont_accounts") and "RETURNING id" in sql:
            code, name, account_class, account_type, parent_id, company_id = p
            aid = next(self._seq["accounts"])
            row = {"id": aid, "code": code, "name": name, "account_class": account_class,
                   "account_type": account_type, "parent_id": parent_id, "is_system": True,
                   "active": True, "created_at": aid, "company_id": company_id}
            self.accounts[aid] = row
            return [{"id": aid}]

        # --- plano de contas: create_account (is_system=false, RETURNING *) ---
        if sql.startswith("INSERT INTO cont_accounts"):
            code, name, account_class, account_type, parent_id, company_id = p
            for a in self.accounts.values():
                if a["code"] == code and a["company_id"] == company_id:
                    raise app_module.psycopg2.errors.UniqueViolation("duplicate code")
            aid = next(self._seq["accounts"])
            row = {"id": aid, "code": code, "name": name, "account_class": account_class,
                   "account_type": account_type, "parent_id": parent_id, "is_system": False,
                   "active": True, "created_at": aid, "company_id": company_id}
            self.accounts[aid] = row
            return [row]

        if sql.startswith("SELECT 1 FROM cont_accounts WHERE id=%s AND company_id=%s"):
            aid, company_id = p
            row = self.accounts.get(aid)
            return [{"1": 1}] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT * FROM cont_accounts WHERE id=%s AND company_id=%s"):
            aid, company_id = p
            row = self.accounts.get(aid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT * FROM cont_accounts WHERE code=%s AND company_id=%s"):
            code, company_id = p
            match = [a for a in self.accounts.values() if a["code"] == code and a["company_id"] == company_id]
            return [match[0]] if match else []

        if sql.startswith("UPDATE cont_accounts SET name=%s, active=%s"):
            name, active, aid, company_id = p
            row = self.accounts.get(aid)
            if not row or row["company_id"] != company_id:
                return []
            row["name"] = name
            row["active"] = active
            return [row]

        if sql.startswith("SELECT 1 FROM cont_entry_lines WHERE account_id=%s LIMIT 1"):
            aid, = p
            return [{"1": 1}] if any(l["account_id"] == aid for l in self.entry_lines.values()) else []

        if sql.startswith("SELECT 1 FROM cont_accounts WHERE parent_id=%s LIMIT 1"):
            aid, = p
            return [{"1": 1}] if any(a["parent_id"] == aid for a in self.accounts.values()) else []

        if sql.startswith("DELETE FROM cont_accounts WHERE id=%s AND company_id=%s"):
            aid, company_id = p
            row = self.accounts.get(aid)
            if row and row["company_id"] == company_id:
                self.accounts.pop(aid)
            return []

        if sql.startswith("SELECT * FROM cont_accounts WHERE company_id=%s"):
            rows = _apply_filters(
                [a for a in self.accounts.values() if a["company_id"] == p[0]], sql, p, [
                    ("active=%s", lambda r, v: r["active"] == v),
                    ("account_class=%s", lambda r, v: r["account_class"] == v),
                ])
            return sorted(rows, key=lambda r: r["code"])

        # --- lançamentos ---
        if sql.startswith("SELECT e.* FROM cont_entries e WHERE"):
            rows = [e for e in self.entries.values() if e["company_id"] == p[0]]
            idx = 1
            if "e.entry_date >= %s" in sql:
                rows = [r for r in rows if r["entry_date"] >= p[idx]]
                idx += 1
            if "e.entry_date <= %s" in sql:
                rows = [r for r in rows if r["entry_date"] <= p[idx]]
                idx += 1
            if "e.source = %s" in sql:
                rows = [r for r in rows if r["source"] == p[idx]]
                idx += 1
            if "EXISTS" in sql:
                account_id = p[idx]
                idx += 1
                entry_ids = {l["entry_id"] for l in self.entry_lines.values() if l["account_id"] == account_id}
                rows = [r for r in rows if r["id"] in entry_ids]
            return sorted(rows, key=lambda r: (r["entry_date"], r["id"]), reverse=True)

        if sql.startswith("SELECT l.id, l.account_id, l.debit, l.credit, l.memo, a.code AS account_code"):
            entry_id, = p
            lines = [self._line_with_account(l) for l in self.entry_lines.values() if l["entry_id"] == entry_id]
            return sorted(lines, key=lambda r: r["id"])

        if sql.startswith("SELECT * FROM cont_entries WHERE id=%s AND company_id=%s"):
            eid, company_id = p
            row = self.entries.get(eid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("INSERT INTO cont_entries") and "reversed_entry_id" not in sql:
            doc_number, entry_date, description, source, source_type, source_id, created_by, company_id = p
            eid = next(self._seq["entries"])
            row = {"id": eid, "doc_number": doc_number, "entry_date": entry_date, "description": description,
                   "source": source, "source_type": source_type, "source_id": source_id, "status": "lancado",
                   "reversed_entry_id": None, "created_by": created_by, "created_at": eid, "company_id": company_id}
            self.entries[eid] = row
            return [row]

        if sql.startswith("INSERT INTO cont_entries"):
            doc_number, entry_date, description, source_type, source_id, reversed_entry_id, created_by, company_id = p
            eid = next(self._seq["entries"])
            row = {"id": eid, "doc_number": doc_number, "entry_date": entry_date, "description": description,
                   "source": "manual", "source_type": source_type, "source_id": source_id, "status": "lancado",
                   "reversed_entry_id": reversed_entry_id, "created_by": created_by, "created_at": eid,
                   "company_id": company_id}
            self.entries[eid] = row
            return [row]

        if sql.startswith("INSERT INTO cont_entry_lines"):
            entry_id, account_id, debit, credit, memo, company_id = p
            lid = next(self._seq["lines"])
            row = {"id": lid, "entry_id": entry_id, "account_id": account_id, "debit": debit,
                   "credit": credit, "memo": memo, "company_id": company_id}
            self.entry_lines[lid] = row
            return [row]

        if sql.startswith("SELECT * FROM cont_entry_lines WHERE entry_id=%s ORDER BY id"):
            entry_id, = p
            rows = [l for l in self.entry_lines.values() if l["entry_id"] == entry_id]
            return sorted(rows, key=lambda r: r["id"])

        if sql.startswith("UPDATE cont_entries SET status='estornado'"):
            eid, company_id = p
            row = self.entries.get(eid)
            if row and row["company_id"] == company_id:
                row["status"] = "estornado"
            return []

        # --- livro razão ---
        if sql.startswith("SELECT COALESCE(SUM(l.debit - l.credit), 0) AS saldo") and "e.entry_date < %s" in sql:
            account_id, company_id, date_from = p
            total = sum(l["debit"] - l["credit"] for l in self.entry_lines.values()
                        if l["account_id"] == account_id and l["company_id"] == company_id
                        and self.entries[l["entry_id"]]["entry_date"] < date_from)
            return [{"saldo": total}]

        if sql.startswith("SELECT e.doc_number, e.entry_date, e.description, l.debit, l.credit, l.memo"):
            account_id, company_id = p[0], p[1]
            idx = 2
            rows = [l for l in self.entry_lines.values()
                    if l["account_id"] == account_id and l["company_id"] == company_id]
            if "e.entry_date >= %s" in sql:
                date_from = p[idx]
                idx += 1
                rows = [l for l in rows if self.entries[l["entry_id"]]["entry_date"] >= date_from]
            if "e.entry_date <= %s" in sql:
                date_to = p[idx]
                idx += 1
                rows = [l for l in rows if self.entries[l["entry_id"]]["entry_date"] <= date_to]
            out = []
            for l in rows:
                e = self.entries[l["entry_id"]]
                out.append({"doc_number": e["doc_number"], "entry_date": e["entry_date"],
                            "description": e["description"], "debit": l["debit"], "credit": l["credit"],
                            "memo": l["memo"]})
            return sorted(out, key=lambda r: (r["entry_date"], r["doc_number"]))

        # --- balancete ---
        if sql.startswith("SELECT a.id, a.code, a.name, a.account_class, COALESCE(SUM(l.debit)"):
            company_id = p[0]
            idx = 1
            date_from = date_to = None
            if "e.entry_date >= %s" in sql:
                date_from = p[idx]
                idx += 1
            if "e.entry_date <= %s" in sql:
                date_to = p[idx]
                idx += 1
            totals = {}
            for l in self.entry_lines.values():
                if l["company_id"] != company_id:
                    continue
                e = self.entries[l["entry_id"]]
                if date_from and e["entry_date"] < date_from:
                    continue
                if date_to and e["entry_date"] > date_to:
                    continue
                acc = self.accounts[l["account_id"]]
                t = totals.setdefault(acc["id"], {"id": acc["id"], "code": acc["code"], "name": acc["name"],
                                                    "account_class": acc["account_class"],
                                                    "debit_total": 0, "credit_total": 0})
                t["debit_total"] += l["debit"]
                t["credit_total"] += l["credit"]
            return sorted(totals.values(), key=lambda r: r["code"])

        # --- DRE ---
        if "a.account_class IN ('receita', 'despesa')" in sql:
            company_id = p[0]
            idx = 1
            date_from = date_to = None
            if "e.entry_date >= %s" in sql:
                date_from = p[idx]
                idx += 1
            if "e.entry_date <= %s" in sql:
                date_to = p[idx]
                idx += 1
            totals = {}
            for l in self.entry_lines.values():
                if l["company_id"] != company_id:
                    continue
                e = self.entries[l["entry_id"]]
                if date_from and e["entry_date"] < date_from:
                    continue
                if date_to and e["entry_date"] > date_to:
                    continue
                acc = self.accounts[l["account_id"]]
                if acc["account_class"] not in ("receita", "despesa"):
                    continue
                t = totals.setdefault(acc["id"], {"id": acc["id"], "code": acc["code"], "name": acc["name"],
                                                    "account_class": acc["account_class"], "total": 0})
                if acc["account_class"] == "receita":
                    t["total"] += l["credit"] - l["debit"]
                else:
                    t["total"] += l["debit"] - l["credit"]
            return sorted(totals.values(), key=lambda r: r["code"])

        # --- balanço patrimonial ---
        if "a.account_class IN ('ativo', 'passivo', 'patrimonio')" in sql:
            company_id, as_of_date = p
            totals = {}
            for l in self.entry_lines.values():
                if l["company_id"] != company_id:
                    continue
                e = self.entries[l["entry_id"]]
                if e["entry_date"] > as_of_date:
                    continue
                acc = self.accounts[l["account_id"]]
                if acc["account_class"] not in ("ativo", "passivo", "patrimonio"):
                    continue
                t = totals.setdefault(acc["id"], {"id": acc["id"], "code": acc["code"], "name": acc["name"],
                                                    "account_class": acc["account_class"], "total": 0})
                if acc["account_class"] == "ativo":
                    t["total"] += l["debit"] - l["credit"]
                else:
                    t["total"] += l["credit"] - l["debit"]
            return sorted(totals.values(), key=lambda r: r["code"])

        # --- resumo (KPIs) ---
        if "a.code IN ('1.1.1', '1.1.2')" in sql:
            company_id, = p
            total = sum(l["debit"] - l["credit"] for l in self.entry_lines.values()
                        if l["company_id"] == company_id
                        and self.accounts[l["account_id"]]["code"] in ("1.1.1", "1.1.2"))
            return [{"saldo": total}]

        if "a.code = '1.1.3'" in sql:
            company_id, = p
            total = sum(l["debit"] - l["credit"] for l in self.entry_lines.values()
                        if l["company_id"] == company_id and self.accounts[l["account_id"]]["code"] == "1.1.3")
            return [{"saldo": total}]

        if "a.code = '2.1.1'" in sql:
            company_id, = p
            total = sum(l["credit"] - l["debit"] for l in self.entry_lines.values()
                        if l["company_id"] == company_id and self.accounts[l["account_id"]]["code"] == "2.1.1")
            return [{"saldo": total}]

        if sql.startswith("SELECT COUNT(*) AS n FROM cont_entries WHERE company_id=%s AND entry_date >= %s"):
            company_id, month_start = p
            n = sum(1 for e in self.entries.values() if e["company_id"] == company_id and e["entry_date"] >= month_start)
            return [{"n": n}]

        if "AS receitas," in sql:
            company_id, cutoff = p
            is_upper_bound = "e.entry_date <= %s" in sql
            receitas = despesas = 0
            for l in self.entry_lines.values():
                if l["company_id"] != company_id:
                    continue
                e = self.entries[l["entry_id"]]
                if is_upper_bound:
                    if e["entry_date"] > cutoff:
                        continue
                else:
                    if e["entry_date"] < cutoff:
                        continue
                acc = self.accounts[l["account_id"]]
                if acc["account_class"] == "receita":
                    receitas += l["credit"] - l["debit"]
                elif acc["account_class"] == "despesa":
                    despesas += l["debit"] - l["credit"]
            return [{"receitas": receitas, "despesas": despesas}]

        # --- documentos ---
        if sql.startswith("SELECT id FROM cont_entries WHERE id=%s AND company_id=%s"):
            eid, company_id = p
            row = self.entries.get(eid)
            return [{"id": eid}] if row and row["company_id"] == company_id else []

        if sql.startswith("INSERT INTO cont_documents"):
            entity_type, entity_id, document_type, filename, object_name, content_type, uploaded_by, company_id = p
            did = next(self._seq["documents"])
            row = {"id": did, "entity_type": entity_type, "entity_id": entity_id, "document_type": document_type,
                   "filename": filename, "object_name": object_name, "content_type": content_type,
                   "uploaded_by": uploaded_by, "created_at": did, "company_id": company_id}
            self.documents[did] = row
            return [row]

        if sql.startswith("SELECT * FROM cont_documents WHERE entity_type='entry'"):
            eid, company_id = p
            rows = [d for d in self.documents.values()
                    if d["entity_type"] == "entry" and d["entity_id"] == eid and d["company_id"] == company_id]
            return sorted(rows, key=lambda r: -r["id"])

        if sql.startswith("SELECT * FROM cont_documents WHERE company_id=%s"):
            rows = _apply_filters(
                [d for d in self.documents.values() if d["company_id"] == p[0]], sql, p, [
                    ("document_type=%s", lambda r, v: r["document_type"] == v),
                ])
            return sorted(rows, key=lambda r: -r["id"])

        if sql.startswith("SELECT object_name FROM cont_documents WHERE id=%s AND company_id=%s"):
            did, company_id = p
            row = self.documents.get(did)
            return [{"object_name": row["object_name"]}] if row and row["company_id"] == company_id else []

        if sql.startswith("DELETE FROM cont_documents WHERE id=%s AND company_id=%s"):
            did, company_id = p
            row = self.documents.get(did)
            if row and row["company_id"] == company_id:
                self.documents.pop(did)
            return []

        raise AssertionError(f"SQL não reconhecido pelo FakeAccountingDB: {sql!r} params={p!r}")


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeAccountingDB()
    monkeypatch.setattr(app_module, "get_connection", db.connection_factory)
    monkeypatch.setattr(app_module, "upload_bytes", lambda data, object_name, content_type: None)
    monkeypatch.setattr(app_module, "delete_object", lambda object_name: None)
    monkeypatch.setattr(app_module, "get_presigned_url", lambda object_name: f"https://minio.local/{object_name}")
    return db


@pytest.fixture
def client(fake_db):
    return TestClient(app_module.app)
