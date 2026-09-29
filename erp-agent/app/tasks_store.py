"""
Persistência local (SQLite) para as tarefas/automações do Kora.

Diferente das ferramentas de RH/CRM (que falam sempre com os serviços reais
através do proxy do web-service), uma "tarefa agendada" é um conceito só do
agente: um pedido como "gera-me um relatório diário do RH" vira uma linha
nesta tabela, que o agendador (app/task_runner.py) verifica periodicamente e
executa quando chega a hora.

Cada tarefa pertence a um utilizador (owner_id) e a um módulo (people/crm) —
as ferramentas do agente em app/agent.py só deixam ver e gerir tarefas do
próprio utilizador, dentro dos módulos a que ele tem acesso.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app import config

FREQ_DAILY = "diaria"
FREQ_WEEKLY = "semanal"
FREQ_ONCE = "unica"
FREQUENCIES = {FREQ_DAILY, FREQ_WEEKLY, FREQ_ONCE}

STATUS_ACTIVE = "ativa"
STATUS_PAUSED = "pausada"
STATUS_CANCELLED = "cancelada"

WEEKDAYS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


@dataclass
class Task:
    id: int
    owner_id: str
    owner_nome: str
    module: str
    title: str
    frequency: str
    time_of_day: str
    weekday: int | None
    run_date: str | None
    status: str
    created_at: str
    next_run_at: str | None
    last_run_at: str | None
    last_output: str | None

    def as_dict(self) -> dict:
        return asdict(self)


def _db_path() -> Path:
    return Path(config.TASKS_DB_PATH)


@contextmanager
def _conn():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL,
                owner_nome TEXT NOT NULL,
                module TEXT NOT NULL,
                title TEXT NOT NULL,
                frequency TEXT NOT NULL,
                time_of_day TEXT NOT NULL,
                weekday INTEGER,
                run_date TEXT,
                status TEXT NOT NULL DEFAULT 'ativa',
                created_at TEXT NOT NULL,
                next_run_at TEXT,
                last_run_at TEXT,
                last_output TEXT
            )
            """
        )


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(**{key: row[key] for key in row.keys()})


