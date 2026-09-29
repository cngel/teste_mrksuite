"""Testes de isolamento entre empresas (multi-tenant) do documents-service: um
JWT válido de uma empresa nunca deve dar acesso a pastas, documentos, etiquetas
ou modelos de outra empresa — e a árvore de pastas/etiquetas por omissão é
criada por empresa, não partilhada globalmente."""
import io
from datetime import datetime, timedelta, timezone

from jose import jwt as jose_jwt

import app as app_module
from core.db import TOP_LEVEL_FOLDERS, DEFAULT_TAGS


def _auth(company_id, sub="user-1"):
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "type": "access", "iat": now, "exp": now + timedelta(minutes=15), "company_id": company_id}
    token = jose_jwt.encode(payload, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


def test_sem_company_id_no_token_e_rejeitado(client):
    now = datetime.now(timezone.utc)
    payload = {"sub": "user-1", "type": "access", "iat": now, "exp": now + timedelta(minutes=15)}
    token = jose_jwt.encode(payload, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)
    resp = client.get("/folders", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_arvore_de_pastas_por_omissao_e_criada_por_empresa(client):
    listagem_1 = client.get("/folders", headers=_auth(1)).json()
    listagem_2 = client.get("/folders", headers=_auth(2)).json()

    assert len(listagem_1) == len(TOP_LEVEL_FOLDERS)
    assert len(listagem_2) == len(TOP_LEVEL_FOLDERS)
    # IDs diferentes: são pastas distintas por empresa, não a mesma árvore partilhada.
    ids_1 = {f["id"] for f in listagem_1}
    ids_2 = {f["id"] for f in listagem_2}
    assert ids_1.isdisjoint(ids_2)


def test_etiquetas_por_omissao_sao_criadas_por_empresa(client):
    tags_1 = client.get("/tags", headers=_auth(1)).json()
    tags_2 = client.get("/tags", headers=_auth(2)).json()
    assert len(tags_1) == len(DEFAULT_TAGS)
    assert {t["id"] for t in tags_1}.isdisjoint({t["id"] for t in tags_2})


def test_pastas_customizadas_nao_aparecem_para_outra_empresa(client):
    client.post("/folders", json={"name": "Pasta Empresa 1"}, headers=_auth(1))
    listagem_2 = client.get("/folders", headers=_auth(2)).json()
    assert all(f["name"] != "Pasta Empresa 1" for f in listagem_2)


def test_nao_acede_a_pasta_de_outra_empresa_por_id(client):
    folder = client.post("/folders", json={"name": "Confidencial"}, headers=_auth(1)).json()
    resp = client.put(f"/folders/{folder['id']}", json={"name": "Hackeado"}, headers=_auth(2))
    assert resp.status_code == 404


def test_nao_cria_subpasta_dentro_de_pasta_de_outra_empresa(client):
    folder = client.post("/folders", json={"name": "Pai"}, headers=_auth(1)).json()
    resp = client.post("/folders", json={"name": "Invasão", "parent_id": folder["id"]}, headers=_auth(2))
    assert resp.status_code == 404


def _upload(client, folder_id, company_id, filename="doc.pdf"):
    return client.post(
        f"/documents?folder_id={folder_id}",
        files={"file": (filename, io.BytesIO(b"conteudo"), "application/pdf")},
        headers=_auth(company_id),
    )


def test_documentos_nao_aparecem_na_listagem_de_outra_empresa(client):
    folder1 = client.post("/folders", json={"name": "F1"}, headers=_auth(1)).json()
    folder2 = client.post("/folders", json={"name": "F2"}, headers=_auth(2)).json()
    _upload(client, folder1["id"], 1)
    _upload(client, folder2["id"], 2)

    listagem_1 = client.get("/documents", headers=_auth(1)).json()
    assert len(listagem_1) == 1


def test_nao_faz_upload_para_pasta_de_outra_empresa(client):
    folder = client.post("/folders", json={"name": "Alvo"}, headers=_auth(1)).json()
    resp = _upload(client, folder["id"], 2)
    assert resp.status_code == 404


def test_nao_acede_a_documento_de_outra_empresa(client):
    folder = client.post("/folders", json={"name": "F"}, headers=_auth(1)).json()
    doc = _upload(client, folder["id"], 1).json()
    assert client.get(f"/documents/{doc['id']}", headers=_auth(2)).status_code == 404


def test_nao_transita_workflow_de_documento_de_outra_empresa(client):
    folder = client.post("/folders", json={"name": "F"}, headers=_auth(1)).json()
    doc = _upload(client, folder["id"], 1).json()
    resp = client.post(f"/documents/{doc['id']}/workflow/submit", json={}, headers=_auth(2))
    assert resp.status_code == 404


def test_nao_ve_etiqueta_de_outra_empresa(client):
    tag = client.post("/tags", json={"name": "Confidencial"}, headers=_auth(1)).json()
    tags_2 = client.get("/tags", headers=_auth(2)).json()
    assert all(t["id"] != tag["id"] for t in tags_2)


def test_nao_usa_modelo_de_outra_empresa(client):
    folder = client.post("/folders", json={"name": "F"}, headers=_auth(2)).json()
    resp = client.post(
        "/templates/99999/use",
        json={"folder_id": folder["id"], "fields": {}},
        headers=_auth(2),
    )
    assert resp.status_code == 404
