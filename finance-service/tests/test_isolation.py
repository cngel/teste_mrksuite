"""Testes de isolamento entre empresas (multi-tenant) do finance-service: um
JWT válido de uma empresa nunca deve dar acesso a faturas, recibos, despesas
ou fornecedores de outra empresa."""
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
    resp = client.get("/invoices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_faturas_nao_aparecem_na_listagem_de_outra_empresa(client):
    client.post("/invoices", json={"client_name": "Cliente 1", "subtotal": 100}, headers=_auth(1))
    client.post("/invoices", json={"client_name": "Cliente 2", "subtotal": 200}, headers=_auth(2))

    listagem = client.get("/invoices", headers=_auth(1)).json()
    assert len(listagem) == 1
    assert listagem[0]["client_name"] == "Cliente 1"


def test_nao_acede_a_fatura_de_outra_empresa(client):
    invoice = client.post("/invoices", json={"client_name": "Confidencial", "subtotal": 100}, headers=_auth(1)).json()
    assert client.get(f"/invoices/{invoice['id']}", headers=_auth(2)).status_code == 404


def test_nao_anula_fatura_de_outra_empresa(client):
    invoice = client.post("/invoices", json={"client_name": "Alvo", "subtotal": 100}, headers=_auth(1)).json()
    resp = client.patch(f"/invoices/{invoice['id']}/cancel", headers=_auth(2))
    assert resp.status_code == 404
    ainda_emitida = client.get(f"/invoices/{invoice['id']}", headers=_auth(1)).json()
    assert ainda_emitida["status"] == "emitida"


def test_nao_cria_recibo_para_fatura_de_outra_empresa(client):
    invoice = client.post("/invoices", json={"client_name": "Alvo", "subtotal": 100}, headers=_auth(1)).json()
    resp = client.post(f"/invoices/{invoice['id']}/receipts", json={"amount": 50}, headers=_auth(2))
    assert resp.status_code == 404


def test_nao_ve_fornecedor_de_outra_empresa(client):
    supplier = client.post("/suppliers", json={"name": "Fornecedor Confidencial"}, headers=_auth(1)).json()
    assert client.get(f"/suppliers/{supplier['id']}", headers=_auth(2)).status_code == 404


def test_nao_cria_despesa_com_fornecedor_de_outra_empresa(client):
    supplier = client.post("/suppliers", json={"name": "Fornecedor Empresa 1"}, headers=_auth(1)).json()
    resp = client.post("/expenses", json={
        "description": "Tentativa cross-tenant", "amount": 100, "supplier_id": supplier["id"],
    }, headers=_auth(2))
    assert resp.status_code == 404


def test_nao_aprova_despesa_de_outra_empresa(client):
    expense = client.post("/expenses", json={"description": "Despesa", "amount": 100}, headers=_auth(1)).json()
    resp = client.patch(f"/expenses/{expense['id']}/approve", json={}, headers=_auth(2))
    assert resp.status_code == 404
    listagem = client.get("/expenses", headers=_auth(1)).json()
    assert listagem[0]["status"] == "pendente"


def test_resumo_financeiro_nao_mistura_dados_entre_empresas(client):
    client.post("/invoices", json={"client_name": "C1", "subtotal": 1000, "iva_rate": 0}, headers=_auth(1))
    client.post("/invoices", json={"client_name": "C2", "subtotal": 5000, "iva_rate": 0}, headers=_auth(2))

    resumo_empresa_1 = client.get("/summary", headers=_auth(1)).json()
    resumo_empresa_2 = client.get("/summary", headers=_auth(2)).json()

    assert resumo_empresa_1["a_receber"] == 1000
    assert resumo_empresa_2["a_receber"] == 5000
