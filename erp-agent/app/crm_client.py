"""
Cliente do módulo CRM, via proxy do web-service.

LEITURA (read_*) — chamadas diretamente pelas ferramentas do agente.
ESCRITA (write_*) — nunca chamadas diretamente pelo agente; só através de
uma proposta (ver agent.py) confirmada pelo utilizador.
"""
from __future__ import annotations

from app.api_client import request


def read_list_contacts(access_token: str) -> list[dict]:
    return request("GET", "crm", "/contacts", access_token)


def read_get_contact(access_token: str, contact_id: int) -> dict:
    return request("GET", "crm", f"/contacts/{contact_id}", access_token)


def write_create_contact(access_token: str, name: str, **fields) -> dict:
    body = {"name": name, **fields}
    return request("POST", "crm", "/contacts", access_token, json=body)


def write_update_contact(access_token: str, contact_id: int, **fields) -> dict:
    """Atualiza propriedades de um contacto/lead. Ação sensível — exige confirmação."""
    current = read_get_contact(access_token, contact_id)
    body = {**current, **fields}
    return request("PUT", "crm", f"/contacts/{contact_id}", access_token, json=body)


def write_update_contact_stage(access_token: str, contact_id: int, stage: str) -> dict:
    return request("PATCH", "crm", f"/contacts/{contact_id}/stage", access_token, json={"stage": stage})
