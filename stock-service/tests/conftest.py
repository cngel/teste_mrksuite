import itertools
import os

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("MINIO_ROOT_USER", "test")
os.environ.setdefault("MINIO_ROOT_PASSWORD", "test")

import psycopg2
import pytest
from fastapi.testclient import TestClient

import app as app_module


class _UniqueViolation(psycopg2.errors.UniqueViolation):
    """Usada nos testes para simular a corrida em INSERT INTO stk_movements com
    um client_ref repetido — app.py só apanha psycopg2.errors.UniqueViolation."""

DOC_PREFIX = {"guia": "GT"}


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
    def __init__(self, db):
        self._db = db

    def cursor(self, cursor_factory=None):
        return FakeCursor(self._db.handle)

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _apply_filters(rows, sql, p, specs):
    """p[0] é sempre company_id (WHERE company_id=%s é a primeira cláusula);
    os restantes parâmetros correspondem aos filtros opcionais, na ordem em
    que aparecem no SQL."""
    result = list(rows)
    idx = 1
    for substr, fn in specs:
        if substr in sql:
            value = p[idx]
            idx += 1
            result = [r for r in result if fn(r, value)]
    return result


class FakeStockDB:
    """Emula as tabelas do stock-service necessárias para os testes de HTTP
    (armazéns, artigos, saldo em stock, lotes e movimentos) — todas escopadas
    por company_id, tal como em produção."""

    def __init__(self):
        self.warehouses: dict[int, dict] = {}
        self.items: dict[int, dict] = {}
        self.item_stock: dict[tuple[int, int], dict] = {}
        self.batches: dict[int, dict] = {}
        self.movements: dict[int, dict] = {}
        self.counters: dict[str, int] = {}
        self.blocklist: set[str] = set()
        self._seq = {
            "warehouses": itertools.count(1), "items": itertools.count(1),
            "batches": itertools.count(1), "movements": itertools.count(1),
        }

    def connection_factory(self):
        return FakeConnection(self)

    def handle(self, sql, p):
        # --- auth ---
        if sql.startswith("SELECT jti FROM jwt_blocklist"):
            jti, = p
            return [{"jti": jti}] if jti in self.blocklist else []

        # --- armazéns ---
        if sql.startswith("INSERT INTO stk_warehouses"):
            name, code, type_, address, company_id = p
            wid = next(self._seq["warehouses"])
            row = {"id": wid, "name": name, "code": code, "type": type_, "address": address,
                   "active": True, "created_at": wid, "company_id": company_id}
            self.warehouses[wid] = row
            return [row]

        if sql.startswith("SELECT id FROM stk_warehouses WHERE id=%s AND company_id=%s"):
            wid, company_id = p
            row = self.warehouses.get(wid)
            return [{"id": wid}] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT * FROM stk_warehouses WHERE id=%s AND company_id=%s"):
            wid, company_id = p
            row = self.warehouses.get(wid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT * FROM stk_warehouses WHERE company_id=%s"):
            rows = _apply_filters(
                [r for r in self.warehouses.values() if r["company_id"] == p[0]], sql, p, [
                    ("active=%s", lambda r, v: r["active"] == v),
                ])
            return sorted(rows, key=lambda r: r["name"])

        # --- artigos ---
        if sql.startswith("INSERT INTO stk_items"):
            sku, barcode, name, description, category, unit, min_stock, track_batches, company_id = p
            iid = next(self._seq["items"])
            row = {"id": iid, "sku": sku, "barcode": barcode, "name": name, "description": description,
                   "category": category, "unit": unit, "min_stock": min_stock, "track_batches": track_batches,
                   "active": True, "created_at": iid, "company_id": company_id}
            self.items[iid] = row
            return [row]

        if sql.startswith("SELECT id FROM stk_items WHERE id=%s AND company_id=%s"):
            iid, company_id = p
            row = self.items.get(iid)
            return [{"id": iid}] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT * FROM stk_items WHERE id=%s AND company_id=%s"):
            iid, company_id = p
            row = self.items.get(iid)
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("SELECT * FROM stk_items WHERE company_id=%s"):
            rows = _apply_filters(
                [r for r in self.items.values() if r["company_id"] == p[0]], sql, p, [
                    ("(name ILIKE %s OR sku ILIKE %s OR barcode ILIKE %s)", lambda r, v: True),
                    ("barcode=%s", lambda r, v: r["barcode"] == v),
                    ("category=%s", lambda r, v: r["category"] == v),
                    ("active=%s", lambda r, v: r["active"] == v),
                ])
            return sorted(rows, key=lambda r: r["name"])

        # --- saldo em stock (get_or_create_stock_row / apply_entrada / apply_saida) ---
        if sql.startswith("SELECT * FROM stk_item_stock WHERE item_id=%s AND warehouse_id=%s AND company_id=%s FOR UPDATE"):
            item_id, warehouse_id, company_id = p
            row = self.item_stock.get((item_id, warehouse_id))
            return [row] if row and row["company_id"] == company_id else []

        if sql.startswith("INSERT INTO stk_item_stock"):
            item_id, warehouse_id, company_id = p
            self.item_stock.setdefault((item_id, warehouse_id), {
                "item_id": item_id, "warehouse_id": warehouse_id,
                "quantity": 0, "avg_cost": 0, "min_stock": None, "company_id": company_id,
            })
            return []

        if sql.startswith("UPDATE stk_item_stock SET quantity=%s, avg_cost=%s"):
            quantity, avg_cost, item_id, warehouse_id, company_id = p
            self.item_stock[(item_id, warehouse_id)].update({"quantity": quantity, "avg_cost": avg_cost})
            return []

        if sql.startswith("UPDATE stk_item_stock SET quantity=%s WHERE"):
            quantity, item_id, warehouse_id, company_id = p
            self.item_stock[(item_id, warehouse_id)]["quantity"] = quantity
            return []

        # --- lotes ---
        if sql.startswith("SELECT * FROM stk_batches WHERE item_id=%s AND warehouse_id=%s AND company_id=%s"
                           " AND quantity_remaining > 0 ORDER BY received_at ASC FOR UPDATE"):
            item_id, warehouse_id, company_id = p
            rows = [b for b in self.batches.values()
                    if b["item_id"] == item_id and b["warehouse_id"] == warehouse_id
                    and b["company_id"] == company_id and b["quantity_remaining"] > 0]
            return sorted(rows, key=lambda b: b["received_at"])

        if sql.startswith("UPDATE stk_batches SET quantity_remaining=%s WHERE id=%s"):
            qty, bid = p
            self.batches[bid]["quantity_remaining"] = qty
            return []

        if sql.startswith("INSERT INTO stk_batches"):
            item_id, warehouse_id, batch_number, expiry_date, qty_received, qty_remaining, unit_cost, company_id = p
            bid = next(self._seq["batches"])
            self.batches[bid] = {
                "id": bid, "item_id": item_id, "warehouse_id": warehouse_id, "batch_number": batch_number,
                "expiry_date": expiry_date, "quantity_received": qty_received, "quantity_remaining": qty_remaining,
                "unit_cost": unit_cost, "received_at": bid, "company_id": company_id,
            }
            return []

        # --- movimentos ---
        if sql.startswith("SELECT * FROM stk_movements WHERE client_ref=%s AND company_id=%s"):
            client_ref, company_id = p
            rows = [m for m in self.movements.values()
                    if m["client_ref"] == client_ref and m["company_id"] == company_id]
            return rows[:1]

        if sql.startswith("INSERT INTO stk_movements"):
            (movement_type, item_id, warehouse_id, destination_warehouse_id, quantity, currency_code, rate,
             unit_cost, unit_cost_aoa, total_cost_aoa, sale_price, batch_number, expiry_date, reason,
             reference_doc_type, reference_doc_id, client_ref, created_by, company_id) = p
            if client_ref is not None and any(
                m["client_ref"] == client_ref and m["company_id"] == company_id for m in self.movements.values()
            ):
                raise _UniqueViolation()
            mid = next(self._seq["movements"])
            row = {
                "id": mid, "movement_type": movement_type, "item_id": item_id, "warehouse_id": warehouse_id,
                "destination_warehouse_id": destination_warehouse_id, "quantity": quantity,
                "currency_code": currency_code, "exchange_rate": rate, "unit_cost": unit_cost,
                "unit_cost_aoa": unit_cost_aoa, "total_cost_aoa": total_cost_aoa, "sale_price": sale_price,
                "batch_number": batch_number, "expiry_date": expiry_date, "reason": reason,
                "reference_doc_type": reference_doc_type, "reference_doc_id": reference_doc_id,
                "client_ref": client_ref, "created_by": created_by, "created_at": mid, "company_id": company_id,
            }
            self.movements[mid] = row
            return [row]

        if "FROM stk_movements m" in sql and sql.startswith("SELECT m.*"):
            rows = _apply_filters(
                [r for r in self.movements.values() if r["company_id"] == p[0]], sql, p, [
                    ("m.item_id=%s", lambda r, v: r["item_id"] == v),
                    ("(m.warehouse_id=%s OR m.destination_warehouse_id=%s)",
                     lambda r, v: r["warehouse_id"] == v or r["destination_warehouse_id"] == v),
                    ("m.movement_type=%s", lambda r, v: r["movement_type"] == v),
                ])
            out = []
            for r in rows:
                item = self.items.get(r["item_id"], {})
                wh = self.warehouses.get(r["warehouse_id"], {})
                out.append({**r, "item_name": item.get("name"), "item_sku": item.get("sku"),
                            "warehouse_name": wh.get("name")})
            return sorted(out, key=lambda r: -r["id"])

        if sql.startswith("SELECT s.warehouse_id, w.name AS warehouse_name, s.quantity, s.avg_cost,"):
            item_id, _item_id_again, company_id = p
            rows = [r for r in self.item_stock.values() if r["item_id"] == item_id and r["company_id"] == company_id]
            out = []
            for r in rows:
                wh = self.warehouses.get(r["warehouse_id"], {})
                item = self.items.get(item_id, {})
                out.append({
                    "warehouse_id": r["warehouse_id"], "warehouse_name": wh.get("name"),
                    "quantity": r["quantity"], "avg_cost": r["avg_cost"],
                    "min_stock": r["min_stock"] if r["min_stock"] is not None else item.get("min_stock"),
                })
            return sorted(out, key=lambda r: r["warehouse_name"] or "")

        # --- alertas / resumo (não usados nos testes actuais, mas mantidos simples) ---
        if sql.startswith("SELECT COUNT(*) AS n FROM stk_items"):
            company_id = p[0]
            return [{"n": sum(1 for r in self.items.values() if r["company_id"] == company_id and r["active"])}]

        if sql.startswith("SELECT COUNT(*) AS n FROM stk_warehouses"):
            company_id = p[0]
            return [{"n": sum(1 for r in self.warehouses.values() if r["company_id"] == company_id and r["active"])}]

        raise AssertionError(f"SQL não reconhecido pelo FakeStockDB: {sql!r} params={p!r}")


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeStockDB()
    monkeypatch.setattr(app_module, "get_connection", db.connection_factory)
    monkeypatch.setattr(app_module, "upload_bytes", lambda data, object_name, content_type: None)
    monkeypatch.setattr(app_module, "delete_object", lambda object_name: None)
    monkeypatch.setattr(app_module, "get_presigned_url", lambda object_name: f"https://minio.local/{object_name}")
    # Best-effort: sem isto, cada teste que cria movimentos pagaria o timeout de
    # rede de tentar contactar um accounting-service inexistente.
    monkeypatch.setattr(app_module, "post_journal_entry", lambda *args, **kwargs: None)
    return db


@pytest.fixture
def client(fake_db):
    return TestClient(app_module.app)
