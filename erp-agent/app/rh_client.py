"""
Cliente do módulo RH (people), via proxy do web-service.

LEITURA (read_*) — chamadas diretamente pelas ferramentas do agente.
ESCRITA (write_*) — nunca chamadas diretamente pelo agente; só através de
uma proposta (ver agent.py) confirmada pelo utilizador, tal como as
ferramentas de escrita do ERP mock.
"""
from __future__ import annotations

from app.api_client import request


def read_list_departments(access_token: str) -> list[dict]:
    return request("GET", "rh", "/departments", access_token)


def read_list_employees(access_token: str, department_id: int | None = None) -> list[dict]:
    params = {"department_id": department_id} if department_id else None
    return request("GET", "rh", "/employees", access_token, params=params)


def read_get_employee(access_token: str, employee_id: int) -> dict:
    return request("GET", "rh", f"/employees/{employee_id}", access_token)


def read_list_leaves(access_token: str, employee_id: int) -> list[dict]:
    return request("GET", "rh", f"/employees/{employee_id}/leave", access_token)


def write_update_employee(access_token: str, employee_id: int, **fields) -> dict:
    """Atualiza propriedades de um colaborador. Ação sensível — exige confirmação."""
    current = read_get_employee(access_token, employee_id)
    body = {**current, **fields}
    return request("PUT", "rh", f"/employees/{employee_id}", access_token, json=body)


def write_update_employee_status(access_token: str, employee_id: int, status: str) -> dict:
    return request("PATCH", "rh", f"/employees/{employee_id}/status", access_token, json={"status": status})


def write_create_leave(access_token: str, employee_id: int, start_date: str, end_date: str, reason: str | None = None) -> dict:
    body = {"start_date": start_date, "end_date": end_date, "reason": reason}
    return request("POST", "rh", f"/employees/{employee_id}/leave", access_token, json=body)


def write_create_department(access_token: str, name: str, color: str = "#6B7280") -> dict:
    return request("POST", "rh", "/departments", access_token, json={"name": name, "color": color})


def write_update_department(access_token: str, department_id: int, name: str, color: str = "#6B7280") -> dict:
    return request("PUT", "rh", f"/departments/{department_id}", access_token, json={"name": name, "color": color})
