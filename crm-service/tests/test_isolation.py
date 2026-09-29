"""Testes de isolamento entre empresas (multi-tenant): um utilizador autenticado
com um JWT válido de uma empresa nunca deve conseguir ver, editar ou apagar
dados de outra empresa, mesmo conhecendo o ID exacto do registo."""
from datetime import datetime, timedelta, timezone

from jose import jwt as jose_jwt

import app as app_module


def _token(company_id, sub="user-1"):
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "type": "access", "iat": now, "exp": now + timedelta(minutes=15), "company_id": company_id}
    return jose_jwt.encode(payload, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)


def _auth(company_id):
    return {"Authorization": f"Bearer {_token(company_id)}"}


def test_sem_company_id_no_token_e_rejeitado(client):
    now = datetime.now(timezone.utc)
    payload = {"sub": "user-1", "type": "access", "iat": now, "exp": now + timedelta(minutes=15)}
    token = jose_jwt.encode(payload, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)
    resp = client.get("/contacts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_utilizador_nao_ve_contactos_de_outra_empresa_na_listagem(client):
    client.post("/contacts", json={"name": "Contacto Empresa 1"}, headers=_auth(company_id=1))
    client.post("/contacts", json={"name": "Contacto Empresa 2"}, headers=_auth(company_id=2))

    listagem_empresa_1 = client.get("/contacts", headers=_auth(company_id=1)).json()
    assert len(listagem_empresa_1) == 1
    assert listagem_empresa_1[0]["name"] == "Contacto Empresa 1"


def test_utilizador_nao_acede_a_contacto_de_outra_empresa_por_id(client):
    contact = client.post("/contacts", json={"name": "Confidencial"}, headers=_auth(company_id=1)).json()
    resp = client.get(f"/contacts/{contact['id']}", headers=_auth(company_id=2))
    assert resp.status_code == 404


def test_utilizador_nao_edita_contacto_de_outra_empresa(client):
    contact = client.post("/contacts", json={"name": "Original"}, headers=_auth(company_id=1)).json()
    resp = client.put(f"/contacts/{contact['id']}", json={"name": "Hackeado"}, headers=_auth(company_id=2))
    assert resp.status_code == 404

    ainda_original = client.get(f"/contacts/{contact['id']}", headers=_auth(company_id=1)).json()
    assert ainda_original["name"] == "Original"


def test_utilizador_nao_apaga_contacto_de_outra_empresa(client):
    contact = client.post("/contacts", json={"name": "Protegido"}, headers=_auth(company_id=1)).json()
    resp = client.delete(f"/contacts/{contact['id']}", headers=_auth(company_id=2))
    assert resp.status_code == 204  # DELETE idempotente não revela existência...

    # ...mas o registo continua lá, intacto, para a empresa dona:
    ainda_existe = client.get(f"/contacts/{contact['id']}", headers=_auth(company_id=1))
    assert ainda_existe.status_code == 200


def test_utilizador_nao_ve_anexos_de_contacto_de_outra_empresa(client):
    contact = client.post("/contacts", json={"name": "Com anexo"}, headers=_auth(company_id=1)).json()
    resp = client.get(f"/contacts/{contact['id']}/attachments", headers=_auth(company_id=2))
    assert resp.status_code == 404


def test_utilizador_nao_faz_upload_de_anexo_para_contacto_de_outra_empresa(client):
    import io
    contact = client.post("/contacts", json={"name": "Alvo"}, headers=_auth(company_id=1)).json()
    resp = client.post(
        f"/contacts/{contact['id']}/attachments",
        files={"file": ("x.pdf", io.BytesIO(b"dados"), "application/pdf")},
        headers=_auth(company_id=2),
    )
    assert resp.status_code == 404
