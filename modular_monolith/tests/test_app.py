from fastapi import FastAPI
from fastapi.testclient import TestClient
import hashlib
import hmac
import json
import os
import sys

from modular_monolith.app import app, module_apps


def test_health_reports_loaded_domains():
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["modules"] == ["accounting", "auth", "crm", "documents", "rh", "stock"]


def test_whatsapp_webhook_verification():
    os.environ["WHATSAPP_VERIFY_TOKEN"] = "verify-test"
    response = TestClient(app).get("/webhook/whatsapp", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "verify-test",
        "hub.challenge": "challenge-test",
    })

    assert response.status_code == 200
    assert response.text == "challenge-test"


def test_whatsapp_webhook_rejects_unsigned_events():
    os.environ["WHATSAPP_APP_SECRET"] = "app-secret-test"
    response = TestClient(app).post("/webhook/whatsapp", json={})

    assert response.status_code == 401


def test_whatsapp_webhook_accepts_signed_event(monkeypatch):
    secret = "app-secret-test"
    monkeypatch.setenv("WHATSAPP_APP_SECRET", secret)
    received = []
    monkeypatch.setattr(
        "modular_monolith.app._process_whatsapp_message",
        lambda *args: received.append(args),
    )
    payload = {
        "entry": [{"changes": [{"value": {
            "messages": [{"type": "text", "from": "244923456789", "text": {"body": "/crm"}}],
            "contacts": [{"profile": {"name": "Carlos"}}],
        }}]}]
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    response = TestClient(app).post(
        "/webhook/whatsapp",
        content=body,
        headers={"content-type": "application/json", "x-hub-signature-256": signature},
    )

    assert response.status_code == 200
    assert received == [("244923456789", "/crm", "Carlos")]


def test_proxy_dispatches_to_module_in_process():
    module = FastAPI()

    @module.get("/probe")
    def probe():
        return {"handled_by": "local-module"}

    original = module_apps["auth"]
    module_apps["auth"] = module
    try:
        response = TestClient(app).get("/api/auth/probe")
    finally:
        module_apps["auth"] = original

    assert response.status_code == 200
    assert response.json() == {"handled_by": "local-module"}


def test_disabled_domain_does_not_fall_back_to_network():
    response = TestClient(app).get("/api/finance/invoices")

    assert response.status_code == 404


def test_stock_accounting_calls_are_dispatched_in_process():
    accounting_app = FastAPI()
    calls = []

    @accounting_app.post("/accounts")
    def create_account(payload: dict):
        calls.append(("account", payload))
        return {"ok": True}

    @accounting_app.post("/entries")
    def create_entry(payload: dict):
        calls.append(("entry", payload))
        return {"ok": True}

    stock_client = sys.modules[
        "modular_monolith.modules.stock.core.accounting_client"
    ]
    original = stock_client._ACCOUNTING_APP
    stock_client.set_accounting_app(accounting_app)
    invoker = FastAPI()

    @invoker.post("/trigger")
    def trigger_accounting():
        stock_client.post_journal_entry("token", "2026-01-01", "Teste", [], "movement", 1)
        return {"ok": True}

    try:
        response = TestClient(invoker).post("/trigger")
    finally:
        stock_client.set_accounting_app(original)

    assert response.status_code == 200
    assert [kind for kind, _ in calls] == ["account", "account", "entry"]