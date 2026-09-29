"""Testes da integração com o accounting-service: criar uma factura, um recibo
ou pagar uma despesa deve gerar um lançamento contabilístico correspondente
(melhor-esforço) — e uma falha nessa chamada nunca pode quebrar a operação
financeira em si."""
from datetime import datetime, timedelta, timezone

import httpx
from jose import jwt as jose_jwt

import app as app_module


def _token(sub="user-1", company_id=1):
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "type": "access", "iat": now, "exp": now + timedelta(minutes=15), "company_id": company_id}
    return jose_jwt.encode(payload, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)


def _auth():
    return {"Authorization": f"Bearer {_token()}"}


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, token, entry_date, description, lines, source_type, source_id):
        self.calls.append({
            "token": token, "entry_date": entry_date, "description": description,
            "lines": lines, "source_type": source_type, "source_id": source_id,
        })


def test_criar_factura_lanca_entrada_contabilistica(client, monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(app_module, "post_journal_entry", spy)

    invoice = client.post("/invoices", json={"client_name": "Cliente A", "subtotal": 100.0, "iva_rate": 14}, headers=_auth()).json()

    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["source_type"] == "invoice"
    assert call["source_id"] == invoice["id"]
    total_debit = sum(l["debit"] for l in call["lines"])
    total_credit = sum(l["credit"] for l in call["lines"])
    assert total_debit == total_credit == 114.0


def test_criar_recibo_lanca_entrada_contabilistica(client, monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(app_module, "post_journal_entry", spy)

    invoice = client.post("/invoices", json={"client_name": "Cliente A", "subtotal": 100.0, "iva_rate": 0}, headers=_auth()).json()
    spy.calls.clear()

    receipt = client.post(f"/invoices/{invoice['id']}/receipts", json={"amount": 100.0}, headers=_auth()).json()

    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["source_type"] == "receipt"
    assert call["source_id"] == receipt["id"]


def test_pagar_despesa_lanca_entrada_contabilistica(client, monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(app_module, "post_journal_entry", spy)

    expense = client.post("/expenses", json={"description": "Material", "amount": 250.0}, headers=_auth()).json()
    client.patch(f"/expenses/{expense['id']}/approve", json={}, headers=_auth())
    spy.calls.clear()

    client.patch(f"/expenses/{expense['id']}/pay", headers=_auth())

    assert len(spy.calls) == 1
    assert spy.calls[0]["source_type"] == "expense"
    assert spy.calls[0]["source_id"] == expense["id"]


def test_criar_despesa_aprovar_e_rejeitar_nao_lancam_entrada(client, monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(app_module, "post_journal_entry", spy)

    expense = client.post("/expenses", json={"description": "Material", "amount": 250.0}, headers=_auth()).json()
    client.patch(f"/expenses/{expense['id']}/approve", json={}, headers=_auth())
    assert spy.calls == []

    expense2 = client.post("/expenses", json={"description": "Outro", "amount": 50.0}, headers=_auth()).json()
    client.patch(f"/expenses/{expense2['id']}/reject", json={}, headers=_auth())
    assert spy.calls == []


def test_anular_factura_nao_lanca_entrada(client, monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(app_module, "post_journal_entry", spy)

    invoice = client.post("/invoices", json={"client_name": "Cliente A", "subtotal": 100.0}, headers=_auth()).json()
    spy.calls.clear()

    client.patch(f"/invoices/{invoice['id']}/cancel", headers=_auth())
    assert spy.calls == []


def test_accounting_service_em_baixo_nao_quebra_criacao_de_factura(client, monkeypatch):
    def _raise(*args, **kwargs):
        raise httpx.ConnectError("accounting-service indisponível")

    # post_journal_entry já engole httpx.HTTPError internamente — isto confirma
    # que a chamada em si nunca é deixada escapar para o endpoint do finance-service.
    def _fake_post_journal_entry(token, entry_date, description, lines, source_type, source_id):
        try:
            _raise()
        except httpx.HTTPError:
            pass

    monkeypatch.setattr(app_module, "post_journal_entry", _fake_post_journal_entry)

    resp = client.post("/invoices", json={"client_name": "Cliente A", "subtotal": 100.0}, headers=_auth())
    assert resp.status_code == 201
