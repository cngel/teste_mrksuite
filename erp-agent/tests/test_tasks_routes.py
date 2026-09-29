"""
Testes das rotas REST de automações (app/web.py) — não passam pelo agente
nem pelo LLM, por isso executam de imediato. Correm sem rede real: a sessão
é simulada diretamente e a base de tarefas é isolada num ficheiro temporário.
"""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import config, tasks_store
from app.agent import AgentDeps
from app.permissions import MODULE_CRM, MODULE_RH, CurrentUser


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TASKS_DB_PATH", str(tmp_path / "tasks.db"))
    tasks_store.init_db()


@pytest.fixture
def client():
    from app import web  # importado aqui para apanhar o TASKS_DB_PATH já isolado

    with TestClient(web.app) as test_client:
        yield test_client, web
    web._sessions.clear()
    web._pending.clear()


def _login(web_module, user: CurrentUser, test_client: TestClient) -> None:
    """Simula a sincronização automática com um utilizador já autenticado
    na plataforma: pré-preenche a sessão da Kora para um token e passa a
    enviá-lo no cabeçalho Authorization, tal como faria o proxy do
    web-service a partir do dashboard — sem chamar auth_client.me (sem
    rede real)."""
    token = str(uuid.uuid4())
    web_module._sessions[token] = (time.time(), AgentDeps(access_token=token, user=user))
    web_module._pending[token] = {}
    test_client.headers["Authorization"] = f"Bearer {token}"


def test_sem_sessao_devolve_401(client):
    test_client, _ = client
    resp = test_client.get("/api/tasks")
    assert resp.status_code == 401


def test_criar_listar_e_obter_tarefa(client):
    test_client, web = client
    _login(web, CurrentUser(id="1", nome="Ana", email="a@x.com", modules=[MODULE_CRM]), test_client)

    resp = test_client.post(
        "/api/tasks",
        json={"module": "crm", "title": "Resumo CRM", "frequency": "diaria", "time_of_day": "09:00"},
    )
    assert resp.status_code == 201
    task_id = resp.json()["id"]

    resp = test_client.get("/api/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = test_client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Resumo CRM"


def test_criar_tarefa_sem_acesso_ao_modulo_devolve_403(client):
    test_client, web = client
    _login(web, CurrentUser(id="1", nome="Ana", email="a@x.com", modules=[MODULE_CRM]), test_client)

    resp = test_client.post(
        "/api/tasks",
        json={"module": "people", "title": "Resumo RH", "frequency": "diaria", "time_of_day": "09:00"},
    )
    assert resp.status_code == 403


def test_nao_ve_nem_gere_tarefas_de_outro_utilizador(client):
    test_client, web = client
    _login(web, CurrentUser(id="1", nome="Ana", email="a@x.com", modules=[MODULE_CRM]), test_client)
    resp = test_client.post(
        "/api/tasks",
        json={"module": "crm", "title": "Resumo CRM", "frequency": "diaria", "time_of_day": "09:00"},
    )
    task_id = resp.json()["id"]

    _login(web, CurrentUser(id="2", nome="Rita", email="r@x.com", modules=[MODULE_CRM]), test_client)
    assert test_client.get("/api/tasks").json() == []
    assert test_client.get(f"/api/tasks/{task_id}").status_code == 404
    assert test_client.post(f"/api/tasks/{task_id}/pause").status_code == 404
    assert test_client.delete(f"/api/tasks/{task_id}").status_code == 404


def test_pausar_retomar_cancelar_e_apagar(client):
    test_client, web = client
    _login(web, CurrentUser(id="1", nome="Ana", email="a@x.com", modules=[MODULE_RH]), test_client)
    resp = test_client.post(
        "/api/tasks",
        json={"module": "people", "title": "Resumo RH", "frequency": "diaria", "time_of_day": "09:00"},
    )
    task_id = resp.json()["id"]

    resp = test_client.post(f"/api/tasks/{task_id}/pause")
    assert resp.json()["status"] == tasks_store.STATUS_PAUSED

    resp = test_client.post(f"/api/tasks/{task_id}/resume")
    assert resp.json()["status"] == tasks_store.STATUS_ACTIVE

    resp = test_client.post(f"/api/tasks/{task_id}/cancel")
    assert resp.json()["status"] == tasks_store.STATUS_CANCELLED

    resp = test_client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 204
    assert test_client.get(f"/api/tasks/{task_id}").status_code == 404


def test_atualizar_tarefa(client):
    test_client, web = client
    _login(web, CurrentUser(id="1", nome="Ana", email="a@x.com", modules=[MODULE_CRM]), test_client)
    resp = test_client.post(
        "/api/tasks",
        json={"module": "crm", "title": "Resumo CRM", "frequency": "diaria", "time_of_day": "09:00"},
    )
    task_id = resp.json()["id"]

    resp = test_client.put(f"/api/tasks/{task_id}", json={"time_of_day": "08:30"})
    assert resp.status_code == 200
    assert resp.json()["time_of_day"] == "08:30"


def test_admin_ve_e_gere_tarefas_de_qualquer_utilizador(client):
    test_client, web = client
    _login(web, CurrentUser(id="1", nome="Ana", email="a@x.com", modules=[MODULE_CRM]), test_client)
    resp = test_client.post(
        "/api/tasks",
        json={"module": "crm", "title": "Resumo CRM", "frequency": "diaria", "time_of_day": "09:00"},
    )
    task_id = resp.json()["id"]

    _login(web, CurrentUser(id="2", nome="Admin", email="admin@x.com", is_admin=True, modules=[]), test_client)
    assert test_client.get(f"/api/tasks/{task_id}").status_code == 200
    assert test_client.post(f"/api/tasks/{task_id}/pause").status_code == 200
