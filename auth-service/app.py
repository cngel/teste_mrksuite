import os
import uuid
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

if __package__:
    from .core.db import execute, init_db
else:
    from core.db import execute, init_db

app = FastAPI(title="Auth Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS",
        "http://localhost,http://localhost:80,http://127.0.0.1,http://localhost:5002",
    ).split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

JWT_SECRET      = os.environ["JWT_SECRET"]
JWT_ALGORITHM   = "HS256"
PASSWORD_PEPPER = os.environ["PASSWORD_PEPPER"]

# Em produção deixar sempre true (cookie só viaja em HTTPS). Só desligar em dev local sem TLS.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() != "false"

ph = PasswordHasher(memory_cost=65536, time_cost=3, parallelism=4)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

# Hash "morto" com o mesmo custo computacional de um hash real — usado no /login quando o
# utilizador não existe, para que o tempo de resposta seja idêntico ao de uma password errada
# num utilizador real, evitando enumeração de emails via timing attack.
_DUMMY_HASH = ph.hash("dummy-password-for-timing-attack-mitigation" + PASSWORD_PEPPER)

# Módulos atribuíveis — espelha os data-target da sidebar em dashboard.html.
# "home" fica de fora de propósito: é sempre visível, não é atribuível.
MODULE_REGISTRY = ["crm", "books", "projects", "desk", "people", "creator", "kora", "accounting", "stock"]


@app.on_event("startup")
def on_startup():
    init_db()


# ---------------------------------------------------------------------------
# Helpers JWT
# ---------------------------------------------------------------------------

def _create_token(user_id: str, token_type: str, expires: timedelta, extra_claims: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "jti": str(uuid.uuid4()),
        "type": token_type,
        "iat": now,
        "nbf": now,
        "exp": now + expires,
    }
    payload.update(extra_claims or {})
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _identity_claims(user_id: str) -> dict:
    """company_id/is_admin sempre lidos de fresco da BD — nunca confiar em claims antigas."""
    rows = execute("SELECT company_id, is_admin FROM usuarios WHERE id = %s", (user_id,))
    if not rows:
        return {"company_id": None, "is_admin": False}
    return {"company_id": rows[0].company_id, "is_admin": bool(rows[0].is_admin)}


def _user_modules(user_id: str) -> list[str]:
    rows = execute("SELECT module FROM user_module_permissions WHERE user_id = %s", (user_id,))
    return [r.module for r in rows]


def _verify_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tipo de token incorreto")
    rows = execute(
        "SELECT jti FROM jwt_blocklist WHERE jti = %s AND expires_at > NOW()",
        (payload["jti"],),
    )
    if rows:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revogado")
    return payload


def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    return _verify_token(token, "access")["sub"]


def get_current_claims(token: str = Depends(oauth2_scheme)) -> dict:
    return _verify_token(token, "access")


def get_current_admin(claims: dict = Depends(get_current_claims)) -> dict:
    if not claims.get("is_admin"):
        raise HTTPException(status_code=403, detail="Apenas administradores")
    return claims


def get_refresh_user_cookie(refresh_token: str | None = Cookie(default=None)) -> dict:
    """Lê o refresh token do cookie HttpOnly em vez do header Authorization —
    o refresh token nunca deve ser acessível a JavaScript nem circular no corpo da resposta."""
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token ausente")
    return _verify_token(refresh_token, "refresh")


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        max_age=int(timedelta(days=30).total_seconds()),
        path="/",
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

MIN_PASSWORD_LENGTH = 8


def _validar_password(senha: str) -> str:
    if len(senha) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"A password deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres")
    return senha


class CriarContaSchema(BaseModel):
    nome: str
    email: EmailStr
    senha: str
    nome_empresa: str
    whatsapp: str

    @field_validator("senha")
    @classmethod
    def validar_senha(cls, v: str) -> str:
        return _validar_password(v)


class LoginSchema(BaseModel):
    email: EmailStr
    senha: str

class ColaboradorCreateSchema(BaseModel):
    nome: str
    email: EmailStr
    senha: str
    modules: list[str] = []

    @field_validator("senha")
    @classmethod
    def validar_senha(cls, v: str) -> str:
        return _validar_password(v)


