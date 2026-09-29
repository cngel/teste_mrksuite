"""
Testes do proxy /api/{serviço}/... do web-service — a camada que decide, com
base nos módulos do JWT, se um pedido pode ser reencaminhado para o
microserviço de dados correspondente. É aqui que se aplica o controlo de
acesso por módulo (RBAC) entre o browser e os serviços internos.
"""
from datetime import datetime, timedelta, timezone

from jose import jwt as jose_jwt

import app as app_module


def _token(modules=None, is_admin=False, secret=None, expires_delta=timedelta(minutes=15)):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "user-1", "type": "access", "iat": now, "exp": now + expires_delta,
        "is_admin": is_admin, "modules": modules or [],
    }
    return jose_jwt.encode(payload, secret or app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Roteamento e serviços desconhecidos
# ---------------------------------------------------------------------------

def test_servico_desconhecido_devolve_404(client):
    resp = client.get("/api/inexistente/qualquer-coisa")
    assert resp.status_code == 404


def test_upstream_indisponivel_devolve_502(client, upstream):
    upstream.raise_connect_error = True
    resp = client.get("/api/auth/me", headers=_auth(_token()))
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Controlo de acesso por módulo
# ---------------------------------------------------------------------------

def test_sem_token_e_reencaminhado_sem_ser_bloqueado_pelo_proxy(client, upstream):
    """Pedidos sem token válido passam para o serviço de destino, que é quem
    decide 401 — o proxy só corta com 403 quando o token é válido mas falta
    o módulo certo."""
    resp = client.get("/api/rh/employees")
    assert resp.status_code == 200
    assert len(upstream.calls) == 1


def test_utilizador_sem_modulo_atribuido_e_bloqueado_com_403(client, upstream):
    token = _token(modules=["crm"])
    resp = client.get("/api/rh/employees", headers=_auth(token))
    assert resp.status_code == 403
    assert len(upstream.calls) == 0


def test_utilizador_com_modulo_atribuido_e_reencaminhado(client, upstream):
    token = _token(modules=["people"])
    resp = client.get("/api/rh/employees", headers=_auth(token))
    assert resp.status_code == 200
    assert len(upstream.calls) == 1
    assert upstream.calls[0]["url"] == "http://rh-service:5003/employees"


def test_admin_acede_a_qualquer_modulo_mesmo_sem_o_ter_atribuido(client, upstream):
    token = _token(modules=[], is_admin=True)
    resp = client.get("/api/finance/invoices", headers=_auth(token))
    assert resp.status_code == 200
    assert len(upstream.calls) == 1


def test_documents_aceita_qualquer_um_dos_tres_modulos(client, upstream):
    for modulo in ("people", "books", "projects"):
        upstream.calls.clear()
        token = _token(modules=[modulo])
        resp = client.get("/api/documents/folders", headers=_auth(token))
        assert resp.status_code == 200, f"módulo {modulo} devia dar acesso a documents"


def test_documents_sem_nenhum_dos_tres_modulos_e_bloqueado(client, upstream):
    token = _token(modules=["crm"])
    resp = client.get("/api/documents/folders", headers=_auth(token))
    assert resp.status_code == 403


def test_servico_sem_modulo_obrigatorio_e_sempre_reencaminhado(client, upstream):
    """auth não está em SERVICE_REQUIRED_MODULES — não há verificação de módulo."""
    token = _token(modules=[])
    resp = client.post("/api/auth/login", json={"email": "x@x.com", "senha": "y"}, headers=_auth(token))
    assert resp.status_code == 200


def test_token_invalido_e_tratado_como_sem_claims_e_reencaminhado(client, upstream):
    resp = client.get("/api/rh/employees", headers=_auth("token-corrompido"))
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Encaminhamento de cabeçalhos e corpo
# ---------------------------------------------------------------------------

def test_cabecalhos_hop_by_hop_nao_sao_reencaminhados_ao_upstream(client, upstream):
    token = _token(modules=["people"])
    client.get("/api/rh/employees", headers=_auth(token))
    forwarded_headers = {k.lower() for k in upstream.calls[0]["headers"].keys()}
    assert "host" not in forwarded_headers
    assert "content-length" not in forwarded_headers


def test_query_params_sao_reencaminhados(client, upstream):
    token = _token(modules=["people"])
    client.get("/api/rh/employees?department_id=5", headers=_auth(token))
    assert ("department_id", "5") in upstream.calls[0]["params"]


# ---------------------------------------------------------------------------
# Páginas estáticas / redirecionamentos
# ---------------------------------------------------------------------------

def test_pagina_de_login_responde_200(client):
    assert client.get("/login").status_code == 200


def test_redirecionamento_de_url_antiga_com_html(client):
    resp = client.get("/dashboard.html", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/dashboard"
