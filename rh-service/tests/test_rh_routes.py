"""Testes de segurança (JWT) e das regras de negócio das rotas do rh-service:
departamentos, funcionários, perfis salariais, regras de dedução e geração de
folhas salariais."""
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
    assert client.get("/employees").status_code == 401


def test_token_expirado_e_rejeitado(client):
    token = _token(expires_delta=timedelta(minutes=-1))
    assert client.get("/employees", headers=_auth(token)).status_code == 401


def test_refresh_token_nao_serve_como_access_token(client):
    token = _token(token_type="refresh")
    assert client.get("/employees", headers=_auth(token)).status_code == 401


# ---------------------------------------------------------------------------
# Departamentos
# ---------------------------------------------------------------------------

def test_criar_e_listar_departamento(client):
    created = client.post("/departments", json={"name": "Comercial"}, headers=_auth())
    assert created.status_code == 201
    listed = client.get("/departments", headers=_auth())
    assert any(d["id"] == created.json()["id"] for d in listed.json())


def test_apagar_departamento_com_funcionarios_e_rejeitado(client):
    dept = client.post("/departments", json={"name": "TI"}, headers=_auth()).json()
    client.post("/employees", json={"full_name": "Ana", "email": "ana@x.com", "department_id": dept["id"]},
                headers=_auth())
    resp = client.delete(f"/departments/{dept['id']}", headers=_auth())
    assert resp.status_code == 409


def test_apagar_departamento_sem_funcionarios_e_permitido(client):
    dept = client.post("/departments", json={"name": "Vazio"}, headers=_auth()).json()
    assert client.delete(f"/departments/{dept['id']}", headers=_auth()).status_code == 204


# ---------------------------------------------------------------------------
# Funcionários
# ---------------------------------------------------------------------------

def test_criar_funcionario_sem_nome_e_rejeitado(client):
    resp = client.post("/employees", json={"email": "semnome@x.com"}, headers=_auth())
    assert resp.status_code == 422


def test_criar_funcionario_sem_email_e_rejeitado(client):
    resp = client.post("/employees", json={"full_name": "Sem Email"}, headers=_auth())
    assert resp.status_code == 422


def test_criar_e_obter_funcionario(client):
    created = client.post("/employees", json={"full_name": "Bruno Silva", "email": "bruno@x.com"}, headers=_auth())
    assert created.status_code == 201
    emp = created.json()
    fetched = client.get(f"/employees/{emp['id']}", headers=_auth())
    assert fetched.status_code == 200
    assert fetched.json()["email"] == "bruno@x.com"


def test_obter_funcionario_inexistente_devolve_404(client):
    assert client.get("/employees/9999", headers=_auth()).status_code == 404


def test_atualizar_estado_do_funcionario(client):
    emp = client.post("/employees", json={"full_name": "Carla", "email": "carla@x.com"}, headers=_auth()).json()
    resp = client.patch(f"/employees/{emp['id']}/status", json={"status": "ausente"}, headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["status"] == "ausente"


# ---------------------------------------------------------------------------
# Perfil salarial
# ---------------------------------------------------------------------------

def test_criar_perfil_salarial_para_funcionario_inexistente_devolve_404(client):
    resp = client.post("/employees/9999/salary-profile", json={"base_salary": 100000}, headers=_auth())
    assert resp.status_code == 404


def test_criar_perfil_salarial_duplicado_e_rejeitado(client):
    emp = client.post("/employees", json={"full_name": "Duda", "email": "duda@x.com"}, headers=_auth()).json()
    first = client.post(f"/employees/{emp['id']}/salary-profile", json={"base_salary": 100000}, headers=_auth())
    assert first.status_code == 201
    second = client.post(f"/employees/{emp['id']}/salary-profile", json={"base_salary": 120000}, headers=_auth())
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# Regras de dedução
# ---------------------------------------------------------------------------

def test_criar_regra_de_deducao_com_tipo_invalido_e_rejeitada(client):
    resp = client.post("/deduction-rules", json={
        "name": "INSS", "calculation_type": "exponencial",
    }, headers=_auth())
    assert resp.status_code == 400


def test_criar_regra_de_deducao_duplicada_no_mesmo_pais_e_rejeitada(client):
    body = {"name": "INSS", "calculation_type": "percentage", "value": 3}
    first = client.post("/deduction-rules", json=body, headers=_auth())
    assert first.status_code == 201
    second = client.post("/deduction-rules", json=body, headers=_auth())
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# Geração de folha salarial
# ---------------------------------------------------------------------------

def _employee_with_salary_profile(client, base_salary=200000):
    emp = client.post("/employees", json={"full_name": "Eva", "email": "eva@x.com"}, headers=_auth()).json()
    client.post(f"/employees/{emp['id']}/salary-profile", json={"base_salary": base_salary}, headers=_auth())
    return emp


def test_gerar_folha_sem_perfil_salarial_devolve_404(client):
    emp = client.post("/employees", json={"full_name": "Fábio", "email": "fabio@x.com"}, headers=_auth()).json()
    resp = client.post(f"/employees/{emp['id']}/payrolls/generate", json={"month": 1, "year": 2026}, headers=_auth())
    assert resp.status_code == 404


def test_gerar_folha_com_perfil_calcula_liquido_corretamente(client):
    emp = _employee_with_salary_profile(client, base_salary=200000)
    resp = client.post(f"/employees/{emp['id']}/payrolls/generate", json={"month": 1, "year": 2026}, headers=_auth())
    assert resp.status_code == 201
    body = resp.json()
    assert body["payroll"]["gross_salary"] == 200000
    assert body["payroll"]["net_salary"] == 200000
    assert body["payroll"]["status"] == "draft"


def test_gerar_folha_duplicada_para_o_mesmo_periodo_e_rejeitada(client):
    emp = _employee_with_salary_profile(client)
    first = client.post(f"/employees/{emp['id']}/payrolls/generate", json={"month": 3, "year": 2026}, headers=_auth())
    assert first.status_code == 201
    second = client.post(f"/employees/{emp['id']}/payrolls/generate", json={"month": 3, "year": 2026}, headers=_auth())
    assert second.status_code == 409


def test_aprovar_folha_ja_aprovada_e_rejeitada(client):
    emp = _employee_with_salary_profile(client)
    payroll = client.post(
        f"/employees/{emp['id']}/payrolls/generate", json={"month": 4, "year": 2026}, headers=_auth(),
    ).json()["payroll"]

    first = client.post(f"/payrolls/{payroll['id']}/approve", headers=_auth())
    assert first.status_code == 200
    assert first.json()["payroll"]["status"] == "approved"

    second = client.post(f"/payrolls/{payroll['id']}/approve", headers=_auth())
    assert second.status_code == 409