def compute_next_run(
    frequency: str,
    time_of_day: str,
    weekday: int | None = None,
    run_date: str | None = None,
    from_dt: datetime | None = None,
) -> datetime | None:
    """Devolve a próxima data/hora de execução, ou None se não houver mais (ex: 'unica' já passada)."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"frequência inválida: {frequency!r}. Usa 'diaria', 'semanal' ou 'unica'.")

    from_dt = from_dt or datetime.now()
    hh, mm = (int(part) for part in time_of_day.split(":"))

    if frequency == FREQ_ONCE:
        if not run_date:
            raise ValueError("run_date é obrigatório para frequência 'unica' (formato YYYY-MM-DD)")
        candidate = datetime.combine(date.fromisoformat(run_date), time(hh, mm))
        return candidate if candidate > from_dt else None

    if frequency == FREQ_DAILY:
        candidate = datetime.combine(from_dt.date(), time(hh, mm))
        if candidate <= from_dt:
            candidate += timedelta(days=1)
        return candidate

    # FREQ_WEEKLY
    if weekday is None or not (0 <= weekday <= 6):
        raise ValueError("weekday (0=segunda .. 6=domingo) é obrigatório para frequência 'semanal'")
    candidate = datetime.combine(from_dt.date(), time(hh, mm))
    candidate += timedelta(days=(weekday - candidate.weekday()) % 7)
    if candidate <= from_dt:
        candidate += timedelta(days=7)
    return candidate


def create_task(
    owner_id: str,
    owner_nome: str,
    module: str,
    title: str,
    frequency: str,
    time_of_day: str,
    weekday: int | None = None,
    run_date: str | None = None,
) -> Task:
    next_run = compute_next_run(frequency, time_of_day, weekday, run_date)
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks
                (owner_id, owner_nome, module, title, frequency, time_of_day, weekday, run_date,
                 status, created_at, next_run_at, last_run_at, last_output)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                owner_id, owner_nome, module, title, frequency, time_of_day, weekday, run_date,
                STATUS_ACTIVE, now, next_run.isoformat(timespec="seconds") if next_run else None,
            ),
        )
        task_id = cur.lastrowid
    return get_task(task_id)


def get_task(task_id: int) -> Task | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def list_tasks(owner_id: str, module: str | None = None, include_cancelled: bool = False) -> list[Task]:
    query = "SELECT * FROM tasks WHERE owner_id = ?"
    params: list = [owner_id]
    if module:
        query += " AND module = ?"
        params.append(module)
    if not include_cancelled:
        query += " AND status != ?"
        params.append(STATUS_CANCELLED)
    query += " ORDER BY id DESC"
    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_task(row) for row in rows]


def update_task(task_id: int, **fields) -> Task:
    """Atualiza title/frequency/time_of_day/weekday/run_date e recalcula next_run_at."""
    task = get_task(task_id)
    if task is None:
        raise ValueError(f"tarefa #{task_id} não encontrada")

    allowed = {"title", "frequency", "time_of_day", "weekday", "run_date"}
    merged = task.as_dict()
    for key, value in fields.items():
        if key in allowed and value is not None:
            merged[key] = value

    next_run = compute_next_run(merged["frequency"], merged["time_of_day"], merged.get("weekday"), merged.get("run_date"))
    with _conn() as conn:
        conn.execute(
            """
            UPDATE tasks
               SET title = ?, frequency = ?, time_of_day = ?, weekday = ?, run_date = ?, next_run_at = ?
             WHERE id = ?
            """,
            (
                merged["title"], merged["frequency"], merged["time_of_day"], merged.get("weekday"),
                merged.get("run_date"), next_run.isoformat(timespec="seconds") if next_run else None, task_id,
            ),
        )
    return get_task(task_id)


def set_status(task_id: int, status: str) -> Task:
    task = get_task(task_id)
    if task is None:
        raise ValueError(f"tarefa #{task_id} não encontrada")

    next_run_at = task.next_run_at
    if status == STATUS_ACTIVE and not next_run_at:
        next_run = compute_next_run(task.frequency, task.time_of_day, task.weekday, task.run_date)
        next_run_at = next_run.isoformat(timespec="seconds") if next_run else None

    with _conn() as conn:
        conn.execute("UPDATE tasks SET status = ?, next_run_at = ? WHERE id = ?", (status, next_run_at, task_id))
    return get_task(task_id)


def delete_task(task_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


def due_tasks(now: datetime | None = None, owner_id: str | None = None) -> list[Task]:
    now = now or datetime.now()
    query = "SELECT * FROM tasks WHERE status = ? AND next_run_at IS NOT NULL AND next_run_at <= ?"
    params: list = [STATUS_ACTIVE, now.isoformat(timespec="seconds")]
    if owner_id is not None:
        query += " AND owner_id = ?"
        params.append(str(owner_id))
    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_task(row) for row in rows]


def record_run(task_id: int, output: str, ran_at: datetime | None = None) -> None:
    ran_at = ran_at or datetime.now()
    task = get_task(task_id)
    if task is None:
        return

    next_run = compute_next_run(task.frequency, task.time_of_day, task.weekday, task.run_date, from_dt=ran_at)
    status = STATUS_CANCELLED if (task.frequency == FREQ_ONCE and next_run is None) else task.status
    with _conn() as conn:
        conn.execute(
            "UPDATE tasks SET last_run_at = ?, last_output = ?, next_run_at = ?, status = ? WHERE id = ?",
            (
                ran_at.isoformat(timespec="seconds"), output,
                next_run.isoformat(timespec="seconds") if next_run else None, status, task_id,
            ),
        )
