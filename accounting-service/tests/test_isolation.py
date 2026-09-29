"""Testes de isolamento entre empresas (multi-tenant) do accounting-service: um
JWT válido de uma empresa nunca deve dar acesso a contas, lançamentos ou
relatórios de outra empresa."""
from datetime import date, datetime, timedelta, timezone

from jose import jwt as jose_jwt

import app as app_module


def _auth(company_id, sub="user-1"):
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "type": "access", "iat": now, "exp": now + timedelta(minutes=15), "company_id": company_id}
    token = jose_jwt.encode(payload, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


def _entry_body(**overrides):
    body = {
        "entry_date": "2026-01-15",
        "description": "Lançamento",
        "lines": [
            {"account_code": "1.1.1", "debit": 100.0, "credit": 0},
            {"account_code": "4.1", "debit": 0, "credit": 100.0},
        ],
    }
    body.update(overrides)
    return body


def test_sem_company_id_no_token_e_rejeitado(client):
    now = datetime.now(timezone.utc)
    payload = {"sub": "user-1", "type": "access", "iat": now, "exp": now + timedelta(minutes=15)}
    token = jose_jwt.encode(payload, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)
    resp = client.get("/accounts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_planos_de_contas_sao_independentes_por_empresa(client):
    client.get("/accounts", headers=_auth(1))
    client.get("/accounts", headers=_auth(2))
    client.post("/accounts", json={"code": "9.9", "name": "Só da empresa 1", "account_class": "ativo"}, headers=_auth(1))

    contas_2 = client.get("/accounts", headers=_auth(2)).json()
    assert "9.9" not in [a["code"] for a in contas_2]


def test_lancamentos_nao_aparecem_na_listagem_de_outra_empresa(client):
    client.post("/entries", json=_entry_body(description="Empresa 1"), headers=_auth(1))
    client.post("/entries", json=_entry_body(description="Empresa 2"), headers=_auth(2))

    listagem = client.get("/entries", headers=_auth(1)).json()
    assert len(listagem) == 1
    assert listagem[0]["description"] == "Empresa 1"


def test_nao_acede_a_lancamento_de_outra_empresa(client):
    entry = client.post("/entries", json=_entry_body(), headers=_auth(1)).json()
    assert client.get(f"/entries/{entry['id']}", headers=_auth(2)).status_code == 404


def test_nao_reverte_lancamento_de_outra_empresa(client):
    entry = client.post("/entries", json=_entry_body(), headers=_auth(1)).json()
    resp = client.post(f"/entries/{entry['id']}/reverse", headers=_auth(2))
    assert resp.status_code == 404
    ainda_lancado = client.get(f"/entries/{entry['id']}", headers=_auth(1)).json()
    assert ainda_lancado["status"] == "lancado"


def test_nao_apaga_conta_de_outra_empresa(client):
    account = client.post("/accounts", json={"code": "9.9", "name": "Confidencial", "account_class": "ativo"}, headers=_auth(1)).json()
    resp = client.delete(f"/accounts/{account['id']}", headers=_auth(2))
    assert resp.status_code == 404
    ainda_existe = client.get(f"/accounts/{account['id']}", headers=_auth(1))
    assert ainda_existe.status_code == 200


def test_lancamento_nao_usa_conta_de_outra_empresa(client):
    account = client.post("/accounts", json={"code": "9.9", "name": "Só empresa 1", "account_class": "ativo"}, headers=_auth(1)).json()
    body = _entry_body(lines=[
        {"account_id": account["id"], "debit": 50.0, "credit": 0},
        {"account_code": "4.1", "debit": 0, "credit": 50.0},
    ])
    resp = client.post("/entries", json=body, headers=_auth(2))
    assert resp.status_code == 404


def test_relatorios_nao_misturam_dados_entre_empresas(client):
    hoje = date.today().isoformat()
    client.post("/entries", json=_entry_body(entry_date=hoje), headers=_auth(1))
    client.post("/entries", json=_entry_body(entry_date=hoje), headers=_auth(1))
    client.post("/entries", json=_entry_body(entry_date=hoje), headers=_auth(2))

    summary_1 = client.get("/summary", headers=_auth(1)).json()
    summary_2 = client.get("/summary", headers=_auth(2)).json()
    assert summary_1["lancamentos_mes"] == 2
    assert summary_2["lancamentos_mes"] == 1

    balancete_1 = client.get("/balancete", headers=_auth(1)).json()
    balancete_2 = client.get("/balancete", headers=_auth(2)).json()
    assert balancete_1["total_debit"] == 200.0
    assert balancete_2["total_debit"] == 100.0
