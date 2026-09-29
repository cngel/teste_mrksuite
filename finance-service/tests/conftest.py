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

DOC_PREFIX = {"fatura": "FAT", "recibo": "REC"}


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


def _apply_filters(rows, sql, p, specs):
    """p[0] é sempre company_id (WHERE company_id=%s é a primeira cláusula, sempre
    presente); os restantes parâmetros correspondem aos filtros opcionais, na ordem
    em que aparecem no SQL."""
    result = list(rows)
    idx = 1
    for substr, fn in specs:
        if substr in sql:
            value = p[idx]
            idx += 1
            result = [r for r in result if fn(r, value)]
    return result


class FakeFinanceDB:
    """Emula as tabelas do finance-service, todas escopadas por company_id —
    o isolamento entre empresas é reforçado em cada query (ver test_isolation.py)."""

    def __init__(self):
        self.counters = {}
        self.invoices = {}
        self.receipts = {}
        self.suppliers = {}
        self.expenses = {}
        self.documents = {}
        self.blocklist = {}
        self._seq = {"invoices": itertools.count(1), "receipts": itertools.count(1),
                     "suppliers": itertools.count(1), "expenses": itertools.count(1),
                     "documents": itertools.count(1)}

    def connection_factory(self):
        return FakeConnection(self.handle)

    def _next_doc_number(self, doc_type):
        seq = self.counters.get(doc_type, 1)
        self.counters[doc_type] = seq + 1
        return f"{DOC_PREFIX[doc_type]}-{seq:06d}"

    def handle(self, sql, p):
        if sql.startswith("SELECT jti FROM jwt_blocklist"):
            jti, = p
            return [{"jti": jti}] if jti in self.blocklist else []

        # --- contador de numeração ---
        if sql.startswith("INSERT INTO fin_counters"):
            doc_type, = p
            self.counters.setdefault(doc_type, 1)
            return []
        if sql.startswith("SELECT next_seq FROM fin_counters WHERE doc_type=%s"):
            doc_type, = p
            return [{"next_seq": self.counters[doc_type]}]
        if sql.startswith("UPDATE fin_counters SET next_seq = next_seq + 1"):
            doc_type, = p
            self.counters[doc_type] += 1
            return []

        # --- invoices: lookups específicos antes do listing genérico ---
        if sql.startswith("SELECT id FROM fin_invoices WHERE id=%s AND company_id=%s"):
            iid, company_id = p
            row = self.invoices.get(iid)
            return [{"id": iid}] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT * FROM fin_invoices WHERE id=%s AND company_id=%s FOR UPDATE"):
            iid, company_id = p
            row = self.invoices.get(iid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT * FROM fin_invoices WHERE id=%s AND company_id=%s"):
            iid, company_id = p
            row = self.invoices.get(iid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("INSERT INTO fin_invoices"):
            (doc_number, client_name, client_nif, client_email, client_contact_id, description,
             subtotal, iva_rate, iva_amount, total, issue_date, due_date, notes, company_id) = p
            iid = next(self._seq["invoices"])
            row = {
                "id": iid, "doc_number": doc_number, "client_name": client_name, "client_nif": client_nif,
                "client_email": client_email, "client_contact_id": client_contact_id, "description": description,
                "subtotal": subtotal, "iva_rate": iva_rate, "iva_amount": iva_amount, "total": total,
                "paid_amount": 0, "status": "emitida", "issue_date": issue_date, "due_date": due_date,
                "notes": notes, "created_at": iid, "company_id": company_id,
            }
            self.invoices[iid] = row
            return [row]

        if sql.startswith("SELECT * FROM fin_invoices WHERE company_id=%s"):
            rows = _apply_filters(
                [r for r in self.invoices.values() if r["company_id"] == p[0]], sql, p, [
                    ("status=%s", lambda r, v: r["status"] == v),
                    ("client_name ILIKE %s", lambda r, v: v.strip("%").lower() in r["client_name"].lower()),
                ])
            return sorted(rows, key=lambda r: -r["id"])

        if sql.startswith("UPDATE fin_invoices SET status='anulada'"):
            iid, company_id = p
            row = self.invoices.get(iid)
            if not row or row["company_id"] != company_id:
                return []
            row["status"] = "anulada"
            return [row]

        if sql.startswith("UPDATE fin_invoices SET paid_amount=%s, status=%s WHERE id=%s"):
            paid_amount, status, iid, company_id = p
            row = self.invoices.get(iid)
            if row and row["company_id"] == company_id:
                row["paid_amount"] = paid_amount
                row["status"] = status
            return []

        if sql.startswith("DELETE FROM fin_invoices WHERE id=%s"):
            iid, company_id = p
            row = self.invoices.get(iid)
            if row and row["company_id"] == company_id:
                self.invoices.pop(iid)
            return []

        # --- receipts ---
        if sql.startswith("SELECT id FROM fin_receipts WHERE invoice_id=%s"):
            iid, = p
            existing = [r for r in self.receipts.values() if r["invoice_id"] == iid]
            return [{"id": existing[0]["id"]}] if existing else []

        if sql.startswith("INSERT INTO fin_receipts"):
            doc_number, invoice_id, amount, payment_date, payment_method, company_id = p
            rid = next(self._seq["receipts"])
            row = {"id": rid, "doc_number": doc_number, "invoice_id": invoice_id, "amount": amount,
                   "payment_date": payment_date, "payment_method": payment_method, "created_at": rid,
                   "company_id": company_id}
            self.receipts[rid] = row
            return [row]

        if sql.startswith("SELECT * FROM fin_receipts WHERE invoice_id=%s"):
            iid, = p
            rows = [r for r in self.receipts.values() if r["invoice_id"] == iid]
            return sorted(rows, key=lambda r: -r["id"])

        if sql.startswith("SELECT payment_date AS day, SUM(amount) AS total"):
            company_id, date_from, date_to = p
            rows = [r for r in self.receipts.values()
                    if r["company_id"] == company_id and date_from <= r["payment_date"] <= date_to]
            totals: dict = {}
            for r in rows:
                totals[r["payment_date"]] = totals.get(r["payment_date"], 0) + r["amount"]
            return [{"day": day, "total": total} for day, total in totals.items()]

        if sql.startswith("SELECT paid_at::date AS day, SUM(amount) AS total"):
            company_id, date_from, date_to = p
            rows = [e for e in self.expenses.values()
                    if e["company_id"] == company_id and e.get("paid_at") and date_from <= e["paid_at"] <= date_to]
            totals: dict = {}
            for e in rows:
                totals[e["paid_at"]] = totals.get(e["paid_at"], 0) + e["amount"]
            return [{"day": day, "total": total} for day, total in totals.items()]

        if sql.startswith("SELECT COALESCE(SUM(amount), 0) AS total FROM fin_receipts WHERE company_id = %s AND payment_date >= %s"):
            company_id, month_start = p
            total = sum(r["amount"] for r in self.receipts.values()
                        if r["company_id"] == company_id and r["payment_date"] >= month_start)
            return [{"total": total}]

        if sql.startswith("SELECT COALESCE(SUM(total - paid_amount), 0) AS total FROM fin_invoices"):
            company_id, = p
            total = sum(r["total"] - r["paid_amount"] for r in self.invoices.values()
                        if r["company_id"] == company_id and r["status"] in ("emitida", "parcial", "vencida"))
            return [{"total": total}]

        if sql.startswith("SELECT COUNT(*) AS n FROM fin_invoices"):
            company_id, today = p
            n = sum(1 for r in self.invoices.values()
                    if r["company_id"] == company_id and r["status"] in ("emitida", "parcial")
                    and r["due_date"] is not None and r["due_date"] < today)
            return [{"n": n}]

        if sql.startswith("SELECT COUNT(*) AS n FROM fin_expenses WHERE company_id = %s AND status='pendente'"):
            company_id, = p
            n = sum(1 for e in self.expenses.values() if e["company_id"] == company_id and e["status"] == "pendente")
            return [{"n": n}]

        if sql.startswith("SELECT COALESCE(SUM(amount), 0) AS total FROM fin_expenses"):
            company_id, month_start = p
            total = sum(e["amount"] for e in self.expenses.values()
                        if e["company_id"] == company_id and e.get("paid_at") and e["paid_at"] >= month_start)
            return [{"total": total}]

        if sql.startswith("SELECT * FROM fin_receipts WHERE company_id=%s"):
            rows = _apply_filters(
                [r for r in self.receipts.values() if r["company_id"] == p[0]], sql, p, [
                    ("payment_date >= %s", lambda r, v: r["payment_date"] >= v),
                    ("payment_date <= %s", lambda r, v: r["payment_date"] <= v),
                ])
            return sorted(rows, key=lambda r: -r["id"])

        # --- suppliers ---
        if sql.startswith("SELECT * FROM fin_suppliers WHERE id=%s AND company_id=%s"):
            sid, company_id = p
            row = self.suppliers.get(sid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("INSERT INTO fin_suppliers"):
            name, nif, email, phone, category, address, notes, company_id = p
            sid = next(self._seq["suppliers"])
            row = {"id": sid, "name": name, "nif": nif, "email": email, "phone": phone,
                   "category": category, "address": address, "notes": notes, "active": True, "created_at": sid,
                   "company_id": company_id}
            self.suppliers[sid] = row
            return [row]

        if sql.startswith("UPDATE fin_suppliers SET name=%s"):
            name, nif, email, phone, category, address, notes, active, sid, company_id = p
            row = self.suppliers.get(sid)
            if not row or row["company_id"] != company_id:
                return []
            row.update({"name": name, "nif": nif, "email": email, "phone": phone,
                        "category": category, "address": address, "notes": notes, "active": active})
            return [row]

        if sql.startswith("SELECT 1 FROM fin_expenses WHERE supplier_id=%s AND company_id=%s"):
            sid, company_id = p
            return [{"1": 1}] if any(e["supplier_id"] == sid and e["company_id"] == company_id
                                      for e in self.expenses.values()) else []

        if sql.startswith("DELETE FROM fin_suppliers WHERE id=%s AND company_id=%s"):
            sid, company_id = p
            row = self.suppliers.get(sid)
            if row and row["company_id"] == company_id:
                self.suppliers.pop(sid)
            return []

        if sql.startswith("SELECT * FROM fin_suppliers WHERE company_id=%s"):
            rows = _apply_filters(
                [r for r in self.suppliers.values() if r["company_id"] == p[0]], sql, p, [
                    ("active=%s", lambda r, v: r["active"] == v),
                    ("(name ILIKE %s OR nif ILIKE %s)", lambda r, v: v.strip("%").lower() in (r["name"] or "").lower()
                        or v.strip("%").lower() in (r["nif"] or "").lower()),
                ])
            return sorted(rows, key=lambda r: r["name"])

        # --- expenses: lookups específicos antes do listing genérico ---
        if sql.startswith("SELECT name FROM fin_suppliers WHERE id=%s AND company_id=%s"):
            sid, company_id = p
            row = self.suppliers.get(sid)
            return [{"name": row["name"]}] if row and row["company_id"] == company_id else []

        if sql.startswith("INSERT INTO fin_expenses"):
            (description, supplier_id, supplier_name, category, department_id,
             requested_by, approver_id, amount, due_date, company_id) = p
            eid = next(self._seq["expenses"])
            row = {
                "id": eid, "description": description, "supplier_id": supplier_id, "supplier_name": supplier_name,
                "category": category, "department_id": department_id, "requested_by": requested_by,
                "approver_id": approver_id, "amount": amount, "due_date": due_date, "status": "pendente",
                "approval_note": None, "created_at": eid, "decided_at": None, "paid_at": None,
                "company_id": company_id,
            }
            self.expenses[eid] = row
            return [row]

        if sql.startswith("UPDATE fin_expenses SET status='aprovada'"):
            approval_note, eid, company_id = p
            row = self.expenses.get(eid)
            if not row or row["company_id"] != company_id or row["status"] != "pendente":
                return []
            row["status"] = "aprovada"
            row["approval_note"] = approval_note
            return [row]

        if sql.startswith("UPDATE fin_expenses SET status='rejeitada'"):
            approval_note, eid, company_id = p
            row = self.expenses.get(eid)
            if not row or row["company_id"] != company_id or row["status"] != "pendente":
                return []
            row["status"] = "rejeitada"
            row["approval_note"] = approval_note
            return [row]

        if sql.startswith("UPDATE fin_expenses SET status='paga'"):
            eid, company_id = p
            row = self.expenses.get(eid)
            if not row or row["company_id"] != company_id or row["status"] != "aprovada":
                return []
            row["status"] = "paga"
            return [row]

        if sql.startswith("DELETE FROM fin_expenses WHERE id=%s AND company_id=%s AND status='pendente'"):
            eid, company_id = p
            row = self.expenses.get(eid)
            if row and row["company_id"] == company_id and row["status"] == "pendente":
                self.expenses.pop(eid)
            return []

        if sql.startswith("SELECT * FROM fin_expenses WHERE supplier_id=%s AND company_id=%s"):
            sid, company_id = p
            rows = [e for e in self.expenses.values() if e["supplier_id"] == sid and e["company_id"] == company_id]
            return sorted(rows, key=lambda r: -r["id"])

        if sql.startswith("SELECT * FROM fin_expenses WHERE company_id=%s"):
            rows = _apply_filters(
                [r for r in self.expenses.values() if r["company_id"] == p[0]], sql, p, [
                    ("status=%s", lambda r, v: r["status"] == v),
                    ("department_id=%s", lambda r, v: r["department_id"] == v),
                    ("requested_by=%s", lambda r, v: r["requested_by"] == v),
                ])
            return sorted(rows, key=lambda r: -r["id"])

        # --- fin_documents ---
        if (sql.startswith("SELECT id FROM fin_invoices WHERE id=%s AND company_id=%s")
                or sql.startswith("SELECT id FROM fin_expenses WHERE id=%s AND company_id=%s")):
            eid, company_id = p
            table = self.invoices if "fin_invoices" in sql else self.expenses
            row = table.get(eid)
            return [{"id": eid}] if row and row["company_id"] == company_id else []

        if sql.startswith("INSERT INTO fin_documents"):
            entity_type, entity_id, document_type, filename, object_name, content_type, uploaded_by, company_id = p
            did = next(self._seq["documents"])
            row = {"id": did, "entity_type": entity_type, "entity_id": entity_id, "document_type": document_type,
                   "filename": filename, "object_name": object_name, "content_type": content_type,
                   "uploaded_by": uploaded_by, "created_at": did, "company_id": company_id}
            self.documents[did] = row
            return [row]

        if sql.startswith("SELECT * FROM fin_documents WHERE entity_type='invoice'"):
            iid, company_id = p
            rows = [d for d in self.documents.values()
                    if d["entity_type"] == "invoice" and d["entity_id"] == iid and d["company_id"] == company_id]
            return sorted(rows, key=lambda r: -r["id"])

        if sql.startswith("SELECT * FROM fin_documents WHERE entity_type='expense'"):
            eid, company_id = p
            rows = [d for d in self.documents.values()
                    if d["entity_type"] == "expense" and d["entity_id"] == eid and d["company_id"] == company_id]
            return sorted(rows, key=lambda r: -r["id"])

        if sql.startswith("SELECT * FROM fin_documents WHERE company_id=%s"):
            rows = _apply_filters(
                [r for r in self.documents.values() if r["company_id"] == p[0]], sql, p, [
                    ("entity_type=%s", lambda r, v: r["entity_type"] == v),
                    ("document_type=%s", lambda r, v: r["document_type"] == v),
                ])
            return sorted(rows, key=lambda r: -r["id"])

        if sql.startswith("SELECT object_name FROM fin_documents WHERE id=%s AND company_id=%s"):
            did, company_id = p
            row = self.documents.get(did)
            return [{"object_name": row["object_name"]}] if row and row["company_id"] == company_id else []

        if sql.startswith("DELETE FROM fin_documents WHERE id=%s AND company_id=%s"):
            did, company_id = p
            row = self.documents.get(did)
            if row and row["company_id"] == company_id:
                self.documents.pop(did)
            return []

        raise AssertionError(f"SQL não reconhecido pelo FakeFinanceDB: {sql!r} params={p!r}")


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeFinanceDB()
    monkeypatch.setattr(app_module, "get_connection", db.connection_factory)
    monkeypatch.setattr(app_module, "upload_bytes", lambda data, object_name, content_type: None)
    monkeypatch.setattr(app_module, "delete_object", lambda object_name: None)
    monkeypatch.setattr(app_module, "get_presigned_url", lambda object_name: f"https://minio.local/{object_name}")
    # Por omissão, a integração com o accounting-service é um no-op nos testes —
    # sem isto, cada teste que cria facturas/recibos/despesas pagaria o timeout
    # de rede de tentar contactar um accounting-service inexistente. Os testes
    # da própria integração (test_accounting_integration.py) substituem isto
    # por um spy explícito via monkeypatch.setattr no corpo do teste.
    monkeypatch.setattr(app_module, "post_journal_entry", lambda *args, **kwargs: None)
    return db


@pytest.fixture
def client(fake_db):
    return TestClient(app_module.app)
