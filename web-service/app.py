import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from jose import JWTError, jwt
import httpx
import uvicorn

app = FastAPI(title="Web Service")

WEB_ROOT = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")
templates = Jinja2Templates(directory=WEB_ROOT / "templates")

# -------------------------------------------------------------------------
# Proxy /api/{serviço}/... -> microserviço interno correspondente.
#
# O frontend (dashboard.js) chama sempre caminhos relativos como /api/rh/...
# em vez de portas directas, para funcionar tanto em local como atrás de um
# nginx em produção sem precisar de CORS entre origens. Este proxy resolve
# isso já dentro do próprio web-service, usando os nomes dos serviços da
# rede Docker (docker-compose.yml) — não depende de nginx nenhum.
# Também é aqui que se aplica a camada de acesso por módulo: cada serviço
# de dados corresponde a um módulo da sidebar, e o pedido só é reencaminhado
# se o utilizador (via claims do JWT) tiver esse módulo atribuído, ou for
# admin da sua empresa (acesso total). Pedidos sem token válido passam à
# mesma para o serviço de destino, que responde 401 — este proxy só decide
# 403 quando o token é válido mas falta o módulo certo.
# -------------------------------------------------------------------------

SERVICE_MAP = {
    "auth": "http://auth-service:5000",
    "crm": "http://crm-service:5001",
    "rh": "http://rh-service:5003",
    "finance": "http://finance-service:5004",
    "projects": "http://projects-service:5005",
    "documents": "http://documents-service:5006",
    "accounting": "http://accounting-service:5007",
    "stock": "http://stock-service:5008",
    "kora": "http://erp-agent:8010",
}

# service -> módulo(s) que dão acesso; "documents" aceita qualquer um dos cinco,
# porque os widgets de documentos vivem dentro de People/Books/Projects/Accounting/Stock.
SERVICE_REQUIRED_MODULES = {
    "rh": ["people"],
    "crm": ["crm"],
    "finance": ["books"],
    "projects": ["projects"],
    "documents": ["people", "books", "projects", "accounting", "stock"],
    "accounting": ["accounting"],
    "stock": ["stock"],
}

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"

_HOP_BY_HOP_REQUEST_HEADERS = {"host", "content-length", "connection"}
_HOP_BY_HOP_RESPONSE_HEADERS = {"content-encoding", "transfer-encoding", "connection", "content-length"}


def _decode_claims(request: Request) -> dict | None:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[7:]
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None


@app.api_route("/api/{service}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_api(service: str, path: str, request: Request):
    base_url = SERVICE_MAP.get(service)
    if not base_url:
        raise HTTPException(status_code=404, detail="Serviço desconhecido")

    required_modules = SERVICE_REQUIRED_MODULES.get(service)
    if required_modules:
        claims = _decode_claims(request)
        if claims and not claims.get("is_admin"):
            user_modules = set(claims.get("modules") or [])
            if not user_modules.intersection(required_modules):
                raise HTTPException(status_code=403, detail="Sem permissão para este módulo")

    module_apps = getattr(request.app.state, "module_apps", None)
    module_app = module_apps.get(service) if module_apps is not None else None
    if module_apps is not None and module_app is None:
        raise HTTPException(status_code=404, detail="Módulo indisponível")

    upstream_url = f"{base_url}/{path}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP_REQUEST_HEADERS}
    # O serviço de destino perde o Host original (excluído acima como cabeçalho
    # hop-by-hop) — sem isto, qualquer URL pré-assinada que ele construa (ex.
    # fotos/documentos no MinIO) fica presa a "localhost", que só funciona a
    # partir da própria máquina do servidor, nunca de outro dispositivo na rede.
    forwarded_host = request.headers.get("host", "").split(":")[0]
    if forwarded_host:
        headers["x-forwarded-host"] = forwarded_host

    try:
        if module_app is not None:
            transport = httpx.ASGITransport(app=module_app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://module.internal", timeout=60.0) as client:
                upstream = await client.request(
                    request.method,
                    f"/{path}",
                    params=list(request.query_params.multi_items()),
                    content=body,
                    headers=headers,
                )
        else:
            async with httpx.AsyncClient(timeout=60.0) as client:
                upstream = await client.request(
                    request.method,
                    upstream_url,
                    params=list(request.query_params.multi_items()),
                    content=body,
                    headers=headers,
                )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail=f"Serviço '{service}' indisponível")

    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP_RESPONSE_HEADERS}
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/login", response_class=HTMLResponse)
def login(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@app.get("/registo", response_class=HTMLResponse)
def registo(request: Request):
    return templates.TemplateResponse(request=request, name="resistrar.html")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html")



@app.get("/precos", response_class=HTMLResponse)
def precos(request: Request):
    return templates.TemplateResponse(request=request, name="precos.html")


# Redirecionamentos para URLs antigas com extensão .html
@app.get("/login.html")
def login_html(): return RedirectResponse("/login")

@app.get("/resistrar.html")
def resistrar_html(): return RedirectResponse("/registo")


@app.get("/dashboard.html")
def dashboard_html(): return RedirectResponse("/dashboard")

@app.get("/precos.html")
def precos_html(): return RedirectResponse("/precos")

@app.get("/index.html")
def index_html(): return RedirectResponse("/")


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=5002, reload=True)
