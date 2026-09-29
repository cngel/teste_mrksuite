"""Testes de isolamento entre empresas (multi-tenant) do stock-service: um JWT
válido de uma empresa nunca deve dar acesso a armazéns, artigos ou movimentos
de outra empresa."""
from datetime import datetime, timedelta, timezone

from jose import jwt as jose_jwt

import app as app_module


def _auth(company_id, sub="user-1"):
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "type": "access", "iat": now, "exp": now + timedelta(minutes=15), "company_id": company_id}
    token = jose_jwt.encode(payload, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


def test_sem_company_id_no_token_e_rejeitado(client):
    now = datetime.now(timezone.utc)
    payload = {"sub": "user-1", "type": "access", "iat": now, "exp": now + timedelta(minutes=15)}
    token = jose_jwt.encode(payload, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)
    resp = client.get("/warehouses", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_armazens_nao_aparecem_na_listagem_de_outra_empresa(client):
    client.post("/warehouses", json={"name": "Armazém Empresa 1"}, headers=_auth(1))
    client.post("/warehouses", json={"name": "Armazém Empresa 2"}, headers=_auth(2))

    listagem = client.get("/warehouses", headers=_auth(1)).json()
    assert len(listagem) == 1
    assert listagem[0]["name"] == "Armazém Empresa 1"


def test_nao_acede_a_artigo_de_outra_empresa(client):
    item = client.post("/items", json={"name": "Confidencial"}, headers=_auth(1)).json()
    assert client.get(f"/items/{item['id']}", headers=_auth(2)).status_code == 404


def test_nao_cria_movimento_com_armazem_de_outra_empresa(client):
    wh = client.post("/warehouses", json={"name": "Armazém Empresa 1"}, headers=_auth(1)).json()
    item = client.post("/items", json={"name": "Artigo Empresa 2"}, headers=_auth(2)).json()

    resp = client.post("/movements", json={
        "movement_type": "entrada", "item_id": item["id"], "warehouse_id": wh["id"],
        "quantity": 10, "unit_cost": 100,
    }, headers=_auth(2))
    assert resp.status_code == 404


def test_movimentos_nao_se_misturam_entre_empresas(client):
    wh1 = client.post("/warehouses", json={"name": "Armazém 1"}, headers=_auth(1)).json()
    item1 = client.post("/items", json={"name": "Artigo 1"}, headers=_auth(1)).json()
    client.post("/movements", json={
        "movement_type": "entrada", "item_id": item1["id"], "warehouse_id": wh1["id"],
        "quantity": 10, "unit_cost": 100,
    }, headers=_auth(1))

    wh2 = client.post("/warehouses", json={"name": "Armazém 2"}, headers=_auth(2)).json()
    item2 = client.post("/items", json={"name": "Artigo 2"}, headers=_auth(2)).json()
    client.post("/movements", json={
        "movement_type": "entrada", "item_id": item2["id"], "warehouse_id": wh2["id"],
        "quantity": 5, "unit_cost": 50,
    }, headers=_auth(2))

    movimentos_empresa_1 = client.get("/movements", headers=_auth(1)).json()
    assert len(movimentos_empresa_1) == 1
    assert movimentos_empresa_1[0]["item_id"] == item1["id"]
