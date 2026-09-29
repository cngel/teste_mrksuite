"""Testes de segurança (JWT) e das regras de negócio do accounting-service:
plano de contas, partidas dobradas, numeração sequencial e estorno."""
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
    assert client.get("/accounts").status_code == 401


def test_token_invalido_e_rejeitado(client):
    assert client.get("/accounts", headers=_auth("lixo")).status_code == 401


# ---------------------------------------------------------------------------
# Plano de Contas
# ---------------------------------------------------------------------------

def test_plano_de_contas_e_criado_automaticamente_e_idempotente(client):
    first = client.get("/accounts", headers=_auth()).json()
    second = client.get("/accounts", headers=_auth()).json()
    assert len(first) == len(second)
    codes = [a["code"] for a in first]
    assert "1.1.1" in codes and "4.1" in codes and "5.1" in codes


def test_criar_conta_com_classe_invalida_e_rejeitado(client):
    resp = client.post("/accounts", json={"code": "9.9", "name": "X", "account_class": "invalido"}, headers=_auth())
    assert resp.status_code == 422


def test_criar_conta_com_codigo_duplicado_e_rejeitado(client):
    client.get("/accounts", headers=_auth())  # garante que o plano por omissão foi semeado
    resp = client.post("/accounts", json={"code": "1.1.1", "name": "Duplicada", "account_class": "ativo"}, headers=_auth())
    assert resp.status_code == 409


def test_apagar_conta_de_sistema_e_rejeitado(client):
    accounts = client.get("/accounts", headers=_auth()).json()
    caixa = next(a for a in accounts if a["code"] == "1.1.1")
    resp = client.delete(f"/accounts/{caixa['id']}", headers=_auth())
    assert resp.status_code == 409


def test_apagar_conta_personalizada_nao_usada_e_permitido(client):
    created = client.post("/accounts", json={"code": "9.9", "name": "Temporária", "account_class": "ativo"}, headers=_auth()).json()
    resp = client.delete(f"/accounts/{created['id']}", headers=_auth())
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Lançamentos: partidas dobradas
# ---------------------------------------------------------------------------

def _entry_body(**overrides):
    body = {
        "entry_date": "2026-01-15",
        "description": "Lançamento de teste",
        "lines": [
            {"account_code": "1.1.1", "debit": 100.0, "credit": 0},
            {"account_code": "4.1", "debit": 0, "credit": 100.0},
        ],
    }
    body.update(overrides)
    return body


def test_criar_lancamento_balanceado_e_numeracao_sequencial(client):
    first = client.post("/entries", json=_entry_body(), headers=_auth())
    assert first.status_code == 201
    assert first.json()["doc_number"] == "LC-000001"

    second = client.post("/entries", json=_entry_body(), headers=_auth())
    assert second.json()["doc_number"] == "LC-000002"


def test_lancamento_desequilibrado_e_rejeitado(client):
    body = _entry_body(lines=[
        {"account_code": "1.1.1", "debit": 100.0, "credit": 0},
        {"account_code": "4.1", "debit": 0, "credit": 90.0},
    ])
    resp = client.post("/entries", json=body, headers=_auth())
    assert resp.status_code == 422


def test_linha_com_debito_e_credito_ao_mesmo_tempo_e_rejeitada(client):
    body = _entry_body(lines=[
        {"account_code": "1.1.1", "debit": 100.0, "credit": 100.0},
        {"account_code": "4.1", "debit": 0, "credit": 0},
    ])
    resp = client.post("/entries", json=body, headers=_auth())
    assert resp.status_code == 422


def test_lancamento_com_conta_inexistente_devolve_404(client):
    body = _entry_body(lines=[
        {"account_code": "1.1.1", "debit": 100.0, "credit": 0},
        {"account_id": 9999, "debit": 0, "credit": 100.0},
    ])
    resp = client.post("/entries", json=body, headers=_auth())
    assert resp.status_code == 404


def test_estorno_cria_lancamento_inverso_e_marca_original(client):
    original = client.post("/entries", json=_entry_body(), headers=_auth()).json()
    reversal = client.post(f"/entries/{original['id']}/reverse", headers=_auth())
    assert reversal.status_code == 201
    reversal_body = reversal.json()
    assert reversal_body["doc_number"] == "LC-000002"

    original_after = client.get(f"/entries/{original['id']}", headers=_auth()).json()
    assert original_after["status"] == "estornado"

    caixa_line = next(l for l in reversal_body["lines"] if l["account_code"] == "1.1.1")
    assert caixa_line["credit"] == 100.0 and caixa_line["debit"] == 0


def test_estornar_lancamento_ja_estornado_e_rejeitado(client):
    original = client.post("/entries", json=_entry_body(), headers=_auth()).json()
    client.post(f"/entries/{original['id']}/reverse", headers=_auth())
    resp = client.post(f"/entries/{original['id']}/reverse", headers=_auth())
    assert resp.status_code == 409


def test_nao_existe_endpoint_de_apagar_lancamento(client):
    original = client.post("/entries", json=_entry_body(), headers=_auth()).json()
    resp = client.delete(f"/entries/{original['id']}", headers=_auth())
    assert resp.status_code in (404, 405)
