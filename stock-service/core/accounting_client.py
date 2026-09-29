import os

import anyio
import httpx

ACCOUNTING_SERVICE_URL = os.environ.get("ACCOUNTING_SERVICE_URL", "http://ms_accounting:5007")
_ACCOUNTING_APP = None


def set_accounting_app(app) -> None:
    global _ACCOUNTING_APP
    _ACCOUNTING_APP = app


def _post_in_process(path: str, payload: dict, token: str) -> None:
    async def send():
        transport = httpx.ASGITransport(app=_ACCOUNTING_APP, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://accounting.internal") as client:
            await client.post(path, json=payload, headers={"Authorization": f"Bearer {token}"})

    anyio.from_thread.run(send)

STOCK_ACCOUNT_CODE = "1.2"
STOCK_ACCOUNT_NAME = "Estoques"
CMV_ACCOUNT_CODE = "5.2"
CMV_ACCOUNT_NAME = "Custo das Mercadorias Vendidas"
SUPPLIERS_ACCOUNT_CODE = "2.1.1"


def _ensure_account(token: str, code: str, name: str, account_class: str) -> None:
    """O plano de contas por omissão do accounting-service não tem contas de
    estoque — criam-se aqui, sob demanda, na primeira vez que há um movimento
    com impacto contabilístico. Idempotente: um 409 (código já existe) é o
    caminho normal a partir da segunda chamada."""
    payload = {"code": code, "name": name, "account_class": account_class, "account_type": "analitica"}
    if _ACCOUNTING_APP is not None:
        _post_in_process("/accounts", payload, token)
        return

    try:
        with httpx.Client(timeout=3.0) as client:
            client.post(
                f"{ACCOUNTING_SERVICE_URL}/accounts",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError:
        pass


def post_journal_entry(token: str, entry_date, description: str, lines: list[dict], source_type: str, source_id: int):
    """Lança um registo contabilístico correspondente a um movimento de stock com
    impacto financeiro (entrada com custo, saída de venda). Melhor-esforço: se o
    accounting-service estiver em baixo, a operação em stock-service não pode
    falhar por causa disto — falha-se silenciosamente aqui, tal como em
    finance-service/core/accounting_client.py."""
    _ensure_account(token, STOCK_ACCOUNT_CODE, STOCK_ACCOUNT_NAME, "ativo")
    _ensure_account(token, CMV_ACCOUNT_CODE, CMV_ACCOUNT_NAME, "despesa")
    payload = {
        "entry_date": entry_date,
        "description": description,
        "lines": lines,
        "source": "stock",
        "source_type": source_type,
        "source_id": source_id,
    }
    if _ACCOUNTING_APP is not None:
        _post_in_process("/entries", payload, token)
        return

    try:
        with httpx.Client(timeout=3.0) as client:
            client.post(
                f"{ACCOUNTING_SERVICE_URL}/entries",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError:
        pass
