import os
import httpx

ACCOUNTING_SERVICE_URL = os.environ.get("ACCOUNTING_SERVICE_URL", "http://ms_accounting:5007")


def post_journal_entry(token: str, entry_date, description: str, lines: list[dict], source_type: str, source_id: int):
    """Lança um registo contabilístico correspondente a um evento financeiro
    (factura emitida, recibo recebido, despesa paga). Melhor-esforço: se o
    accounting-service estiver em baixo, a operação em finance-service não
    pode falhar por causa disto — falha-se silenciosamente aqui."""
    try:
        with httpx.Client(timeout=3.0) as client:
            client.post(
                f"{ACCOUNTING_SERVICE_URL}/entries",
                json={
                    "entry_date": entry_date,
                    "description": description,
                    "lines": lines,
                    "source": "finance",
                    "source_type": source_type,
                    "source_id": source_id,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError:
        pass
