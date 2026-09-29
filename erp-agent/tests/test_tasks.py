"""
Testes das automações (tarefas agendadas) — persistência local (SQLite),
cálculo determinístico da próxima execução, e visibilidade das ferramentas
consoante os módulos do utilizador. Não dependem de rede nem de LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from app import config, tasks_store
from app.agent import AgentDeps, agent
from app.permissions import MODULE_CRM, MODULE_RH, CurrentUser


@dataclass
class _FakeCtx:
    deps: AgentDeps


async def _visible_tool_names(user: CurrentUser | None) -> set[str]:
    deps = AgentDeps(access_token="tok", user=user)
    ctx = _FakeCtx(deps=deps)
    names = set()
    for name, tool in agent._function_toolset.tools.items():
        if tool.prepare is None:
            names.add(name)
            continue
        tool_def = await tool.prepare(ctx, tool.tool_def)
        if tool_def is not None:
            names.add(name)
    return names


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TASKS_DB_PATH", str(tmp_path / "tasks.db"))
    tasks_store.init_db()


def test_compute_next_run_diaria_agenda_para_hoje_se_ainda_nao_passou():
    from_dt = datetime(2026, 7, 15, 8, 0)
    next_run = tasks_store.compute_next_run("diaria", "09:00", from_dt=from_dt)
    assert next_run == datetime(2026, 7, 15, 9, 0)


def test_compute_next_run_diaria_agenda_para_amanha_se_ja_passou():
    from_dt = datetime(2026, 7, 15, 10, 0)
    next_run = tasks_store.compute_next_run("diaria", "09:00", from_dt=from_dt)
    assert next_run == datetime(2026, 7, 16, 9, 0)


def test_compute_next_run_semanal_respeita_dia_da_semana():
    # 2026-07-15 é quarta-feira (weekday=2); pedir sexta (weekday=4)
    from_dt = datetime(2026, 7, 15, 8, 0)
    next_run = tasks_store.compute_next_run("semanal", "09:00", weekday=4, from_dt=from_dt)
    assert next_run == datetime(2026, 7, 17, 9, 0)


def test_compute_next_run_unica_no_passado_devolve_none():
    next_run = tasks_store.compute_next_run(
        "unica", "09:00", run_date="2020-01-01", from_dt=datetime(2026, 7, 15, 8, 0)
    )
    assert next_run is None


def test_compute_next_run_semanal_sem_weekday_falha():
    with pytest.raises(ValueError):
        tasks_store.compute_next_run("semanal", "09:00")


def test_create_e_list_tasks():
    task = tasks_store.create_task(
        owner_id="1", owner_nome="Ana", module=MODULE_RH, title="Relatório diário de RH",
        frequency="diaria", time_of_day="09:00",
    )
    assert task.id is not None
    assert task.status == tasks_store.STATUS_ACTIVE
    assert task.next_run_at is not None

    tasks = tasks_store.list_tasks(owner_id="1")
    assert len(tasks) == 1
    assert tasks[0].title == "Relatório diário de RH"

    # não aparece para outro utilizador
    assert tasks_store.list_tasks(owner_id="2") == []


def test_pausar_e_retomar_tarefa():
    task = tasks_store.create_task(
        owner_id="1", owner_nome="Ana", module=MODULE_CRM, title="Resumo CRM",
        frequency="diaria", time_of_day="09:00",
    )
    paused = tasks_store.set_status(task.id, tasks_store.STATUS_PAUSED)
    assert paused.status == tasks_store.STATUS_PAUSED

    resumed = tasks_store.set_status(task.id, tasks_store.STATUS_ACTIVE)
    assert resumed.status == tasks_store.STATUS_ACTIVE
    assert resumed.next_run_at is not None


def test_apagar_tarefa_remove_definitivamente():
    task = tasks_store.create_task(
        owner_id="1", owner_nome="Ana", module=MODULE_RH, title="X", frequency="diaria", time_of_day="09:00",
    )
    tasks_store.delete_task(task.id)
    assert tasks_store.get_task(task.id) is None


def test_due_tasks_so_devolve_tarefas_ativas_e_vencidas():
    passado = tasks_store.create_task(
        owner_id="1", owner_nome="Ana", module=MODULE_RH, title="Vencida",
        frequency="unica", time_of_day="09:00", run_date="2020-01-01",
    )
    # forçar next_run_at para o passado (compute_next_run já teria dado None; simulamos manualmente)
    with tasks_store._conn() as conn:  # noqa: SLF001
        conn.execute("UPDATE tasks SET next_run_at = ? WHERE id = ?", ("2020-01-01T09:00:00", passado.id))

    futuro = tasks_store.create_task(
        owner_id="1", owner_nome="Ana", module=MODULE_RH, title="Futura",
        frequency="unica", time_of_day="09:00", run_date="2099-01-01",
    )

    devidas = tasks_store.due_tasks(now=datetime(2026, 7, 15))
    ids = {t.id for t in devidas}
    assert passado.id in ids
    assert futuro.id not in ids


def test_record_run_unica_cancela_apos_execucao():
    task = tasks_store.create_task(
        owner_id="1", owner_nome="Ana", module=MODULE_RH, title="Só uma vez",
        frequency="unica", time_of_day="09:00", run_date="2026-07-16",
    )
    tasks_store.record_run(task.id, "relatório gerado", ran_at=datetime(2026, 7, 16, 9, 5))
    updated = tasks_store.get_task(task.id)
    assert updated.status == tasks_store.STATUS_CANCELLED
    assert updated.last_output == "relatório gerado"
    assert updated.next_run_at is None


def test_record_run_diaria_reagenda_para_o_dia_seguinte():
    task = tasks_store.create_task(
        owner_id="1", owner_nome="Ana", module=MODULE_RH, title="Diária",
        frequency="diaria", time_of_day="09:00",
    )
    tasks_store.record_run(task.id, "ok", ran_at=datetime(2026, 7, 15, 9, 1))
    updated = tasks_store.get_task(task.id)
    assert updated.status == tasks_store.STATUS_ACTIVE
    assert updated.next_run_at == "2026-07-16T09:00:00"


@pytest.mark.anyio
async def test_ferramentas_de_automacao_pedem_pelo_menos_um_modulo():
    assert await _visible_tool_names(user=None) == set()

    crm_user = CurrentUser(id="1", nome="Rita", email="rita@x.com", is_admin=False, modules=[MODULE_CRM])
    visiveis = await _visible_tool_names(crm_user)
    assert "automation_list_tasks" in visiveis
    assert "propose_create_automation" in visiveis
    assert "propose_delete_automation" in visiveis