class ColaboradorModulesSchema(BaseModel):
    modules: list[str]

class ColaboradorUpdateSchema(BaseModel):
    nome: str | None = None
    email: EmailStr | None = None
    senha: str | None = None
    modules: list[str] | None = None

    @field_validator("senha")
    @classmethod
    def validar_senha_opcional(cls, v: str | None) -> str | None:
        return v if v is None else _validar_password(v)


def _issue_tokens(response: Response, user_id: str) -> dict:
    claims = _identity_claims(user_id)
    claims["modules"] = _user_modules(user_id)
    refresh_token = _create_token(user_id, "refresh", timedelta(days=30), claims)
    _set_refresh_cookie(response, refresh_token)
    return {
        "access_token": _create_token(user_id, "access", timedelta(minutes=15), claims),
    }


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@app.post("/criar_conta", status_code=201)
@limiter.limit("5/minute")
def criar_conta(request: Request, body: CriarContaSchema, response: Response):
    rows = execute("SELECT id FROM usuarios WHERE email = %s", (body.email,))
    if rows:
        raise HTTPException(status_code=409, detail="Email já registado")

    if not body.nome_empresa.strip():
        raise HTTPException(status_code=422, detail="Nome da empresa é obrigatório")

    if not body.whatsapp.strip():
        raise HTTPException(status_code=422, detail="Número de WhatsApp é obrigatório")

    senha_hash = ph.hash(body.senha + PASSWORD_PEPPER)
    novo_id = str(uuid.uuid4())

    execute(
        "INSERT INTO usuarios (id, nome, email, senha, whatsapp) VALUES (%s, %s, %s, %s, %s)",
        (novo_id, body.nome, body.email, senha_hash, body.whatsapp),
    )

    # Quem se auto-regista cria a sua própria empresa e é logo o admin dela.
    empresa = execute(
        "INSERT INTO companies (name, whatsapp, owner_user_id) VALUES (%s, %s, %s) RETURNING id",
        (body.nome_empresa.strip(), body.whatsapp.strip(), novo_id),
    )[0]
    execute(
        "UPDATE usuarios SET company_id = %s, is_admin = true WHERE id = %s",
        (empresa.id, novo_id),
    )

    return {
        "status": "sucesso",
        "id": novo_id,
        **_issue_tokens(response, novo_id),
        "mensagem": "Conta e empresa criadas com sucesso!",
    }


@app.post("/login")
@limiter.limit("5/minute")
def login(request: Request, body: LoginSchema, response: Response):
    rows = execute(
        "SELECT id, nome, senha FROM usuarios WHERE email = %s",
        (body.email,),
    )
    usuario = rows[0] if rows else None

    # Verifica sempre um hash (real ou "morto") mesmo quando o email não existe, para que o
    # tempo de resposta não revele se o email está ou não registado (mitigação de timing attack).
    try:
        ph.verify(usuario.senha if usuario else _DUMMY_HASH, body.senha + PASSWORD_PEPPER)
    except VerifyMismatchError:
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    if not usuario:
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    user_id = str(usuario.id)
    return {
        "status": "sucesso",
        "mensagem": f"Bem-vindo de volta, {usuario.nome}!",
        **_issue_tokens(response, user_id),
    }


@app.post("/logout", status_code=200)
def logout(payload: dict = Depends(lambda token=Depends(oauth2_scheme): _verify_token(token, "access"))):
    execute(
        "INSERT INTO jwt_blocklist (jti, expires_at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (payload["jti"], datetime.fromtimestamp(payload["exp"], tz=timezone.utc)),
    )
    return {"status": "sucesso", "mensagem": "Access token revogado."}


@app.post("/logout_refresh", status_code=200)
def logout_refresh(response: Response, payload: dict = Depends(get_refresh_user_cookie)):
    execute(
        "INSERT INTO jwt_blocklist (jti, expires_at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (payload["jti"], datetime.fromtimestamp(payload["exp"], tz=timezone.utc)),
    )
    response.delete_cookie(key="refresh_token", path="/")
    return {"status": "sucesso", "mensagem": "Refresh token revogado."}


