"""Testes de segurança (JWT) e das regras de negócio do finance-service:
numeração sequencial de documentos, cálculo de totais, e máquinas de estado
de facturas/despesas."""
from datetime import datetime, timedelta, timezone

from jose import jwt as jose_jwt

import app as app_module


def _token(sub="user-1", token_type="access", secret=None, expires_delta=timedelta(minutes=15), company_id=1):
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "type": token_type, "iat": now, "exp": now + expires_delta, "company_id": company_id}
    return jose_jwt.encode(payload, secret or app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)


def _auth(token=None):
    return {"Authorization": f"Bearer {token or _token()}"}


# ---------------------------------------------------------------------------
# Segurança / JWT
# ---------------------------------------------------------------------------

def test_sem_token_e_rejeitado(client):
    assert client.get("/invoices").status_code == 401


def test_token_invalido_e_rejeitado(client):
    assert client.get("/invoices", headers=_auth("lixo")).status_code == 401


def test_token_expirado_e_rejeitado(client):
    token = _token(expires_delta=timedelta(minutes=-1))
    assert client.get("/invoices", headers=_auth(token)).status_code == 401


def test_refresh_token_nao_serve_como_access_token(client):
    token = _token(token_type="refresh")
    assert client.get("/invoices", headers=_auth(token)).status_code == 401


# ---------------------------------------------------------------------------
# Facturas: cálculo de totais e numeração sequencial
# ---------------------------------------------------------------------------

def _create_invoice(client, **overrides):
    body = {"client_name": "Cliente A", "subtotal": 100.0, "iva_rate": 14}
    body.update(overrides)
    return client.post("/invoices", json=body, headers=_auth())


def test_criar_factura_calcula_iva_e_total_corretamente(client):
    resp = _create_invoice(client, subtotal=100.0, iva_rate=14)
    assert resp.status_code == 201
    body = resp.json()
    assert body["iva_amount"] == 14.0
    assert body["total"] == 114.0
    assert body["doc_number"] == "FAT-000001"


def test_numeracao_de_facturas_e_sequencial_e_imutavel(client):
    first = _create_invoice(client).json()
    second = _create_invoice(client).json()
    assert first["doc_number"] == "FAT-000001"
    assert second["doc_number"] == "FAT-000002"


def test_obter_factura_inexistente_devolve_404(client):
    assert client.get("/invoices/9999", headers=_auth()).status_code == 404


