import itertools
import os
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PASSWORD_PEPPER", "test-pepper")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("COOKIE_SECURE", "false")

import pytest
from fastapi.testclient import TestClient

import app as app_module


class FakeAuthDB:
    """Emula as tabelas usadas por auth-service/app.py em memória, para os testes
    correrem sem uma base de dados real. O método execute() reconhece cada
    instrução SQL usada no app pelo início da string (normalizada)."""

    def __init__(self):
        self.usuarios = {}
        self.companies = {}
        self.company_seq = 0
        self.permissions = set()
        self.blocklist = {}
        self._seq = itertools.count()

    def _row(self, d):
        return SimpleNamespace(**d)

    def _next_seq(self):
        return next(self._seq)

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        p = tuple(params or ())

        if s.startswith("SELECT id FROM usuarios WHERE email = %s AND id <> %s"):
            email, exclude_id = p
            return [self._row({"id": u["id"]}) for u in self.usuarios.values()
                    if u["email"] == email and u["id"] != exclude_id]

        if s.startswith("SELECT id FROM usuarios WHERE email = %s"):
            email, = p
            return [self._row({"id": u["id"]}) for u in self.usuarios.values() if u["email"] == email]

        if s.startswith("SELECT id, nome, senha FROM usuarios WHERE email = %s"):
            email, = p
            for u in self.usuarios.values():
                if u["email"] == email:
                    return [self._row({"id": u["id"], "nome": u["nome"], "senha": u["senha"]})]
            return []

        if s.startswith("SELECT company_id, is_admin FROM usuarios WHERE id = %s"):
            uid, = p
            u = self.usuarios.get(str(uid))
            return [self._row({"company_id": u["company_id"], "is_admin": u["is_admin"]})] if u else []

        if s.startswith("SELECT nome, email FROM usuarios WHERE id = %s"):
            uid, = p
            u = self.usuarios.get(str(uid))
            return [self._row({"nome": u["nome"], "email": u["email"]})] if u else []

        if s.startswith("SELECT nome, company_id FROM usuarios WHERE id = %s"):
            uid, = p
            u = self.usuarios.get(str(uid))
            return [self._row({"nome": u["nome"], "company_id": u["company_id"]})] if u else []

        if s.startswith("SELECT id, nome, email, is_admin, criado_em FROM usuarios WHERE company_id = %s"):
            cid, = p
            rows = sorted((u for u in self.usuarios.values() if u["company_id"] == cid),
                          key=lambda u: u["criado_em"])
            return [self._row({"id": u["id"], "nome": u["nome"], "email": u["email"],
                                "is_admin": u["is_admin"], "criado_em": u["criado_em"]}) for u in rows]

        if s.startswith("SELECT id, nome, email, is_admin FROM usuarios WHERE id = %s AND company_id = %s"):
            uid, cid = p
            u = self.usuarios.get(str(uid))
            if not u or u["company_id"] != cid:
                return []
            return [self._row({"id": u["id"], "nome": u["nome"], "email": u["email"], "is_admin": u["is_admin"]})]

        if s.startswith("INSERT INTO usuarios (id, nome, email, senha) VALUES"):
            uid, nome, email, senha = p
            self.usuarios[str(uid)] = {
                "id": uid, "nome": nome, "email": email, "senha": senha,
                "company_id": None, "is_admin": False, "criado_em": self._next_seq(),
            }
            return []

        if s.startswith("INSERT INTO usuarios (id, nome, email, senha, company_id, is_admin) VALUES"):
            uid, nome, email, senha, cid = p
            self.usuarios[str(uid)] = {
                "id": uid, "nome": nome, "email": email, "senha": senha,
                "company_id": cid, "is_admin": False, "criado_em": self._next_seq(),
            }
            return []

        if s.startswith("UPDATE usuarios SET company_id = %s, is_admin = true WHERE id = %s"):
            cid, uid = p
            self.usuarios[str(uid)]["company_id"] = cid
            self.usuarios[str(uid)]["is_admin"] = True
            return []

        if s.startswith("UPDATE usuarios SET nome = %s, email = %s WHERE id = %s"):
            nome, email, uid = p
            self.usuarios[str(uid)]["nome"] = nome
            self.usuarios[str(uid)]["email"] = email
            return []

        if s.startswith("UPDATE usuarios SET senha = %s WHERE id = %s"):
            senha, uid = p
            self.usuarios[str(uid)]["senha"] = senha
            return []

        if s.startswith("DELETE FROM usuarios WHERE id = %s"):
            uid, = p
            self.usuarios.pop(str(uid), None)
            return []

        if s.startswith("INSERT INTO companies (name, whatsapp, owner_user_id) VALUES"):
            name, whatsapp, owner = p
            self.company_seq += 1
            cid = self.company_seq
            self.companies[cid] = {"id": cid, "name": name, "whatsapp": whatsapp, "owner_user_id": owner}
            return [self._row({"id": cid})]

        if s.startswith("INSERT INTO companies (name, owner_user_id) VALUES"):
            name, owner = p
            self.company_seq += 1
            cid = self.company_seq
            self.companies[cid] = {"id": cid, "name": name, "whatsapp": None, "owner_user_id": owner}
            return [self._row({"id": cid})]

        if s.startswith("SELECT module FROM user_module_permissions WHERE user_id = %s"):
            uid, = p
            return [self._row({"module": m}) for (u, m) in self.permissions if u == str(uid)]

        if s.startswith("DELETE FROM user_module_permissions WHERE user_id = %s"):
            uid, = p
            self.permissions = {k for k in self.permissions if k[0] != str(uid)}
            return []

        if s.startswith("INSERT INTO user_module_permissions (user_id, module) VALUES"):
            uid, mod = p
            self.permissions.add((str(uid), mod))
            return []

        if s.startswith("SELECT jti FROM jwt_blocklist WHERE jti = %s"):
            jti, = p
            return [self._row({"jti": jti})] if jti in self.blocklist else []

        if s.startswith("INSERT INTO jwt_blocklist"):
            jti, expires_at = p
            self.blocklist[jti] = expires_at
            return []

        raise AssertionError(f"SQL não reconhecido pelo FakeAuthDB: {s!r} params={p!r}")


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeAuthDB()
    monkeypatch.setattr(app_module, "execute", db.execute)
    return db


@pytest.fixture
def client(fake_db):
    # O limiter do slowapi guarda estado em memória partilhado entre pedidos com
    # o mesmo IP; sem reset, o rate limit de /criar_conta e /login (5/minuto)
    # seria atingido a meio da suite e derrubaria testes não relacionados.
    app_module.limiter.reset()
    return TestClient(app_module.app)
