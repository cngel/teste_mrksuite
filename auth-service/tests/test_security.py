"""
Testes de segurança do auth-service: validação de JWT, revogação, isolamento
entre empresas e mitigação de timing attack no /login.
"""
from datetime import datetime, timedelta, timezone

import jose.jwt as jose_jwt
import pytest

import app as app_module


def _token(sub="u1", token_type="access", secret=None, expires_delta=timedelta(minutes=15), **extra):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub, "jti": "jti-1", "type": token_type,
        "iat": now, "nbf": now, "exp": now + expires_delta,
    }
    payload.update(extra)
    return jose_jwt.encode(payload, secret or app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_token_invalido_e_rejeitado(client):
    resp = client.get("/me", headers=_auth("isto-nao-e-um-jwt"))
    assert resp.status_code == 401


def test_token_com_segredo_errado_e_rejeitado(client):
    token = _token(secret="segredo-errado")
    resp = client.get("/me", headers=_auth(token))
    assert resp.status_code == 401


def test_token_expirado_e_rejeitado(client):
    token = _token(expires_delta=timedelta(minutes=-1))
    resp = client.get("/me", headers=_auth(token))
    assert resp.status_code == 401


def test_refresh_token_nao_e_aceite_como_access_token(client):
    token = _token(token_type="refresh")
    resp = client.get("/me", headers=_auth(token))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Tipo de token incorreto"


def test_sem_token_e_rejeitado(client):
    resp = client.get("/me")
    assert resp.status_code == 401


def test_access_token_revogado_e_rejeitado(client, fake_db):
    token = _token()
    fake_db.blocklist["jti-1"] = datetime.now(timezone.utc) + timedelta(days=1)
    resp = client.get("/me", headers=_auth(token))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token revogado"


def test_logout_revoga_o_access_token_e_bloqueia_pedidos_seguintes(client, fake_db):
    admin_id = "11111111-1111-1111-1111-111111111111"
    fake_db.usuarios[admin_id] = {
        "id": admin_id, "nome": "Ana", "email": "ana@x.com", "senha": "hash",
        "company_id": 1, "is_admin": False, "criado_em": 0,
    }
    token = _token(sub=admin_id)

    logout_resp = client.post("/logout", headers=_auth(token))
    assert logout_resp.status_code == 200

    resp = client.get("/me", headers=_auth(token))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token revogado"


def test_utilizador_sem_ser_admin_nao_acede_a_rotas_de_admin(client, fake_db):
    uid = "22222222-2222-2222-2222-222222222222"
    fake_db.usuarios[uid] = {
        "id": uid, "nome": "Rita", "email": "rita@x.com", "senha": "hash",
        "company_id": 1, "is_admin": False, "criado_em": 0,
    }
    token = _token(sub=uid, is_admin=False)
    resp = client.get("/colaboradores", headers=_auth(token))
    assert resp.status_code == 403


def test_login_com_email_inexistente_e_com_password_errada_devolvem_erro_identico(client, fake_db):
    """Mitigação de timing/enumeration attack: a resposta não deve distinguir
    'email não existe' de 'password errada para um email existente'."""
    from argon2 import PasswordHasher
    ph = PasswordHasher()
    real_uid = "33333333-3333-3333-3333-333333333333"
    fake_db.usuarios[real_uid] = {
        "id": real_uid, "nome": "Zé", "email": "ze@x.com",
        "senha": ph.hash("password-correta" + app_module.PASSWORD_PEPPER),
        "company_id": None, "is_admin": False, "criado_em": 0,
    }

    resp_email_inexistente = client.post("/login", json={"email": "ninguem@x.com", "senha": "qualquer"})
    resp_password_errada = client.post("/login", json={"email": "ze@x.com", "senha": "errada"})

    assert resp_email_inexistente.status_code == 401
    assert resp_password_errada.status_code == 401
    assert resp_email_inexistente.json()["detail"] == resp_password_errada.json()["detail"]


def test_colaborador_de_outra_empresa_nao_e_visivel(client, fake_db):
    admin_id = "44444444-4444-4444-4444-444444444444"
    outro_user_id = "55555555-5555-5555-5555-555555555555"
    fake_db.usuarios[admin_id] = {
        "id": admin_id, "nome": "Admin A", "email": "admina@x.com", "senha": "hash",
        "company_id": 1, "is_admin": True, "criado_em": 0,
    }
    fake_db.usuarios[outro_user_id] = {
        "id": outro_user_id, "nome": "Fulano", "email": "fulano@x.com", "senha": "hash",
        "company_id": 2, "is_admin": False, "criado_em": 0,
    }
    token = _token(sub=admin_id, is_admin=True)

    resp = client.delete(f"/admin/usuario/{outro_user_id}", headers=_auth(token))
    assert resp.status_code == 404


def test_admin_nao_pode_apagar_a_propria_conta(client, fake_db):
    admin_id = "66666666-6666-6666-6666-666666666666"
    fake_db.usuarios[admin_id] = {
        "id": admin_id, "nome": "Admin", "email": "admin@x.com", "senha": "hash",
        "company_id": 1, "is_admin": True, "criado_em": 0,
    }
    token = _token(sub=admin_id, is_admin=True)
    resp = client.delete(f"/admin/usuario/{admin_id}", headers=_auth(token))
    assert resp.status_code == 400
