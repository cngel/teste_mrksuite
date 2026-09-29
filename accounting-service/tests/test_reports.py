"""Testes de correcção matemática do livro razão, balancete, DRE e balanço
patrimonial do accounting-service."""
from datetime import datetime, timedelta, timezone

from jose import jwt as jose_jwt

import app as app_module


def _token(sub="user-1", company_id=1):
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "type": "access", "iat": now, "exp": now + timedelta(minutes=15), "company_id": company_id}
    return jose_jwt.encode(payload, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)


def _auth(company_id=1):
    return {"Authorization": f"Bearer {_token(company_id=company_id)}"}


def _post_entry(client, entry_date, description, lines):
    resp = client.post("/entries", json={"entry_date": entry_date, "description": description, "lines": lines}, headers=_auth())
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_two_entries(client):
    # Venda de serviços: Cliente deve 1140 (1000 receita + 140 IVA)
    _post_entry(client, "2026-01-10", "Factura 1", [
        {"account_code": "1.1.3", "debit": 1140.0, "credit": 0},
        {"account_code": "4.1", "debit": 0, "credit": 1000.0},
        {"account_code": "2.1.2", "debit": 0, "credit": 140.0},
    ])
    # Recebimento: Banco recebe 1140, Cliente fica a zero
    _post_entry(client, "2026-01-20", "Recibo 1", [
        {"account_code": "1.1.2", "debit": 1140.0, "credit": 0},
        {"account_code": "1.1.3", "debit": 0, "credit": 1140.0},
    ])


def test_livro_razao_calcula_saldo_acumulado_correto(client):
    client.get("/accounts", headers=_auth())
    accounts = client.get("/accounts", headers=_auth()).json()
    clientes = next(a for a in accounts if a["code"] == "1.1.3")

    _seed_two_entries(client)

    ledger = client.get(f"/ledger/{clientes['id']}", headers=_auth()).json()
    assert len(ledger["movements"]) == 2
    assert ledger["movements"][0]["running_balance"] == 1140.0
    assert ledger["movements"][1]["running_balance"] == 0.0
    assert ledger["closing_balance"] == 0.0


def test_balancete_debito_e_credito_totais_batem_certo(client):
    _seed_two_entries(client)
    balancete = client.get("/balancete", headers=_auth()).json()
    assert balancete["balanced"] is True
    assert balancete["total_debit"] == balancete["total_credit"]
    assert balancete["total_debit"] == 2280.0  # 1140 + 1140


def test_dre_calcula_resultado_liquido_e_filtra_por_periodo(client):
    _seed_two_entries(client)

    dre = client.get("/dre", params={"date_from": "2026-01-01", "date_to": "2026-01-31"}, headers=_auth()).json()
    assert dre["total_receitas"] == 1000.0
    assert dre["total_despesas"] == 0.0
    assert dre["resultado_liquido"] == 1000.0

    dre_fora = client.get("/dre", params={"date_from": "2026-02-01", "date_to": "2026-02-28"}, headers=_auth()).json()
    assert dre_fora["total_receitas"] == 0.0
    assert dre_fora["resultado_liquido"] == 0.0


def test_balanco_ativo_igual_passivo_mais_patrimonio(client):
    _seed_two_entries(client)

    balanco = client.get("/balanco", params={"as_of": "2026-01-31"}, headers=_auth()).json()
    assert balanco["balanceado"] is True
    assert balanco["total_ativo"] == round(balanco["total_passivo"] + balanco["total_patrimonio"], 2)
    assert balanco["total_ativo"] == 1140.0
    assert balanco["total_passivo"] == 140.0
    # O resultado do período (receita 1000 - despesa 0) entra como componente
    # implícito do Património Líquido, pois ainda não foi apurado formalmente.
    assert balanco["total_patrimonio"] == 1000.0

    balanco_antes = client.get("/balanco", params={"as_of": "2026-01-05"}, headers=_auth()).json()
    assert balanco_antes["total_ativo"] == 0.0
    assert balanco_antes["balanceado"] is True
