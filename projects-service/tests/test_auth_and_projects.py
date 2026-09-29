"""Testes de segurança (JWT) e dos fluxos de projectos/tarefas do projects-service."""
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
    assert client.get("/projects").status_code == 401


def test_token_invalido_e_rejeitado(client):
    assert client.get("/projects", headers=_auth("lixo")).status_code == 401


def test_token_expirado_e_rejeitado(client):
    token = _token(expires_delta=timedelta(minutes=-1))
    assert client.get("/projects", headers=_auth(token)).status_code == 401


def test_refresh_token_nao_serve_como_access_token(client):
    token = _token(token_type="refresh")
    assert client.get("/projects", headers=_auth(token)).status_code == 401


def test_token_revogado_e_rejeitado(client, fake_db):
    fake_db.blocklist["jti-x"] = True
    token = _token(jti="jti-x")
    resp = client.get("/projects", headers=_auth(token))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token revogado"


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def _create_project(client, **overrides):
    body = {"name": "Website novo", "status": "ativo"}
    body.update(overrides)
    return client.post("/projects", json=body, headers=_auth())


def test_criar_e_listar_projecto(client):
    created = _create_project(client)
    assert created.status_code == 201
    project = created.json()

    listed = client.get("/projects", headers=_auth())
    assert listed.status_code == 200
    assert any(p["id"] == project["id"] for p in listed.json())


def test_obter_projecto_inexistente_devolve_404(client):
    assert client.get("/projects/999", headers=_auth()).status_code == 404


def test_atualizar_projecto(client):
    project = _create_project(client).json()
    resp = client.put(f"/projects/{project['id']}", json={"name": "Renomeado", "status": "concluido"}, headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renomeado"


def test_apagar_projecto(client):
    project = _create_project(client).json()
    assert client.delete(f"/projects/{project['id']}", headers=_auth()).status_code == 204


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def test_criar_tarefa_e_listar_por_projecto(client):
    project = _create_project(client).json()
    task = client.post("/tasks", json={"project_id": project["id"], "title": "Fazer X"}, headers=_auth())
    assert task.status_code == 201
    task_body = task.json()
    assert task_body["tags"] == []

    listed = client.get(f"/projects/{project['id']}/tasks", headers=_auth())
    assert listed.status_code == 200
    assert any(t["id"] == task_body["id"] for t in listed.json())


def test_marcar_tarefa_como_concluida_define_completed_at(client):
    project = _create_project(client).json()
    task = client.post("/tasks", json={"project_id": project["id"], "title": "Fazer Y"}, headers=_auth()).json()

    resp = client.patch(f"/tasks/{task['id']}/status", json={"status": "done"}, headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    assert resp.json()["completed_at"] is not None


def test_atualizar_tarefa_inexistente_devolve_404(client):
    resp = client.put("/tasks/999", json={"project_id": 1, "title": "X"}, headers=_auth())
    assert resp.status_code == 404


def test_apagar_tarefa(client):
    project = _create_project(client).json()
    task = client.post("/tasks", json={"project_id": project["id"], "title": "Apagar-me"}, headers=_auth()).json()
    assert client.delete(f"/tasks/{task['id']}", headers=_auth()).status_code == 204


# ---------------------------------------------------------------------------
# Tags e comentários
# ---------------------------------------------------------------------------

def test_criar_etiqueta_e_associar_a_tarefa(client):
    project = _create_project(client).json()
    task = client.post("/tasks", json={"project_id": project["id"], "title": "Com etiqueta"}, headers=_auth()).json()
    tag = client.post(f"/projects/{project['id']}/tags", json={"name": "Urgente"}, headers=_auth()).json()

    resp = client.put(f"/tasks/{task['id']}/tags", json={"tag_ids": [tag["id"]]}, headers=_auth())
    assert resp.status_code == 200
    assert any(t["id"] == tag["id"] for t in resp.json())

    fetched = client.get(f"/tasks/{task['id']}", headers=_auth())
    assert any(t["id"] == tag["id"] for t in fetched.json()["tags"])


def test_criar_e_listar_comentarios(client):
    project = _create_project(client).json()
    task = client.post("/tasks", json={"project_id": project["id"], "title": "Comentada"}, headers=_auth()).json()

    resp = client.post(f"/tasks/{task['id']}/comments", json={"body": "Olá"}, headers=_auth())
    assert resp.status_code == 201
    assert resp.json()["author_name"] == "Utilizador Teste"

    listed = client.get(f"/tasks/{task['id']}/comments", headers=_auth())
    assert len(listed.json()) == 1


# ---------------------------------------------------------------------------
# Anexos
# ---------------------------------------------------------------------------

def test_upload_e_apagar_anexo(client):
    project = _create_project(client).json()
    task = client.post("/tasks", json={"project_id": project["id"], "title": "Com anexo"}, headers=_auth()).json()

    upload = client.post(
        f"/tasks/{task['id']}/attachments",
        files={"file": ("ficheiro.pdf", io.BytesIO(b"dados"), "application/pdf")},
        headers=_auth(),
    )
    assert upload.status_code == 201
    attachment_id = upload.json()["id"]

    listed = client.get(f"/tasks/{task['id']}/attachments", headers=_auth())
    assert listed.status_code == 200
    assert listed.json()[0]["url"].startswith("https://minio.local/")

    assert client.delete(f"/attachments/{attachment_id}", headers=_auth()).status_code == 204


def test_apagar_anexo_inexistente_devolve_404(client):
    assert client.delete("/attachments/999", headers=_auth()).status_code == 404