def test_anular_factura_sem_recibos_e_permitido(client):
    invoice = _create_invoice(client).json()
    resp = client.patch(f"/invoices/{invoice['id']}/cancel", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["status"] == "anulada"


def test_anular_factura_com_recibos_emitidos_e_rejeitado(client):
    invoice = _create_invoice(client, subtotal=100.0, iva_rate=0).json()
    client.post(f"/invoices/{invoice['id']}/receipts", json={"amount": 50.0}, headers=_auth())
    resp = client.patch(f"/invoices/{invoice['id']}/cancel", headers=_auth())
    assert resp.status_code == 409


def test_apagar_factura_com_recibos_emitidos_e_rejeitado(client):
    invoice = _create_invoice(client, subtotal=100.0, iva_rate=0).json()
    client.post(f"/invoices/{invoice['id']}/receipts", json={"amount": 100.0}, headers=_auth())
    resp = client.delete(f"/invoices/{invoice['id']}", headers=_auth())
    assert resp.status_code == 409


def test_apagar_factura_sem_recibos_e_permitido(client):
    invoice = _create_invoice(client).json()
    resp = client.delete(f"/invoices/{invoice['id']}", headers=_auth())
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Recibos: pagamento parcial vs total, numeração e factura anulada
# ---------------------------------------------------------------------------

def test_recibo_parcial_deixa_factura_no_estado_parcial(client):
    invoice = _create_invoice(client, subtotal=100.0, iva_rate=0).json()  # total = 100
    resp = client.post(f"/invoices/{invoice['id']}/receipts", json={"amount": 40.0}, headers=_auth())
    assert resp.status_code == 201
    assert resp.json()["doc_number"] == "REC-000001"

    updated_invoice = client.get(f"/invoices/{invoice['id']}", headers=_auth()).json()
    assert updated_invoice["status"] == "parcial"
    assert updated_invoice["paid_amount"] == 40.0


def test_recibo_que_cobre_o_total_marca_factura_como_paga(client):
    invoice = _create_invoice(client, subtotal=100.0, iva_rate=0).json()
    client.post(f"/invoices/{invoice['id']}/receipts", json={"amount": 100.0}, headers=_auth())
    updated_invoice = client.get(f"/invoices/{invoice['id']}", headers=_auth()).json()
    assert updated_invoice["status"] == "paga"


def test_recibo_em_factura_anulada_e_rejeitado(client):
    invoice = _create_invoice(client).json()
    client.patch(f"/invoices/{invoice['id']}/cancel", headers=_auth())
    resp = client.post(f"/invoices/{invoice['id']}/receipts", json={"amount": 10.0}, headers=_auth())
    assert resp.status_code == 409


def test_recibo_em_factura_inexistente_devolve_404(client):
    resp = client.post("/invoices/9999/receipts", json={"amount": 10.0}, headers=_auth())
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Despesas: aprovação hierárquica (pendente -> aprovada/rejeitada -> paga)
# ---------------------------------------------------------------------------

def _create_expense(client, **overrides):
    body = {"description": "Compra de material", "amount": 250.0}
    body.update(overrides)
    return client.post("/expenses", json=body, headers=_auth())


def test_despesa_e_criada_como_pendente(client):
    resp = _create_expense(client)
    assert resp.status_code == 201
    assert resp.json()["status"] == "pendente"


def test_aprovar_despesa_pendente(client):
    expense = _create_expense(client).json()
    resp = client.patch(f"/expenses/{expense['id']}/approve", json={}, headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["status"] == "aprovada"


def test_aprovar_despesa_ja_decidida_e_rejeitado(client):
    expense = _create_expense(client).json()
    client.patch(f"/expenses/{expense['id']}/approve", json={}, headers=_auth())
    resp = client.patch(f"/expenses/{expense['id']}/approve", json={}, headers=_auth())
    assert resp.status_code == 404


def test_pagar_despesa_nao_aprovada_e_rejeitado(client):
    expense = _create_expense(client).json()
    resp = client.patch(f"/expenses/{expense['id']}/pay", headers=_auth())
    assert resp.status_code == 404


def test_pagar_despesa_aprovada(client):
    expense = _create_expense(client).json()
    client.patch(f"/expenses/{expense['id']}/approve", json={}, headers=_auth())
    resp = client.patch(f"/expenses/{expense['id']}/pay", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["status"] == "paga"


def test_apagar_despesa_ja_aprovada_nao_tem_efeito(client):
    expense = _create_expense(client).json()
    client.patch(f"/expenses/{expense['id']}/approve", json={}, headers=_auth())
    client.delete(f"/expenses/{expense['id']}", headers=_auth())
    # a despesa aprovada não é apagada — só despesas 'pendente' são elegíveis
    listed = client.get("/expenses", headers=_auth()).json()
    assert any(e["id"] == expense["id"] for e in listed)


def test_rejeitar_despesa_pendente(client):
    expense = _create_expense(client).json()
    resp = client.patch(f"/expenses/{expense['id']}/reject", json={"approval_note": "Sem orçamento"}, headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejeitada"


# ---------------------------------------------------------------------------
# Fornecedores
# ---------------------------------------------------------------------------

def test_apagar_fornecedor_com_despesas_associadas_e_rejeitado(client):
    supplier = client.post("/suppliers", json={"name": "Fornecedor X"}, headers=_auth()).json()
    _create_expense(client, supplier_id=supplier["id"])
    resp = client.delete(f"/suppliers/{supplier['id']}", headers=_auth())
    assert resp.status_code == 409


def test_apagar_fornecedor_sem_despesas_e_permitido(client):
    supplier = client.post("/suppliers", json={"name": "Fornecedor Y"}, headers=_auth()).json()
    resp = client.delete(f"/suppliers/{supplier['id']}", headers=_auth())
    assert resp.status_code == 204


def test_criar_despesa_com_fornecedor_inexistente_devolve_404(client):
    resp = _create_expense(client, supplier_id=9999)
    assert resp.status_code == 404