@app.post("/refresh")
def refresh(payload: dict = Depends(get_refresh_user_cookie)):
    user_id = payload["sub"]
    claims = _identity_claims(user_id)
    claims["modules"] = _user_modules(user_id)
    return {
        "access_token": _create_token(user_id, "access", timedelta(minutes=15), claims),
    }


# ---------------------------------------------------------------------------
# Identidade e gestão de colaboradores (admin da empresa)
# ---------------------------------------------------------------------------

@app.get("/me")
def me(claims: dict = Depends(get_current_claims)):
    user_id = claims["sub"]
    rows = execute("SELECT nome, email FROM usuarios WHERE id = %s", (user_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado")
    return {
        "id": user_id,
        "nome": rows[0].nome,
        "email": rows[0].email,
        "is_admin": bool(claims.get("is_admin")),
        "company_id": claims.get("company_id"),
        "modules": claims.get("modules", []),
    }


def _resolve_admin_company(admin: dict) -> int:
    """Devolve o company_id real do admin (lido de fresco da BD, nunca das claims,
    que podem estar desactualizadas). Contas admin criadas antes da introdução das
    empresas ficaram com company_id NULL — nesse caso cria a empresa em falta,
    para que a gestão de colaboradores funcione."""
    user_id = admin["sub"]
    rows = execute("SELECT nome, company_id FROM usuarios WHERE id = %s", (user_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado")
    if rows[0].company_id:
        return rows[0].company_id

    empresa = execute(
        "INSERT INTO companies (name, owner_user_id) VALUES (%s, %s) RETURNING id",
        (f"Empresa de {rows[0].nome}", user_id),
    )[0]
    execute("UPDATE usuarios SET company_id = %s WHERE id = %s", (empresa.id, user_id))
    return empresa.id


@app.get("/colaboradores")
def listar_colaboradores(admin: dict = Depends(get_current_admin)):
    company_id = _resolve_admin_company(admin)
    rows = execute(
        "SELECT id, nome, email, is_admin, criado_em FROM usuarios WHERE company_id = %s ORDER BY criado_em",
        (company_id,),
    )
    colaboradores = []
    for r in rows:
        modules = _user_modules(str(r.id))
        colaboradores.append({
            "id": str(r.id), "nome": r.nome, "email": r.email,
            "is_admin": bool(r.is_admin), "modules": modules,
        })
    return colaboradores


@app.post("/colaboradores", status_code=201)
def criar_colaborador(body: ColaboradorCreateSchema, admin: dict = Depends(get_current_admin)):
    company_id = _resolve_admin_company(admin)

    rows = execute("SELECT id FROM usuarios WHERE email = %s", (body.email,))
    if rows:
        raise HTTPException(status_code=409, detail="Email já registado")

    modulos_invalidos = set(body.modules) - set(MODULE_REGISTRY)
    if modulos_invalidos:
        raise HTTPException(status_code=422, detail=f"Módulos desconhecidos: {', '.join(modulos_invalidos)}")

    senha_hash = ph.hash(body.senha + PASSWORD_PEPPER)
    novo_id = str(uuid.uuid4())
    execute(
        "INSERT INTO usuarios (id, nome, email, senha, company_id, is_admin) VALUES (%s, %s, %s, %s, %s, false)",
        (novo_id, body.nome, body.email, senha_hash, company_id),
    )
    for modulo in set(body.modules):
        execute(
            "INSERT INTO user_module_permissions (user_id, module) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (novo_id, modulo),
        )

    return {"id": novo_id, "nome": body.nome, "email": body.email, "is_admin": False, "modules": list(set(body.modules))}


def _get_company_colaborador(colaborador_id: str, admin: dict):
    """Valida que o alvo existe, pertence à empresa do admin e não é outro admin."""
    try:
        uuid.UUID(colaborador_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Identificador de colaborador inválido")
    company_id = _resolve_admin_company(admin)
    rows = execute(
        "SELECT id, nome, email, is_admin FROM usuarios WHERE id = %s AND company_id = %s",
        (colaborador_id, company_id),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Colaborador não encontrado nesta empresa")
    if rows[0].is_admin:
        raise HTTPException(status_code=403, detail="Não é possível alterar contas de administrador")
    return rows[0]


@app.put("/colaboradores/{colaborador_id}/modules")
def atualizar_modulos_colaborador(colaborador_id: str, body: ColaboradorModulesSchema, admin: dict = Depends(get_current_admin)):
    modulos_invalidos = set(body.modules) - set(MODULE_REGISTRY)
    if modulos_invalidos:
        raise HTTPException(status_code=422, detail=f"Módulos desconhecidos: {', '.join(modulos_invalidos)}")

    _get_company_colaborador(colaborador_id, admin)

    execute("DELETE FROM user_module_permissions WHERE user_id = %s", (colaborador_id,))
    for modulo in set(body.modules):
        execute("INSERT INTO user_module_permissions (user_id, module) VALUES (%s, %s)", (colaborador_id, modulo))

    return {"id": colaborador_id, "modules": list(set(body.modules))}


@app.put("/colaboradores/{colaborador_id}")
def atualizar_colaborador(colaborador_id: str, body: ColaboradorUpdateSchema, admin: dict = Depends(get_current_admin)):
    alvo = _get_company_colaborador(colaborador_id, admin)

    if body.modules is not None:
        modulos_invalidos = set(body.modules) - set(MODULE_REGISTRY)
        if modulos_invalidos:
            raise HTTPException(status_code=422, detail=f"Módulos desconhecidos: {', '.join(modulos_invalidos)}")

    nome = (body.nome or "").strip() or alvo.nome
    email = body.email or alvo.email

    if email != alvo.email:
        rows = execute("SELECT id FROM usuarios WHERE email = %s AND id <> %s", (email, colaborador_id))
        if rows:
            raise HTTPException(status_code=409, detail="Email já registado por outra conta")

    execute("UPDATE usuarios SET nome = %s, email = %s WHERE id = %s", (nome, email, colaborador_id))

    if body.senha:
        senha_hash = ph.hash(body.senha + PASSWORD_PEPPER)
        execute("UPDATE usuarios SET senha = %s WHERE id = %s", (senha_hash, colaborador_id))

    if body.modules is not None:
        execute("DELETE FROM user_module_permissions WHERE user_id = %s", (colaborador_id,))
        for modulo in set(body.modules):
            execute("INSERT INTO user_module_permissions (user_id, module) VALUES (%s, %s)", (colaborador_id, modulo))

    return {
        "id": colaborador_id,
        "nome": nome,
        "email": email,
        "is_admin": False,
        "modules": _user_modules(colaborador_id),
    }


@app.delete("/colaboradores/{colaborador_id}", status_code=204)
def apagar_colaborador(colaborador_id: str, admin: dict = Depends(get_current_admin)):
    if colaborador_id == admin["sub"]:
        raise HTTPException(status_code=400, detail="Não podes apagar a tua própria conta")

    _get_company_colaborador(colaborador_id, admin)

    execute("DELETE FROM user_module_permissions WHERE user_id = %s", (colaborador_id,))
    execute("DELETE FROM usuarios WHERE id = %s", (colaborador_id,))


@app.delete("/admin/usuario/{id_usuario}", status_code=200)
@limiter.limit("10/hour")
def apagar_usuario(request: Request, id_usuario: str, admin: dict = Depends(get_current_admin)):
    """Autorização via role no JWT (is_admin), lida sempre de fresco da BD — substitui o
    antigo segredo estático partilhado por header, que era um único ponto de falha global."""
    if id_usuario == admin["sub"]:
        raise HTTPException(status_code=400, detail="Não podes apagar a tua própria conta")

    alvo = _get_company_colaborador(id_usuario, admin)

    execute("DELETE FROM user_module_permissions WHERE user_id = %s", (id_usuario,))
    execute("DELETE FROM usuarios WHERE id = %s", (id_usuario,))
    return {"status": "sucesso", "mensagem": f"Utilizador {alvo.email} apagado."}
