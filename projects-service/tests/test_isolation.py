"""Testes de isolamento entre empresas (multi-tenant) do projects-service:
um JWT válido de uma empresa nunca deve dar acesso a projectos, tarefas,
etiquetas, comentários ou anexos de outra empresa."""
import io
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
    resp = client.get("/projects", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_projectos_nao_aparecem_na_listagem_de_outra_empresa(client):
    client.post("/projects", json={"name": "Projecto Empresa 1"}, headers=_auth(1))
    client.post("/projects", json={"name": "Projecto Empresa 2"}, headers=_auth(2))

    listagem = client.get("/projects", headers=_auth(1)).json()
    assert len(listagem) == 1
    assert listagem[0]["name"] == "Projecto Empresa 1"


def test_nao_acede_a_projecto_de_outra_empresa_por_id(client):
    project = client.post("/projects", json={"name": "Confidencial"}, headers=_auth(1)).json()
    assert client.get(f"/projects/{project['id']}", headers=_auth(2)).status_code == 404


def test_nao_cria_tarefa_num_projecto_de_outra_empresa(client):
    project = client.post("/projects", json={"name": "Alvo"}, headers=_auth(1)).json()
    resp = client.post("/tasks", json={"project_id": project["id"], "title": "Invasão"}, headers=_auth(2))
    assert resp.status_code == 404


def test_nao_ve_tarefa_de_outra_empresa_por_id(client):
    project = client.post("/projects", json={"name": "P1"}, headers=_auth(1)).json()
    task = client.post("/tasks", json={"project_id": project["id"], "title": "T1"}, headers=_auth(1)).json()
    assert client.get(f"/tasks/{task['id']}", headers=_auth(2)).status_code == 404


def test_nao_apaga_tarefa_de_outra_empresa(client):
    project = client.post("/projects", json={"name": "P1"}, headers=_auth(1)).json()
    task = client.post("/tasks", json={"project_id": project["id"], "title": "T1"}, headers=_auth(1)).json()
    client.delete(f"/tasks/{task['id']}", headers=_auth(2))
    # a tarefa continua lá para a empresa dona
    assert client.get(f"/tasks/{task['id']}", headers=_auth(1)).status_code == 200


def test_nao_associa_etiqueta_de_outra_empresa_a_tarefa_propria(client):
    project1 = client.post("/projects", json={"name": "P1"}, headers=_auth(1)).json()
    project2 = client.post("/projects", json={"name": "P2"}, headers=_auth(2)).json()
    task1 = client.post("/tasks", json={"project_id": project1["id"], "title": "T1"}, headers=_auth(1)).json()
    tag2 = client.post(f"/projects/{project2['id']}/tags", json={"name": "TagEmpresa2"}, headers=_auth(2)).json()

    resp = client.put(f"/tasks/{task1['id']}/tags", json={"tag_ids": [tag2["id"]]}, headers=_auth(1))
    assert resp.status_code == 404


def test_nao_comenta_tarefa_de_outra_empresa(client):
    project = client.post("/projects", json={"name": "P1"}, headers=_auth(1)).json()
    task = client.post("/tasks", json={"project_id": project["id"], "title": "T1"}, headers=_auth(1)).json()
    resp = client.post(f"/tasks/{task['id']}/comments", json={"body": "Invasor"}, headers=_auth(2))
    assert resp.status_code == 404


def test_nao_faz_upload_de_anexo_para_tarefa_de_outra_empresa(client):
    project = client.post("/projects", json={"name": "P1"}, headers=_auth(1)).json()
    task = client.post("/tasks", json={"project_id": project["id"], "title": "T1"}, headers=_auth(1)).json()
    resp = client.post(
        f"/tasks/{task['id']}/attachments",
        files={"file": ("x.pdf", io.BytesIO(b"dados"), "application/pdf")},
        headers=_auth(2),
    )
    assert resp.status_code == 404
