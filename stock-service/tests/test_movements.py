from datetime import datetime, timedelta, timezone

from jose import jwt as jose_jwt

import app as app_module


def _auth(company_id, sub="user-1"):
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "type": "access", "iat": now, "exp": now + timedelta(minutes=15), "company_id": company_id}
    token = jose_jwt.encode(payload, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


def _setup_warehouse_and_item(client, company_id=1, track_batches=False):
    wh = client.post("/warehouses", json={"name": "Armazém Central"}, headers=_auth(company_id)).json()
    item = client.post("/items", json={
        "name": "Cimento 50kg", "sku": "CIM-50", "min_stock": 5, "track_batches": track_batches,
    }, headers=_auth(company_id)).json()
    return wh, item


def test_entrada_aumenta_stock_e_actualiza_cmp(client):
    wh, item = _setup_warehouse_and_item(client)
    r1 = client.post("/movements", json={
        "movement_type": "entrada", "item_id": item["id"], "warehouse_id": wh["id"],
        "quantity": 10, "unit_cost": 100,
    }, headers=_auth(1))
    assert r1.status_code == 201
    # Uma entrada regista o custo pago nessa compra — não o CMP resultante.
    assert float(r1.json()["unit_cost_aoa"]) == 100
    assert float(r1.json()["total_cost_aoa"]) == 1000

    r2 = client.post("/movements", json={
        "movement_type": "entrada", "item_id": item["id"], "warehouse_id": wh["id"],
        "quantity": 10, "unit_cost": 200,
    }, headers=_auth(1))
    assert r2.status_code == 201
    assert float(r2.json()["unit_cost_aoa"]) == 200

    # O CMP (Custo Médio Ponderado) fica em stk_item_stock, consultável por armazém.
    stock = client.get(f"/items/{item['id']}/stock", headers=_auth(1)).json()
    assert len(stock) == 1
    assert float(stock[0]["quantity"]) == 20
    assert float(stock[0]["avg_cost"]) == 150  # (10*100 + 10*200) / 20


def test_saida_usa_cmp_e_reduz_stock(client):
    wh, item = _setup_warehouse_and_item(client)
    client.post("/movements", json={
        "movement_type": "entrada", "item_id": item["id"], "warehouse_id": wh["id"],
        "quantity": 10, "unit_cost": 100,
    }, headers=_auth(1))

    r = client.post("/movements", json={
        "movement_type": "saida", "item_id": item["id"], "warehouse_id": wh["id"], "quantity": 4,
    }, headers=_auth(1))
    assert r.status_code == 201
    assert float(r.json()["unit_cost_aoa"]) == 100
    assert float(r.json()["total_cost_aoa"]) == 400


def test_saida_com_stock_insuficiente_devolve_409(client):
    wh, item = _setup_warehouse_and_item(client)
    client.post("/movements", json={
        "movement_type": "entrada", "item_id": item["id"], "warehouse_id": wh["id"],
        "quantity": 5, "unit_cost": 100,
    }, headers=_auth(1))

    r = client.post("/movements", json={
        "movement_type": "saida", "item_id": item["id"], "warehouse_id": wh["id"], "quantity": 10,
    }, headers=_auth(1))
    assert r.status_code == 409


def test_transferencia_move_stock_entre_armazens_mantendo_custo(client):
    origem = client.post("/warehouses", json={"name": "Loja A"}, headers=_auth(1)).json()
    destino = client.post("/warehouses", json={"name": "Loja B"}, headers=_auth(1)).json()
    item = client.post("/items", json={"name": "Cimento 50kg"}, headers=_auth(1)).json()

    client.post("/movements", json={
        "movement_type": "entrada", "item_id": item["id"], "warehouse_id": origem["id"],
        "quantity": 10, "unit_cost": 100,
    }, headers=_auth(1))

    r = client.post("/movements", json={
        "movement_type": "transferencia", "item_id": item["id"], "warehouse_id": origem["id"],
        "destination_warehouse_id": destino["id"], "quantity": 4,
    }, headers=_auth(1))
    assert r.status_code == 201
    assert r.json()["destination_warehouse_id"] == destino["id"]
    assert float(r.json()["unit_cost_aoa"]) == 100


def test_movimento_com_client_ref_repetido_e_idempotente(client):
    wh, item = _setup_warehouse_and_item(client)
    body = {
        "movement_type": "entrada", "item_id": item["id"], "warehouse_id": wh["id"],
        "quantity": 10, "unit_cost": 100, "client_ref": "offline-uuid-123",
    }
    r1 = client.post("/movements", json=body, headers=_auth(1))
    r2 = client.post("/movements", json=body, headers=_auth(1))
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]

    movimentos = client.get("/movements", params={"item_id": item["id"]}, headers=_auth(1)).json()
    assert len(movimentos) == 1


def test_ajuste_negativo_reduz_stock_ao_cmp_corrente(client):
    wh, item = _setup_warehouse_and_item(client)
    client.post("/movements", json={
        "movement_type": "entrada", "item_id": item["id"], "warehouse_id": wh["id"],
        "quantity": 10, "unit_cost": 50,
    }, headers=_auth(1))

    r = client.post("/movements", json={
        "movement_type": "ajuste", "item_id": item["id"], "warehouse_id": wh["id"], "quantity": -3,
    }, headers=_auth(1))
    assert r.status_code == 201
    assert float(r.json()["total_cost_aoa"]) == 150  # 3 * 50


def test_fifo_por_lotes_usa_custo_dos_lotes_consumidos(client):
    wh, item = _setup_warehouse_and_item(client, track_batches=True)
    client.post("/movements", json={
        "movement_type": "entrada", "item_id": item["id"], "warehouse_id": wh["id"],
        "quantity": 5, "unit_cost": 100, "batch_number": "L1",
    }, headers=_auth(1))
    client.post("/movements", json={
        "movement_type": "entrada", "item_id": item["id"], "warehouse_id": wh["id"],
        "quantity": 5, "unit_cost": 200, "batch_number": "L2",
    }, headers=_auth(1))

    r = client.post("/movements", json={
        "movement_type": "saida", "item_id": item["id"], "warehouse_id": wh["id"], "quantity": 7,
    }, headers=_auth(1))
    assert r.status_code == 201
    # 5 unidades @100 (lote L1) + 2 @200 (lote L2) = 900 / 7
    assert round(float(r.json()["total_cost_aoa"]), 2) == 900.0
