"""Testes unitários do custeio (CMP e FIFO por lotes), isolados da camada HTTP —
usam um cursor mínimo que só entende as queries que apply_entrada/apply_saida/
consume_fifo emitem, sem precisar de Postgres nem de TestClient."""
import os

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("MINIO_ROOT_USER", "test")
os.environ.setdefault("MINIO_ROOT_PASSWORD", "test")

import pytest
from fastapi import HTTPException

import app as app_module


class MiniCursor:
    """Emula só as tabelas stk_item_stock e stk_batches, o suficiente para
    exercitar apply_entrada/apply_saida/consume_fifo tal como são chamadas em
    app.py — nenhuma outra query é reconhecida."""

    def __init__(self):
        self.stock: dict[tuple[int, int], dict] = {}
        self.batches: list[dict] = []
        self._next_batch_id = 1
        self._result: list = []

    def set_stock(self, item_id, warehouse_id, quantity, avg_cost, company_id=1):
        self.stock[(item_id, warehouse_id)] = {
            "item_id": item_id, "warehouse_id": warehouse_id,
            "quantity": quantity, "avg_cost": avg_cost, "company_id": company_id,
        }

    def add_batch(self, item_id, warehouse_id, quantity, unit_cost, received_at):
        b = {"id": self._next_batch_id, "item_id": item_id, "warehouse_id": warehouse_id,
             "quantity_remaining": quantity, "unit_cost": unit_cost, "received_at": received_at}
        self.batches.append(b)
        self._next_batch_id += 1
        return b

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        p = params or ()

        if s.startswith("SELECT * FROM stk_item_stock WHERE item_id=%s AND warehouse_id=%s AND company_id=%s FOR UPDATE"):
            item_id, warehouse_id, _company_id = p
            row = self.stock.get((item_id, warehouse_id))
            self._result = [row] if row else []
        elif s.startswith("INSERT INTO stk_item_stock"):
            item_id, warehouse_id, company_id = p
            self.stock.setdefault((item_id, warehouse_id), {
                "item_id": item_id, "warehouse_id": warehouse_id,
                "quantity": 0, "avg_cost": 0, "company_id": company_id,
            })
            self._result = []
        elif s.startswith("UPDATE stk_item_stock SET quantity=%s, avg_cost=%s"):
            quantity, avg_cost, item_id, warehouse_id, _company_id = p
            self.stock[(item_id, warehouse_id)].update({"quantity": quantity, "avg_cost": avg_cost})
            self._result = []
        elif s.startswith("UPDATE stk_item_stock SET quantity=%s WHERE"):
            quantity, item_id, warehouse_id, _company_id = p
            self.stock[(item_id, warehouse_id)]["quantity"] = quantity
            self._result = []
        elif s.startswith("SELECT * FROM stk_batches WHERE item_id=%s AND warehouse_id=%s AND company_id=%s"
                           " AND quantity_remaining > 0 ORDER BY received_at ASC FOR UPDATE"):
            item_id, warehouse_id, _company_id = p
            rows = [b for b in self.batches if b["item_id"] == item_id and b["warehouse_id"] == warehouse_id
                    and b["quantity_remaining"] > 0]
            self._result = sorted(rows, key=lambda b: b["received_at"])
        elif s.startswith("UPDATE stk_batches SET quantity_remaining=%s WHERE id=%s"):
            qty, bid = p
            for b in self.batches:
                if b["id"] == bid:
                    b["quantity_remaining"] = qty
            self._result = []
        else:
            raise AssertionError(f"SQL não esperado no MiniCursor: {s!r} params={p!r}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


def test_cmp_pondera_duas_entradas_a_custos_diferentes():
    cur = MiniCursor()
    item = {"id": 1, "track_batches": False}
    app_module.apply_entrada(cur, item, warehouse_id=1, quantity=10, unit_cost_aoa=100, company_id=1)
    novo_cmp = app_module.apply_entrada(cur, item, warehouse_id=1, quantity=10, unit_cost_aoa=200, company_id=1)
    assert novo_cmp == 150  # (10*100 + 10*200) / 20
    assert cur.stock[(1, 1)]["quantity"] == 20


def test_saida_sem_lotes_usa_cmp_corrente():
    cur = MiniCursor()
    item = {"id": 1, "track_batches": False}
    app_module.apply_entrada(cur, item, 1, 10, 100, 1)
    unit_cost_used, total_cost = app_module.apply_saida(cur, item, warehouse_id=1, quantity=4, company_id=1)
    assert unit_cost_used == 100
    assert total_cost == 400
    assert cur.stock[(1, 1)]["quantity"] == 6


def test_saida_insuficiente_lanca_409_e_nao_altera_stock():
    cur = MiniCursor()
    item = {"id": 1, "track_batches": False}
    app_module.apply_entrada(cur, item, 1, 5, 100, 1)
    with pytest.raises(HTTPException) as exc:
        app_module.apply_saida(cur, item, warehouse_id=1, quantity=10, company_id=1)
    assert exc.value.status_code == 409
    assert cur.stock[(1, 1)]["quantity"] == 5


def test_fifo_consome_lotes_mais_antigos_primeiro():
    cur = MiniCursor()
    item = {"id": 1, "track_batches": True}
    cur.set_stock(1, 1, 10, 0, company_id=1)
    cur.add_batch(1, 1, quantity=5, unit_cost=100, received_at="2026-01-01")
    cur.add_batch(1, 1, quantity=5, unit_cost=200, received_at="2026-01-05")

    unit_cost_used, total_cost = app_module.apply_saida(cur, item, warehouse_id=1, quantity=7, company_id=1)

    # Consome o lote mais antigo por inteiro (5 @ 100) + 2 do seguinte (2 @ 200)
    assert total_cost == 900
    assert round(unit_cost_used, 4) == round(900 / 7, 4)
    remaining = [b for b in cur.batches if b["quantity_remaining"] > 0]
    assert len(remaining) == 1
    assert remaining[0]["unit_cost"] == 200
    assert remaining[0]["quantity_remaining"] == 3


def test_fifo_lanca_409_quando_lotes_nao_cobrem_quantidade():
    # Saldo agregado (6) chega para passar a validação inicial, mas os lotes só
    # somam 5 — cenário real de dessincronização que a consulta FIFO tem de apanhar.
    cur = MiniCursor()
    item = {"id": 1, "track_batches": True}
    cur.set_stock(1, 1, 6, 0, company_id=1)
    cur.add_batch(1, 1, quantity=5, unit_cost=100, received_at="2026-01-01")

    with pytest.raises(HTTPException) as exc:
        app_module.apply_saida(cur, item, warehouse_id=1, quantity=6, company_id=1)
    assert exc.value.status_code == 409
