"""
Execução das tarefas/automações agendadas (ver app/tasks_store.py).

As automações do Kora só executam ações de LEITURA — compilam um relatório
a partir dos dados reais de RH/CRM. Nunca escrevem dados: qualquer alteração
continua a exigir uma proposta explícita confirmada por um humano no
momento em que é pedida (ver app/gate.py), nunca disparada sozinha por um
agendamento. Isto mantém a mesma garantia de segurança do resto do agente.

`run_due_tasks` é chamado periodicamente (agendador em segundo plano da UI
web, ver app/web.py, ou no início de cada turno da CLI, ver app/main.py) e
usa o token de acesso de uma sessão autenticada — sem sessão ativa no
momento em que uma tarefa vence, a execução fica adiada até à próxima
verificação com sessão ativa.
"""
from __future__ import annotations

import logging
from datetime import date

from app import crm_client, rh_client
from app.permissions import MODULE_CRM, MODULE_RH
from app.tasks_store import Task, due_tasks, record_run

logger = logging.getLogger(__name__)


def generate_report(module: str, access_token: str) -> str:
    if module == MODULE_RH:
        return _rh_report(access_token)
    if module == MODULE_CRM:
        return _crm_report(access_token)
    raise ValueError(f"módulo desconhecido: {module!r}")


def _rh_report(access_token: str) -> str:
    employees = rh_client.read_list_employees(access_token)
    departments = rh_client.read_list_departments(access_token)
    dept_names = {dept["id"]: dept["name"] for dept in departments}

    by_dept: dict[int | None, int] = {}
    for emp in employees:
        dept_id = emp.get("department_id")
        by_dept[dept_id] = by_dept.get(dept_id, 0) + 1

    linhas = [f"Relatório de RH — {date.today().isoformat()}", f"Total de colaboradores: {len(employees)}"]
    for dept_id, count in sorted(by_dept.items(), key=lambda item: -item[1]):
        nome = dept_names.get(dept_id, "sem departamento")
        linhas.append(f"  {nome}: {count}")
    return "\n".join(linhas)


def _crm_report(access_token: str) -> str:
    contacts = crm_client.read_list_contacts(access_token)
    by_stage: dict[str, int] = {}
    for contact in contacts:
        stage = contact.get("stage") or "sem estágio"
        by_stage[stage] = by_stage.get(stage, 0) + 1

    linhas = [f"Relatório de CRM — {date.today().isoformat()}", f"Total de contactos/leads: {len(contacts)}"]
    for stage, count in sorted(by_stage.items(), key=lambda item: -item[1]):
        linhas.append(f"  {stage}: {count}")
    return "\n".join(linhas)


def run_due_tasks(access_token: str, owner_id: str | None = None) -> list[Task]:
    """
    Executa as tarefas vencidas do `owner_id` (ou de todos, se omitido),
    usando o `access_token` fornecido (herda exatamente as mesmas
    permissões de módulo desse utilizador — se ele já não tiver acesso ao
    módulo da tarefa, o proxy do web-service recusa o pedido como recusaria
    a qualquer outra chamada do agente).

    Numa sessão multi-utilizador, cada utilizador com sessão ativa só
    executa as suas próprias tarefas, com o seu próprio token — nunca as
    de outro utilizador (ver o agendador em app/web.py).
    """
    executed = []
    for task in due_tasks(owner_id=owner_id):
        try:
            output = generate_report(task.module, access_token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao executar a tarefa #%s: %s", task.id, exc)
            output = f"Falha ao gerar o relatório: {exc}"
        record_run(task.id, output)
        executed.append(task)
    return executed
