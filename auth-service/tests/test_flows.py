"""Testes dos fluxos funcionais do auth-service: registo, login, refresh e
gestão de colaboradores por um admin."""
import pytest


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_criar_conta_cria_empresa_e_devolve_tokens(client):
    resp = client.post("/criar_conta", json={
        "nome": "Ana", "email": "ana@empresa.com", "senha": "senha1234", "nome_empresa": "Empresa Ana", "whatsapp": "+244912345678",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "sucesso"
    assert "access_token" in body
    assert "refresh_token" in resp.cookies


def test_criar_conta_com_email_duplicado_e_rejeitada(client):
    payload = {"nome": "Ana", "email": "dup@empresa.com", "senha": "senha1234", "nome_empresa": "Empresa", "whatsapp": "+244912345678"}
    first = client.post("/criar_conta", json=payload)
    assert first.status_code == 201
    second = client.post("/criar_conta", json=payload)
    assert second.status_code == 409


def test_criar_conta_com_password_curta_e_rejeitada(client):
    resp = client.post("/criar_conta", json={
        "nome": "Ana", "email": "ana3@empresa.com", "senha": "1234567", "nome_empresa": "Empresa", "whatsapp": "+244912345678",
    })
    assert resp.status_code == 422


def test_criar_conta_sem_nome_de_empresa_e_rejeitada(client):
    resp = client.post("/criar_conta", json={
        "nome": "Ana", "email": "ana2@empresa.com", "senha": "senha1234", "nome_empresa": "   ", "whatsapp": "+244912345678",
    })
    assert resp.status_code == 422


def test_login_com_credenciais_corretas_devolve_access_token(client):
    client.post("/criar_conta", json={
        "nome": "Bob", "email": "bob@empresa.com", "senha": "senha1234", "nome_empresa": "Empresa Bob", "whatsapp": "+244912345678",
    })
    resp = client.post("/login", json={"email": "bob@empresa.com", "senha": "senha1234"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_me_devolve_dados_do_utilizador_autenticado(client):
    criar = client.post("/criar_conta", json={
        "nome": "Carlos", "email": "carlos@empresa.com", "senha": "senha1234", "nome_empresa": "Empresa Carlos", "whatsapp": "+244912345678",
    })
    token = criar.json()["access_token"]
    resp = client.get("/me", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "carlos@empresa.com"
    assert body["is_admin"] is True


def test_refresh_emite_novo_access_token_a_partir_do_cookie(client):
    criar = client.post("/criar_conta", json={
        "nome": "Diana", "email": "diana@empresa.com", "senha": "senha1234", "nome_empresa": "Empresa Diana", "whatsapp": "+244912345678",
    })
    assert "refresh_token" in criar.cookies
    resp = client.post("/refresh")
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_refresh_sem_cookie_e_rejeitado(client):
    resp = client.post("/refresh")
    assert resp.status_code == 401


def test_admin_cria_lista_e_apaga_colaborador(client):
    criar = client.post("/criar_conta", json={
        "nome": "Eva", "email": "eva@empresa.com", "senha": "senha1234", "nome_empresa": "Empresa Eva", "whatsapp": "+244912345678",
    })
    admin_token = criar.json()["access_token"]

    resp = client.post("/colaboradores", json={
        "nome": "Funcionário", "email": "func@empresa.com", "senha": "senha1234", "modules": ["crm"],
    }, headers=_auth(admin_token))
    assert resp.status_code == 201
    colaborador_id = resp.json()["id"]

    listagem = client.get("/colaboradores", headers=_auth(admin_token))
    assert listagem.status_code == 200
    assert any(c["id"] == colaborador_id for c in listagem.json())

    apagar = client.delete(f"/colaboradores/{colaborador_id}", headers=_auth(admin_token))
    assert apagar.status_code == 204

    listagem2 = client.get("/colaboradores", headers=_auth(admin_token))
    assert all(c["id"] != colaborador_id for c in listagem2.json())


def test_criar_colaborador_com_modulo_desconhecido_e_rejeitado(client):
    criar = client.post("/criar_conta", json={
        "nome": "Frank", "email": "frank@empresa.com", "senha": "senha1234", "nome_empresa": "Empresa Frank", "whatsapp": "+244912345678",
    })
    admin_token = criar.json()["access_token"]
    resp = client.post("/colaboradores", json={
        "nome": "X", "email": "x@empresa.com", "senha": "senha1234", "modules": ["modulo_fantasma"],
    }, headers=_auth(admin_token))
    assert resp.status_code == 422


def test_criar_colaborador_com_password_curta_e_rejeitada(client):
    criar = client.post("/criar_conta", json={
        "nome": "Zeca", "email": "zeca@empresa.com", "senha": "senha1234", "nome_empresa": "Empresa Zeca", "whatsapp": "+244912345678",
    })
    admin_token = criar.json()["access_token"]
    resp = client.post("/colaboradores", json={
        "nome": "X", "email": "curta@empresa.com", "senha": "123", "modules": [],
    }, headers=_auth(admin_token))
    assert resp.status_code == 422


def test_atualizar_colaborador_com_password_curta_e_rejeitada(client):
    criar = client.post("/criar_conta", json={
        "nome": "Gil", "email": "gil@empresa.com", "senha": "senha1234", "nome_empresa": "Empresa Gil", "whatsapp": "+244912345678",
    })
    admin_token = criar.json()["access_token"]
    colaborador = client.post("/colaboradores", json={
        "nome": "Y", "email": "y@empresa.com", "senha": "senha1234", "modules": [],
    }, headers=_auth(admin_token)).json()

    resp = client.put(f"/colaboradores/{colaborador['id']}", json={"senha": "curta"}, headers=_auth(admin_token))
    assert resp.status_code == 422


def test_nao_e_possivel_alterar_conta_de_outro_admin(client):
    admin1 = client.post("/criar_conta", json={
        "nome": "Hugo", "email": "hugo@empresa.com", "senha": "senha1234", "nome_empresa": "Empresa Hugo", "whatsapp": "+244912345678",
    }).json()
    admin2 = client.post("/criar_conta", json={
        "nome": "Inês", "email": "ines@empresa.com", "senha": "senha1234", "nome_empresa": "Empresa Hugo Filial", "whatsapp": "+244912345678",
    }).json()

    # admin2 pertence a outra empresa, portanto nem sequer é encontrado (isolamento) —
    # mas mesmo dentro da mesma empresa um admin não pode alterar outro admin.
    resp = client.put(
        f"/colaboradores/{admin2['id']}",
        json={"nome": "Tentativa"},
        headers=_auth(admin1["access_token"]),
    )
    assert resp.status_code == 404
