"""Testes de segurança (JWT) e dos fluxos de pastas/documentos/workflow do documents-service."""
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


def _auth(token=None):
    return {"Authorization": f"Bearer {token or _token()}"}


# ---------------------------------------------------------------------------
# Segurança / JWT
# ---------------------------------------------------------------------------

def test_sem_token_e_rejeitado(client):
    assert client.get("/folders").status_code == 401


def test_token_expirado_e_rejeitado(client):
    token = _token(expires_delta=timedelta(minutes=-1))
    assert client.get("/folders", headers=_auth(token)).status_code == 401


def test_token_revogado_e_rejeitado(client, fake_db):
    fake_db.blocklist["jti-x"] = True
    token = _token(jti="jti-x")
    resp = client.get("/folders", headers=_auth(token))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token revogado"


# ---------------------------------------------------------------------------
# Pastas
# ---------------------------------------------------------------------------

def _create_folder(client, name="Contratos", parent_id=None):
    return client.post("/folders", json={"name": name, "parent_id": parent_id}, headers=_auth())


def test_criar_e_listar_pasta(client):
    created = _create_folder(client)
    assert created.status_code == 201
    listed = client.get("/folders", headers=_auth())
    assert any(f["id"] == created.json()["id"] for f in listed.json())


def test_renomear_pasta(client):
    folder = _create_folder(client).json()
    resp = client.put(f"/folders/{folder['id']}", json={"name": "Novo Nome"}, headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["name"] == "Novo Nome"


def test_pasta_do_sistema_nao_pode_ser_renomeada(client, fake_db):
    fake_db.folders[999] = {"id": 999, "name": "Modelos", "parent_id": None, "kind": "system",
                             "employee_id": None, "department_id": None, "is_system": True, "company_id": 1}
    resp = client.put("/folders/999", json={"name": "Hackeado"}, headers=_auth())
    assert resp.status_code == 403


def test_pasta_do_sistema_nao_pode_ser_apagada(client, fake_db):
    fake_db.folders[998] = {"id": 998, "name": "Departamentos", "parent_id": None, "kind": "system",
                             "employee_id": None, "department_id": None, "is_system": True, "company_id": 1}
    resp = client.delete("/folders/998", headers=_auth())
    assert resp.status_code == 403


def test_apagar_pasta_com_subpastas_e_rejeitado(client):
    parent = _create_folder(client, name="Pai").json()
    _create_folder(client, name="Filha", parent_id=parent["id"])
    resp = client.delete(f"/folders/{parent['id']}", headers=_auth())
    assert resp.status_code == 409


def test_apagar_pasta_inexistente_devolve_404(client):
    assert client.delete("/folders/12345", headers=_auth()).status_code == 404


# ---------------------------------------------------------------------------
# Documentos e workflow de aprovação
# ---------------------------------------------------------------------------

def _upload_document(client, folder_id):
    return client.post(
        f"/documents?folder_id={folder_id}",
        files={"file": ("contrato.pdf", io.BytesIO(b"conteudo"), "application/pdf")},
        headers=_auth(),
    )


def test_upload_de_documento_comeca_no_estado_enviado(client):
    folder = _create_folder(client).json()
    resp = _upload_document(client, folder["id"])
    assert resp.status_code == 201
    assert resp.json()["workflow_state"] == "enviado"
    assert resp.json()["url"].startswith("https://minio.local/")


def test_workflow_feliz_enviado_ate_publicado(client):
    folder = _create_folder(client).json()
    doc = _upload_document(client, folder["id"]).json()

    submit = client.post(f"/documents/{doc['id']}/workflow/submit", json={}, headers=_auth())
    assert submit.status_code == 200
    assert submit.json()["workflow_state"] == "em_revisao"

    approve = client.post(f"/documents/{doc['id']}/workflow/approve", json={}, headers=_auth())
    assert approve.status_code == 200
    assert approve.json()["workflow_state"] == "aprovado"

    publish = client.post(f"/documents/{doc['id']}/workflow/publish", json={}, headers=_auth())
    assert publish.status_code == 200
    assert publish.json()["workflow_state"] == "publicado"

    history = client.get(f"/documents/{doc['id']}/workflow/history", headers=_auth())
    assert len(history.json()) == 3


def test_nao_e_possivel_aprovar_documento_que_nao_esta_em_revisao(client):
    folder = _create_folder(client).json()
    doc = _upload_document(client, folder["id"]).json()  # estado: enviado

    resp = client.post(f"/documents/{doc['id']}/workflow/approve", json={}, headers=_auth())
    assert resp.status_code == 409


def test_documento_rejeitado_pode_voltar_a_ser_submetido(client):
    folder = _create_folder(client).json()
    doc = _upload_document(client, folder["id"]).json()
    client.post(f"/documents/{doc['id']}/workflow/submit", json={}, headers=_auth())
    reject = client.post(f"/documents/{doc['id']}/workflow/reject", json={}, headers=_auth())
    assert reject.json()["workflow_state"] == "rejeitado"

    resubmit = client.post(f"/documents/{doc['id']}/workflow/submit", json={}, headers=_auth())
    assert resubmit.status_code == 200
    assert resubmit.json()["workflow_state"] == "em_revisao"


def test_favoritar_documento_alterna_estado(client):
    folder = _create_folder(client).json()
    doc = _upload_document(client, folder["id"]).json()
    first = client.post(f"/documents/{doc['id']}/favorite", headers=_auth())
    assert first.json()["is_favorite"] is True
    second = client.post(f"/documents/{doc['id']}/favorite", headers=_auth())
    assert second.json()["is_favorite"] is False


def test_obter_documento_inexistente_devolve_404(client):
    assert client.get("/documents/99999", headers=_auth()).status_code == 404


# ---------------------------------------------------------------------------
# Etiquetas
# ---------------------------------------------------------------------------

def test_criar_etiqueta_duplicada_e_rejeitada(client):
    first = client.post("/tags", json={"name": "Urgente"}, headers=_auth())
    assert first.status_code == 201
    second = client.post("/tags", json={"name": "Urgente"}, headers=_auth())
    assert second.status_code == 409
