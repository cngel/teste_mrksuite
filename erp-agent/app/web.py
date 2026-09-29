"""
API do agente Kora, consumida pelo dashboard (web-service) através do
proxy /api/kora/... (ver web-service/app.py). Sem ecrã de login próprio:
sincroniza automaticamente com a sessão já autenticada no dashboard (ver
_get_session mais abaixo).

Uso:
    python -m app.web
    # API em http://127.0.0.1:8010
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import date

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import auth_client, config, task_runner, tasks_store
from app.agent import AgentDeps, agent
from app.api_client import ApiError, PermissionDenied
from app.auth_client import AuthError
from app.context_builder import build_context_preamble
from app.gate import PendingAction
from app.permissions import CurrentUser

logger = logging.getLogger(__name__)

app = FastAPI(title="Kora API")

# O dashboard (web-service, noutra origem) chama esta API directamente do
# browser — sem isto o CORS bloqueia mesmo os pedidos correctos.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("KORA_CORS_ORIGINS", "http://localhost:5002,http://127.0.0.1:5002").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# Sessões multi-utilizador, sincronizadas automaticamente com o dashboard:
# não há ecrã de login próprio da Kora. Cada pedido chega com o mesmo
# Bearer token da sessão já autenticada na plataforma (reencaminhado pelo
# proxy /api/kora/... do web-service, ver web-service/app.py); a sessão da
# Kora para esse token é criada lazily no primeiro pedido e reaproveitada
# nos seguintes — cada utilizador com o seu próprio histórico de conversa,
# gate de confirmação e propostas pendentes, sem interferirem uns com os
# outros. Estado guardado em memória (perde-se num restart do container) e
# expira ao fim de SESSION_IDLE_TTL sem pedidos, para não crescer sem fim.
# --------------------------------------------------------------------------
SESSION_IDLE_TTL = 60 * 60 * 12  # 12h sem pedidos
KORA_HISTORY_LIMIT = 32  # mensagens pydantic_ai guardadas (cobre com folga >= 8 turnos de conversa)

_sessions: dict[str, tuple[float, AgentDeps]] = {}
_pending: dict[str, dict[str, PendingAction]] = {}
_lock = threading.Lock()


def _get_session(request: Request) -> tuple[str, AgentDeps]:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sem sessão autenticada.")
    token = auth_header[7:]

    now = time.time()
    with _lock:
        cached = _sessions.get(token)
        if cached is not None:
            _sessions[token] = (now, cached[1])
            return token, cached[1]

    try:
        user = auth_client.me(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    deps = AgentDeps(access_token=token, user=user)
    with _lock:
        _sessions[token] = (now, deps)
        _pending[token] = {}
    return token, deps


def _scheduler_loop() -> None:
    """
    Verifica periodicamente se há automações vencidas e executa-as, e
    liberta sessões inativas há mais de SESSION_IDLE_TTL. Cada utilizador
    com sessão ativa executa só as suas próprias tarefas, com o seu
    próprio token — nunca as de outro utilizador. Tarefas de utilizadores
    sem sessão ativa no momento em que vencem ficam à espera da próxima
    verificação com esse utilizador autenticado.
    """
    while True:
        time.sleep(config.TASKS_POLL_INTERVAL)
        now = time.time()
        with _lock:
            expirados = [tok for tok, (last_seen, _) in _sessions.items() if now - last_seen > SESSION_IDLE_TTL]
            for tok in expirados:
                _sessions.pop(tok, None)
                _pending.pop(tok, None)
            sessions_snapshot = [deps for _, deps in _sessions.values()]
        for deps in sessions_snapshot:
            if deps.user is None:
                continue
            try:
                executadas = task_runner.run_due_tasks(deps.access_token, owner_id=str(deps.user.id))
                for task in executadas:
                    logger.info("Automação #%s ('%s') executada.", task.id, task.title)
            except Exception:  # noqa: BLE001
                logger.exception("Falha no agendador de automações.")


threading.Thread(target=_scheduler_loop, daemon=True).start()


class ChatBody(BaseModel):
    message: str


class ConfirmBody(BaseModel):
    approve: bool


@app.get("/api/session")
def session_info(request: Request):
    _, deps = _get_session(request)
    return {"nome": deps.user.nome, "is_admin": deps.user.is_admin, "modules": deps.user.modules}


@app.post("/api/chat")
async def chat(body: ChatBody, request: Request):
    token, deps = _get_session(request)

    # Atualiza identidade/módulos em cada mensagem: a sessão em memória pode
    # ter um snapshot antigo (ex: admin acabou de atribuir people/crm) e o
    # Kora recusaria pedidos que as tools já permitiriam.
    try:
        deps.user = auth_client.me(token)
        deps.access_token = token
    except AuthError as exc:
        with _lock:
            _sessions.pop(token, None)
            _pending.pop(token, None)
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    preamble = build_context_preamble(body.message, today=date.today())
    full_prompt = f"{preamble}\n\nMensagem do utilizador: {body.message}" if preamble else body.message
    try:
        result = await agent.run(full_prompt, deps=deps, message_history=deps.message_history)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Erro ao contactar o modelo ({config.MODEL_PROVIDER}): {exc}",
        ) from exc

    # Mantém as últimas KORA_HISTORY_LIMIT mensagens (pedidos + respostas do
    # modelo) para a Kora se lembrar do contexto de turnos anteriores —
    # cobre confortavelmente pelo menos as últimas 8 mensagens da conversa.
    deps.message_history = result.all_messages()[-KORA_HISTORY_LIMIT:]

    pendentes = []
    while deps.pending_actions:
        action = deps.pending_actions.pop(0)
        action_id = str(uuid.uuid4())
        with _lock:
            _pending[token][action_id] = action
        pendentes.append({"id": action_id, "description": action.description})

    return {"reply": result.output, "pending": pendentes}


@app.post("/api/confirm/{action_id}")
def confirm(action_id: str, body: ConfirmBody, request: Request):
    token, deps = _get_session(request)

    with _lock:
        action = _pending.get(token, {}).pop(action_id, None)
    if action is None:
        raise HTTPException(status_code=404, detail="Ação não encontrada (já processada?).")

    if not body.approve:
        deps.gate.reject(action)
        return {"status": "rejeitada"}

    try:
        resultado = deps.gate.confirm(action)
        return {"status": "confirmada", "resultado": resultado}
    except PermissionDenied as exc:
        return {"status": "erro", "mensagem": f"Sem permissão: {exc}"}
    except ApiError as exc:
        return {"status": "erro", "mensagem": f"Erro no backend: {exc}"}


@app.get("/api/log")
def log(request: Request):
    _, deps = _get_session(request)
    return deps.gate.history()


# --------------------------------------------------------------------------
# Automações (tarefas agendadas) — API direta, sem passar pelo agente/LLM.
# Chamada diretamente por um utilizador autenticado (não por uma proposta do
# Kora), por isso executa de imediato — não há alucinação a mitigar aqui,
# ao contrário do fluxo de chat. Continua sujeita às mesmas regras de posse
# e de módulo que as ferramentas do agente (ver app/agent.py).
# --------------------------------------------------------------------------

class CreateTaskBody(BaseModel):
    module: str
    title: str
    frequency: str
    time_of_day: str
    weekday: int | None = None
    run_date: str | None = None


class UpdateTaskBody(BaseModel):
    title: str | None = None
    frequency: str | None = None
    time_of_day: str | None = None
    weekday: int | None = None
    run_date: str | None = None


def _current_user(request: Request) -> CurrentUser:
    _, deps = _get_session(request)
    if deps.user is None:
        raise HTTPException(status_code=401, detail="Faz login primeiro.")
    return deps.user


def _owned_task(task_id: int, user: CurrentUser) -> tasks_store.Task:
    task = tasks_store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Automação não encontrada.")
    if str(task.owner_id) != str(user.id) and not user.is_admin:
        # não revela que a tarefa existe e pertence a outro utilizador
        raise HTTPException(status_code=404, detail="Automação não encontrada.")
    if not user.has_module(task.module):
        raise HTTPException(status_code=403, detail="Sem acesso a este módulo.")
    return task


@app.get("/api/tasks")
def list_tasks_route(request: Request, module: str | None = None, include_cancelled: bool = False):
    user = _current_user(request)
    tasks = tasks_store.list_tasks(owner_id=str(user.id), module=module, include_cancelled=include_cancelled)
    return [task.as_dict() for task in tasks]


@app.post("/api/tasks", status_code=201)
def create_task_route(body: CreateTaskBody, request: Request):
    user = _current_user(request)
    if not user.has_module(body.module):
        raise HTTPException(status_code=403, detail="Sem acesso a este módulo.")
    try:
        task = tasks_store.create_task(
            owner_id=str(user.id), owner_nome=user.nome, module=body.module, title=body.title,
            frequency=body.frequency, time_of_day=body.time_of_day, weekday=body.weekday, run_date=body.run_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return task.as_dict()


@app.get("/api/tasks/{task_id}")
def get_task_route(task_id: int, request: Request):
    user = _current_user(request)
    return _owned_task(task_id, user).as_dict()


@app.put("/api/tasks/{task_id}")
def update_task_route(task_id: int, body: UpdateTaskBody, request: Request):
    user = _current_user(request)
    _owned_task(task_id, user)
    try:
        task = tasks_store.update_task(task_id, **body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return task.as_dict()


@app.post("/api/tasks/{task_id}/pause")
def pause_task_route(task_id: int, request: Request):
    user = _current_user(request)
    _owned_task(task_id, user)
    return tasks_store.set_status(task_id, tasks_store.STATUS_PAUSED).as_dict()


@app.post("/api/tasks/{task_id}/resume")
def resume_task_route(task_id: int, request: Request):
    user = _current_user(request)
    _owned_task(task_id, user)
    return tasks_store.set_status(task_id, tasks_store.STATUS_ACTIVE).as_dict()


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task_route(task_id: int, request: Request):
    user = _current_user(request)
    _owned_task(task_id, user)
    return tasks_store.set_status(task_id, tasks_store.STATUS_CANCELLED).as_dict()


@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task_route(task_id: int, request: Request):
    user = _current_user(request)
    _owned_task(task_id, user)
    tasks_store.delete_task(task_id)


if __name__ == "__main__":
    uvicorn.run("app.web:app", host="127.0.0.1", port=8010, reload=True)
