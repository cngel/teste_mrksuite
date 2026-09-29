import importlib.machinery
import importlib.util
import hashlib
import hmac
import logging
import os
import re
import sys
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from psycopg2.extras import RealDictCursor


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIRECTORIES = {
    "auth": "auth-service",
    "crm": "crm-service",
    "rh": "rh-service",
    "documents": "documents-service",
    "accounting": "accounting-service",
    "stock": "stock-service",
}


def _load_app(module_name: str, directory_name: str):
    source_directory = ROOT / directory_name
    package_name = f"{__package__}.modules.{module_name}"
    package_spec = importlib.machinery.ModuleSpec(package_name, loader=None, is_package=True)
    package_spec.submodule_search_locations = [str(source_directory)]
    package = importlib.util.module_from_spec(package_spec)
    sys.modules[package_name] = package

    app_spec = importlib.util.spec_from_file_location(f"{package_name}.app", source_directory / "app.py")
    if app_spec is None or app_spec.loader is None:
        raise ImportError(f"Não foi possível carregar o módulo {module_name}")
    module = importlib.util.module_from_spec(app_spec)
    sys.modules[app_spec.name] = module
    app_spec.loader.exec_module(module)
    return module.app


module_apps = {name: _load_app(name, directory) for name, directory in DOMAIN_DIRECTORIES.items()}
app = _load_app("web", "web-service")
app.state.module_apps = module_apps
get_connection = sys.modules[f"{__package__}.modules.crm.core.db"].get_connection
stock_accounting = sys.modules[f"{__package__}.modules.stock.core.accounting_client"]
stock_accounting.set_accounting_app(module_apps["accounting"])
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def start_modules():
    for module_app in module_apps.values():
        await module_app.router.startup()


@app.on_event("shutdown")
async def stop_modules():
    for module_app in reversed(list(module_apps.values())):
        await module_app.router.shutdown()


@app.get("/health")
def health():
    return {"status": "ok", "modules": sorted(module_apps)}


@app.get("/webhook/whatsapp")
def verify_whatsapp_webhook(request: Request):
    query = request.query_params
    if (
        query.get("hub.mode") == "subscribe"
        and hmac.compare_digest(
            query.get("hub.verify_token", ""),
            os.environ.get("WHATSAPP_VERIFY_TOKEN", ""),
        )
        and os.environ.get("WHATSAPP_VERIFY_TOKEN")
    ):
        return Response(content=query.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verificação inválida")


def _get_crm_contacts_for_phone(phone: str) -> list[dict] | None:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("244"):
        digits = digits[3:]
    if not digits:
        return None

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT company_id FROM usuarios
                   WHERE regexp_replace(COALESCE(whatsapp, ''), '[^0-9]', '', 'g') IN (%s, %s)
                   LIMIT 2""",
                (digits, f"244{digits}"),
            )
            users = cur.fetchall()
            if len(users) != 1 or not users[0]["company_id"]:
                return None

            cur.execute(
                """SELECT name, email, phone, company, stage FROM contacts
                   WHERE company_id = %s ORDER BY created_at DESC LIMIT 20""",
                (users[0]["company_id"],),
            )
            return cur.fetchall()


def _send_whatsapp_message(phone: str, message: str) -> None:
    response = httpx.post(
        f"https://graph.facebook.com/v25.0/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages",
        headers={"Authorization": f"Bearer {os.environ['WHATSAPP_ACCESS_TOKEN']}"},
        json={
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": message},
        },
        timeout=15,
    )
    response.raise_for_status()


def _process_whatsapp_message(phone: str, text: str, name: str) -> None:
    text = text.strip().lower()
    if text in {"ola", "olá"}:
        reply = f"Olá, {name}! Boas-vindas. O seu número é {phone}."
    elif text in {"/cliente", "/crm"}:
        try:
            contacts = _get_crm_contacts_for_phone(phone)
            if contacts is None:
                reply = "Este número de telefone não está autorizado a consultar o CRM."
            elif not contacts:
                reply = "Não há contactos no CRM da sua empresa."
            else:
                lines = []
                for index, contact in enumerate(contacts, start=1):
                    details = [contact.get(key) for key in ("phone", "email", "company", "stage") if contact.get(key)]
                    suffix = f" - {' | '.join(map(str, details))}" if details else ""
                    lines.append(f"{index}. {contact['name']}{suffix}")
                reply = "Contactos do CRM (até 20 mais recentes):\n" + "\n".join(lines)
        except Exception:
            logger.exception("Falha ao consultar contactos do CRM via WhatsApp")
            reply = "Não foi possível consultar o CRM neste momento."
    else:
        return

    try:
        _send_whatsapp_message(phone, reply)
    except Exception:
        logger.exception("Falha ao enviar resposta do WhatsApp")


@app.post("/webhook/whatsapp", status_code=200)
async def receive_whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    secret = os.environ.get("WHATSAPP_APP_SECRET", "")
    signature = request.headers.get("x-hub-signature-256", "")
    expected_signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=401, detail="Assinatura inválida")

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON inválido")

    value = payload.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
    message = (value.get("messages") or [{}])[0]
    if message.get("type") == "text":
        profile = (value.get("contacts") or [{}])[0].get("profile") or {}
        background_tasks.add_task(
            _process_whatsapp_message,
            message.get("from", ""),
            message.get("text", {}).get("body", ""),
            profile.get("name") or "utilizador",
        )
    return {"status": "processing"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5002)