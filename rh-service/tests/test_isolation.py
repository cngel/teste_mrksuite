"""Testes de isolamento entre empresas (multi-tenant) do rh-service: um JWT
válido de uma empresa nunca deve dar acesso a funcionários, departamentos,
folhas salariais ou regras de dedução de outra empresa."""
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
    resp = client.get("/employees", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_funcionarios_nao_aparecem_na_listagem_de_outra_empresa(client):
    client.post("/employees", json={"full_name": "Ana", "email": "ana@empresa1.com"}, headers=_auth(1))
    client.post("/employees", json={"full_name": "Bruno", "email": "bruno@empresa2.com"}, headers=_auth(2))

    listagem = client.get("/employees", headers=_auth(1)).json()
    assert len(listagem) == 1
    assert listagem[0]["email"] == "ana@empresa1.com"


def test_nao_acede_a_funcionario_de_outra_empresa(client):
    emp = client.post("/employees", json={"full_name": "Confidencial", "email": "c@x.com"}, headers=_auth(1)).json()
    assert client.get(f"/employees/{emp['id']}", headers=_auth(2)).status_code == 404


def test_nao_edita_funcionario_de_outra_empresa(client):
    emp = client.post("/employees", json={"full_name": "Original", "email": "o@x.com"}, headers=_auth(1)).json()
    resp = client.put(f"/employees/{emp['id']}", json={"full_name": "Hackeado", "email": "o@x.com"}, headers=_auth(2))
    assert resp.status_code == 404


def test_nao_ve_departamento_de_outra_empresa(client):
    dept = client.post("/departments", json={"name": "Confidencial"}, headers=_auth(1)).json()
    resp = client.put(f"/departments/{dept['id']}", json={"name": "Hackeado"}, headers=_auth(2))
    assert resp.status_code == 404


def test_nao_cria_contrato_para_funcionario_de_outra_empresa(client):
    emp = client.post("/employees", json={"full_name": "Alvo", "email": "alvo@x.com"}, headers=_auth(1)).json()
    resp = client.post("/contracts", json={
        "employee_id": emp["id"], "contract_type": "efetivo", "start_date": "2026-01-01", "base_salary": 100000,
    }, headers=_auth(2))
    assert resp.status_code == 404


def test_nao_ve_folha_salarial_de_outra_empresa(client):
    emp = client.post("/employees", json={"full_name": "Eva", "email": "eva@x.com"}, headers=_auth(1)).json()
    client.post(f"/employees/{emp['id']}/salary-profile", json={"base_salary": 200000}, headers=_auth(1))
    payroll = client.post(
        f"/employees/{emp['id']}/payrolls/generate", json={"month": 1, "year": 2026}, headers=_auth(1),
    ).json()["payroll"]

    assert client.get(f"/payrolls/{payroll['id']}", headers=_auth(2)).status_code == 404
    assert client.post(f"/payrolls/{payroll['id']}/approve", headers=_auth(2)).status_code == 404


def test_regra_de_deducao_de_uma_empresa_nao_e_usada_na_folha_de_outra(client):
    client.post("/deduction-rules", json={
        "name": "INSS Empresa 1", "calculation_type": "percentage", "value": 3,
    }, headers=_auth(1))

    emp2 = client.post("/employees", json={"full_name": "Fábio", "email": "fabio@x.com"}, headers=_auth(2)).json()
    client.post(f"/employees/{emp2['id']}/salary-profile", json={"base_salary": 100000}, headers=_auth(2))
    payroll = client.post(
        f"/employees/{emp2['id']}/payrolls/generate", json={"month": 2, "year": 2026}, headers=_auth(2),
    ).json()["payroll"]

    # Sem regras de dedução na empresa 2, o líquido tem de ser igual ao bruto —
    # a regra da empresa 1 não pode ter sido aplicada.
    assert payroll["net_salary"] == payroll["gross_salary"]


def test_nao_ve_regra_de_deducao_de_outra_empresa(client):
    rule = client.post("/deduction-rules", json={
        "name": "Regra Confidencial", "calculation_type": "fixed", "value": 100,
    }, headers=_auth(1)).json()
    assert client.get(f"/deduction-rules/{rule['id']}", headers=_auth(2)).status_code == 404


def test_nao_reatribui_avaliacao_a_funcionario_de_outra_empresa(client):
    """Regressão: PUT /evaluations/{id} aceitava um employee_id do corpo do
    pedido e gravava-o sem validar que pertence à empresa do utilizador —
    ao contrário do POST /evaluations, que já validava via _require_employee."""
    emp1 = client.post("/employees", json={"full_name": "Da Empresa 1", "email": "e1@x.com"}, headers=_auth(1)).json()
    emp2 = client.post("/employees", json={"full_name": "Da Empresa 2", "email": "e2@x.com"}, headers=_auth(2)).json()
    evaluation = client.post("/evaluations", json={
        "employee_id": emp1["id"], "eval_date": "2026-01-01", "rating": 5,
    }, headers=_auth(1)).json()

    resp = client.put(f"/evaluations/{evaluation['id']}", json={
        "employee_id": emp2["id"], "eval_date": "2026-01-01", "rating": 1,
    }, headers=_auth(1))
    assert resp.status_code == 404


def test_nao_reatribui_formacao_a_funcionario_de_outra_empresa(client):
    emp1 = client.post("/employees", json={"full_name": "Da Empresa 1", "email": "t1@x.com"}, headers=_auth(1)).json()
    emp2 = client.post("/employees", json={"full_name": "Da Empresa 2", "email": "t2@x.com"}, headers=_auth(2)).json()
    training = client.post("/trainings", json={
        "employee_id": emp1["id"], "title": "Formação Original",
    }, headers=_auth(1)).json()

    resp = client.put(f"/trainings/{training['id']}", json={
        "employee_id": emp2["id"], "title": "Formação Hackeada",
    }, headers=_auth(1))
    assert resp.status_code == 404
