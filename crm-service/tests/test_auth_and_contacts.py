"""Testes de segurança (JWT) e dos fluxos de contactos/anexos do crm-service."""
import io
from datetime import datetime, timedelta, timezone

from jose import jwt as jose_jwt

import app as app_module


def _token(sub="user-1", token_type="access", secret=None, expires_delta=timedelta(minutes=15),
           jti=None, company_id=1):
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "type": token_type, "iat": now, "exp": now + expires_delta, "company_id": company_id}
    if jti:
        payload["jti"] = jti
    return jose_jwt.encode(payload, secret or app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Segurança / JWT
# ---------------------------------------------------------------------------

def test_sem_token_e_rejeitado(client):
    resp = client.get("/contacts")
    assert resp.status_code == 401


def test_token_invalido_e_rejeitado(client):
    resp = client.get("/contacts", headers=_auth("token-invalido"))
    assert resp.status_code == 401


def test_token_com_segredo_errado_e_rejeitado(client):
    token = _token(secret="segredo-errado")
    resp = client.get("/contacts", headers=_auth(token))
    assert resp.status_code == 401


def test_token_expirado_e_rejeitado(client):
    token = _token(expires_delta=timedelta(minutes=-1))
    resp = client.get("/contacts", headers=_auth(token))
    assert resp.status_code == 401


def test_refresh_token_nao_serve_como_access_token(client):
    token = _token(token_type="refresh")
    resp = client.get("/contacts", headers=_auth(token))
    assert resp.status_code == 401


def test_token_revogado_e_rejeitado(client, fake_db):
    fake_db.blocklist["jti-revogado"] = True
    token = _token(jti="jti-revogado")
    resp = client.get("/contacts", headers=_auth(token))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token revogado"


# ---------------------------------------------------------------------------
# Contactos (CRUD)
# ---------------------------------------------------------------------------

def _create_contact(client, **overrides):
    body = {"name": "Empresa X", "email": "x@x.com", "stage": "novo", "pipeline_value": 1000.0}
    body.update(overrides)
    return client.post("/contacts", json=body, headers=_auth(_token()))


def test_criar_e_listar_contacto(client):
    created = _create_contact(client)
    assert created.status_code == 201
    contact = created.json()
    assert contact["name"] == "Empresa X"

    listed = client.get("/contacts", headers=_auth(_token()))
    assert listed.status_code == 200
    assert any(c["id"] == contact["id"] for c in listed.json())


def test_obter_contacto_inexistente_devolve_404(client):
    resp = client.get("/contacts/9999", headers=_auth(_token()))
    assert resp.status_code == 404


def test_atualizar_contacto(client):
    contact = _create_contact(client).json()
    resp = client.put(f"/contacts/{contact['id']}", json={
        "name": "Empresa X Renomeada", "stage": "negociação", "pipeline_value": 2000.0,
    }, headers=_auth(_token()))
    assert resp.status_code == 200
    assert resp.json()["name"] == "Empresa X Renomeada"
    assert resp.json()["stage"] == "negociação"


def test_atualizar_stage_do_contacto(client):
    contact = _create_contact(client).json()
    resp = client.patch(f"/contacts/{contact['id']}/stage", json={"stage": "ganho"}, headers=_auth(_token()))
    assert resp.status_code == 200
    assert resp.json()["stage"] == "ganho"


def test_atualizar_stage_sem_campo_stage_e_rejeitado(client):
    contact = _create_contact(client).json()
    resp = client.patch(f"/contacts/{contact['id']}/stage", json={}, headers=_auth(_token()))
    assert resp.status_code == 400


def test_apagar_contacto(client):
    contact = _create_contact(client).json()
    resp = client.delete(f"/contacts/{contact['id']}", headers=_auth(_token()))
    assert resp.status_code == 204
    assert client.get(f"/contacts/{contact['id']}", headers=_auth(_token())).status_code == 404


# ---------------------------------------------------------------------------
# Anexos
# ---------------------------------------------------------------------------

def test_upload_e_listagem_de_anexos(client):
    contact = _create_contact(client).json()
    resp = client.post(
        f"/contacts/{contact['id']}/attachments",
        files={"file": ("contrato.pdf", io.BytesIO(b"conteudo"), "application/pdf")},
        headers=_auth(_token()),
    )
    assert resp.status_code == 201
    assert resp.json()["filename"] == "contrato.pdf"

    listagem = client.get(f"/contacts/{contact['id']}/attachments", headers=_auth(_token()))
    assert listagem.status_code == 200
    attachments = listagem.json()
    assert len(attachments) == 1
    assert attachments[0]["url"].startswith("https://minio.local/")
