"""
Agente principal.

Liga um modelo de linguagem — Gemma via Ollama (local) ou Groq/Gemini/
Anthropic (cloud), consoante MODEL_PROVIDER — às ferramentas do ERP,
seguindo a arquitetura discutida:

  - Ferramentas de LEITURA (read_*) são chamadas livremente pelo agente.
  - Ferramentas de ESCRITA (write_*) nunca são chamadas diretamente: o
    agente só pode "propor" a ação (propose_*). A execução real fica
    sempre dependente de confirmação humana explícita fora do agente
    (ver main.py).

As ferramentas de RH e CRM falam com os serviços reais (rh-service,
crm-service) através do proxy do web-service, usando o token do
utilizador autenticado. Só ficam disponíveis para o modelo se esse
utilizador tiver o módulo correspondente atribuído ("people" para RH,
"crm" para CRM) ou for admin da empresa — ver `_require_module` e
app/permissions.py. Mesmo que o modelo tente contornar isto, o proxy do
web-service valida o mesmo módulo de novo no lado do servidor.

Para correr com Ollama (por omissão):
  1. Instala o Ollama: https://ollama.com
  2. `ollama pull gemma4:e4b`   (ajusta o nome se o teu tag for diferente)
  3. `ollama serve`             (normalmente já corre em background)
  4. Sobe o backend: `docker compose up -d` (a partir da raiz do projeto)
  5. `python -m app.main`

Para usar um provedor cloud, define no .env:
  MODEL_PROVIDER=groq|gemini|anthropic
e a respetiva API key (GROQ_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition

from app import config, crm_client, rh_client, tasks_store
from app.api_client import ApiError, PermissionDenied
from app.gate import ConfirmationGate, PendingAction
from app.permissions import MODULE_CRM, MODULE_RH, CurrentUser

tasks_store.init_db()


@dataclass
class AgentDeps:
    """Estado partilhado entre o agente e as suas ferramentas durante uma execução."""
    gate: ConfirmationGate = field(default_factory=ConfirmationGate)
    pending_actions: list[PendingAction] = field(default_factory=list)
    access_token: str = ""
    user: CurrentUser | None = None
    # Histórico da conversa (ver pydantic_ai message_history), para o agente
    # se lembrar de turnos anteriores em vez de tratar cada mensagem como
    # isolada. Mantido pelo chamador (ver app/web.py) entre pedidos da
    # mesma sessão.
    message_history: list[ModelMessage] = field(default_factory=list)


def _build_model() -> Model:
    """Constrói o modelo de acordo com MODEL_PROVIDER (ver app/config.py)."""
    if config.MODEL_PROVIDER == "ollama":
        provider = OpenAIProvider(base_url=config.OLLAMA_BASE_URL, api_key=config.FAKE_API_KEY)
        return OpenAIChatModel(model_name=config.MODEL_NAME, provider=provider)

    if config.MODEL_PROVIDER == "groq":
        from pydantic_ai.models.groq import GroqModel
        from pydantic_ai.providers.groq import GroqProvider

        return GroqModel(config.GROQ_MODEL_NAME, provider=GroqProvider(api_key=config.GROQ_API_KEY or None))

    if config.MODEL_PROVIDER == "gemini":
        return GoogleModel(config.GEMINI_MODEL_NAME, provider=GoogleProvider(api_key=config.GEMINI_API_KEY or None))

    if config.MODEL_PROVIDER == "anthropic":
        return AnthropicModel(
            config.ANTHROPIC_MODEL_NAME, provider=AnthropicProvider(api_key=config.ANTHROPIC_API_KEY or None)
        )

    raise ValueError(
        f"MODEL_PROVIDER desconhecido: {config.MODEL_PROVIDER!r}. "
        "Usa 'ollama', 'groq', 'gemini' ou 'anthropic'."
    )


SYSTEM_PROMPT = """\
És um assistente de gestão empresarial (ERP) em português, com o nome Kora.
Age: quando o pedido couber nas tuas capacidades deste turno, cumpre-o —
consulta ou propõe a alteração. Não recuses por cautela desnecessária.

REGRAS ESTRITAS:
1. NUNCA calcules datas relativas (ex: "semana passada", "ontem") por ti
   próprio. Se o contexto fornecido já tiver datas resolvidas, usa-as
   exatamente como estão. Se não houver datas resolvidas e a pergunta
   precisar de um período de tempo, pergunta ao utilizador para
   especificar as datas exatas.
2. Para CONSULTAS (listar, calcular, mostrar), usa as ferramentas de
   leitura disponíveis diretamente e responde com os dados reais.
3. Para AÇÕES QUE ALTERAM DADOS (alterar um colaborador, registar uma
   ausência, alterar um contacto/lead do CRM, criar/editar automações,
   etc.): NÃO executes a escrita final sozinho — usa sempre as
   ferramentas "propose_*", que registam uma proposta para o utilizador
   confirmar. Isso NÃO é uma recusa: é a forma correcta de cumprir o
   pedido. Explica o que fica pendente de confirmação, com naturalidade.
4. Se faltar informação essencial (ID do colaborador/contacto,
   departamento, datas), pergunta antes de agir. Não assumas valores. Se o
   utilizador referir algo pelo nome em vez do ID (ex: "o contacto Empresa
   X", "a automação Resumo diário CRM"), usa primeiro uma ferramenta de
   leitura/listagem para encontrar o ID certo — só pergunta ao utilizador
   se não conseguires encontrá-lo assim. NUNCA inventes um ID nem escrevas
   uma chamada de função como texto na tua resposta; chama sempre a
   ferramenta a sério.
5. NUNCA fales com o utilizador em termos técnicos: não menciones
   "ferramentas", "funções", nomes internos (ex: "propose_*", "rh_*"),
   módulos, permissões técnicas ou erros de sistema.
6. ANTI-RECUSA FALSA (crítica): a lista de capacidades do turno (instrução
   de contexto abaixo) e as ferramentas que te forem apresentadas definem
   o que podes fazer. Se o pedido couber aí, USA a ferramenta — NUNCA
   digas "não posso", "não tenho acesso", "não faço isso" ou "isso não é
   comigo". Só recusa quando o pedido estiver claramente FORA dessa lista
   (ex: facturação, finanças ou projetos se não constarem nas capacidades).
   Em caso de dúvida entre agir e recusar, age (consulta ou propõe).
7. Também sabes criar automações (tarefas agendadas) que geram relatórios
   periódicos de RH ou CRM — por exemplo "envia-me todos os dias um
   resumo do RH" — quando as automações estiverem nas tuas capacidades.
   Usa sempre as ferramentas de automação (criar, editar, pausar,
   retomar, cancelar, apagar) como proposta. As automações só geram
   relatórios a partir dos dados existentes; nunca alteram dados sozinhas.
8. Responde sempre em português, de forma natural e profissional.
"""

# Capacidades em linguagem de negócio (espelham as tools filtradas por módulo).
# Injetadas no system prompt dinâmico para o modelo saber o que PODE fazer
# neste turno — mitiga recusas falsas do tipo "não posso" quando a tool existe.
_CAPABILITIES_RH = (
    "listar departamentos e colaboradores",
    "consultar a ficha completa de um colaborador",
    "listar ausências/licenças de um colaborador",
    "propor alterações a dados de um colaborador (incluindo estado)",
    "propor o registo de uma ausência/licença",
    "propor a criação ou alteração de um departamento",
)

_CAPABILITIES_CRM = (
    "listar contactos/leads do CRM",
    "consultar a ficha de um contacto/lead",
    "propor a criação de um contacto/lead",
    "propor alterações a um contacto/lead",
    "propor a mudança de estágio no pipeline de um contacto/lead",
)

_CAPABILITIES_AUTOMATION = (
    "listar automações/relatórios agendados do utilizador",
    "propor a criação, edição, pausa, retoma, cancelamento ou apagar "
    "de automações que geram relatórios periódicos de RH ou CRM",
)


def build_persona_scope(user: CurrentUser | None) -> str:
    """
    Texto de system prompt dinâmico: âmbito + inventário de capacidades
    deste utilizador. Extraído para ser testável sem RunContext.
    """
    if user is None:
        return (
            "Neste turno não há utilizador autenticado. Não tens capacidades "
            "de RH, CRM nem automações — só podes conversar. Se pedirem uma "
            "ação de dados, pede que iniciem sessão na plataforma."
        )

    tem_rh = user.has_module(MODULE_RH)
    tem_crm = user.has_module(MODULE_CRM)

    if user.is_admin or (tem_rh and tem_crm):
        ambito = "RH e CRM"
        apresentacao = (
            "Podes apresentar-te como o assistente de gestão da empresa "
            "(RH e CRM)."
        )
    elif tem_rh:
        ambito = "RH"
        apresentacao = 'Apresenta-te como o assistente de RH (ex: "sou o assistente de RH").'
    elif tem_crm:
        ambito = "CRM"
        apresentacao = 'Apresenta-te como o assistente de CRM (ex: "sou o assistente de CRM").'
    else:
        return (
            "Neste turno não tens capacidades de RH, CRM nem automações — "
            "só podes conversar. Se pedirem listagens ou alterações desses "
            "domínios, explica com naturalidade que isso não é contigo e "
            "sugere que peçam a alguém com o acesso certo — sem mencionar "
            "ferramentas, módulos técnicos ou o motivo interno."
        )

    capabilities: list[str] = []
    if tem_rh or user.is_admin:
        capabilities.extend(_CAPABILITIES_RH)
    if tem_crm or user.is_admin:
        capabilities.extend(_CAPABILITIES_CRM)
    if tem_rh or tem_crm or user.is_admin:
        capabilities.extend(_CAPABILITIES_AUTOMATION)

    lista = "\n".join(f"  - {item}" for item in capabilities)
    return (
        f"{apresentacao}\n"
        f"Âmbito deste turno: {ambito}.\n"
        f"Capacidades disponíveis NESTE turno (podes e deves usá-las quando "
        f"o pedido as cobrir — nunca digas que não podes fazer o que está "
        f"nesta lista):\n{lista}\n"
        f"Pedidos fora desta lista (ex: facturação, finanças, projetos, se "
        f"não aparecerem acima): explica com naturalidade que isso não é "
        f"contigo e sugere quem tenha o acesso certo — sem jargão técnico.\n"
        f"Lembra-te: propor uma alteração para o utilizador confirmar É "
        f"cumprir o pedido; não é recusar."
    )


agent = Agent(
    model=_build_model(),
    deps_type=AgentDeps,
    system_prompt=SYSTEM_PROMPT,
)


@agent.system_prompt
def _persona_scope(ctx: RunContext[AgentDeps]) -> str:
    """
    Instrução dinâmica (avaliada por pedido): âmbito + inventário de
    capacidades consoante os módulos do utilizador — complementa o filtro
    de tools em `_require_module` e reduz recusas falsas do modelo.
    """
    return build_persona_scope(ctx.deps.user)


def _require_module(module: str) -> Callable:
    """Prepare-hook: só expõe a ferramenta ao modelo se o utilizador tiver o módulo."""
    async def prepare(ctx: RunContext[AgentDeps], tool_def: ToolDefinition) -> ToolDefinition | None:
        if ctx.deps.user is not None and ctx.deps.user.has_module(module):
            return tool_def
        return None
    return prepare


def _require_any_module() -> Callable:
    """Prepare-hook: só expõe a ferramenta se o utilizador tiver pelo menos um módulo (RH ou CRM)."""
    async def prepare(ctx: RunContext[AgentDeps], tool_def: ToolDefinition) -> ToolDefinition | None:
        user = ctx.deps.user
        if user is not None and (user.is_admin or user.has_module(MODULE_RH) or user.has_module(MODULE_CRM)):
            return tool_def
        return None
    return prepare


def _describe_api_error(exc: Exception) -> str:
    if isinstance(exc, PermissionDenied):
        return f"Sem permissão para executar esta ação: {exc}"
    if isinstance(exc, ApiError):
        return f"Erro ao contactar o backend: {exc}"
    raise exc


# --------------------------------------------------------------------------
# RH ("people") — leitura, só visível com o módulo atribuído
# --------------------------------------------------------------------------

@agent.tool(prepare=_require_module(MODULE_RH))
def rh_list_departments(ctx: RunContext[AgentDeps]) -> list[dict] | str:
    """Lista os departamentos da empresa."""
    try:
        return rh_client.read_list_departments(ctx.deps.access_token)
    except (PermissionDenied, ApiError) as exc:
        return _describe_api_error(exc)


@agent.tool(prepare=_require_module(MODULE_RH))
def rh_list_employees(ctx: RunContext[AgentDeps], department_id: int | None = None) -> list[dict] | str:
    """Lista colaboradores, opcionalmente filtrando por ID de departamento."""
    try:
        return rh_client.read_list_employees(ctx.deps.access_token, department_id)
    except (PermissionDenied, ApiError) as exc:
        return _describe_api_error(exc)


@agent.tool(prepare=_require_module(MODULE_RH))
def rh_get_employee(ctx: RunContext[AgentDeps], employee_id: int) -> dict | str:
    """Devolve os dados completos de um colaborador pelo ID."""
    try:
        return rh_client.read_get_employee(ctx.deps.access_token, employee_id)
    except (PermissionDenied, ApiError) as exc:
        return _describe_api_error(exc)


@agent.tool(prepare=_require_module(MODULE_RH))
def rh_list_leaves(ctx: RunContext[AgentDeps], employee_id: int) -> list[dict] | str:
    """Lista as ausências/licenças registadas de um colaborador."""
    try:
        return rh_client.read_list_leaves(ctx.deps.access_token, employee_id)
    except (PermissionDenied, ApiError) as exc:
        return _describe_api_error(exc)


# --------------------------------------------------------------------------
# RH ("people") — escrita, apenas propõe
# --------------------------------------------------------------------------

@agent.tool(prepare=_require_module(MODULE_RH))
def propose_update_employee(ctx: RunContext[AgentDeps], employee_id: int, **fields) -> str:
    """
    Regista uma PROPOSTA de alteração de propriedades de um colaborador.
    Passa em `fields` só as propriedades a mudar, usando os nomes exatos:
    full_name, email, phone, department_id, role, reports_to, birth_date,
    contract_type, gender, nationality, marital_status, mobile, address,
    province, city, bi_number, nif, niss, bank_name, bank_account, iban.
    Nunca altera diretamente.
    """
    description = f"Atualizar colaborador #{employee_id}: {fields}"
    action = ctx.deps.gate.propose(
        description=description,
        fn=rh_client.write_update_employee,
        kwargs={"access_token": ctx.deps.access_token, "employee_id": employee_id, **fields},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


@agent.tool(prepare=_require_module(MODULE_RH))
def propose_update_employee_status(ctx: RunContext[AgentDeps], employee_id: int, status: str) -> str:
    """Regista uma PROPOSTA de alteração de estado de um colaborador (ex: 'online', 'ausente', 'inativo')."""
    description = f"Alterar estado do colaborador #{employee_id} para '{status}'"
    action = ctx.deps.gate.propose(
        description=description,
        fn=rh_client.write_update_employee_status,
        kwargs={"access_token": ctx.deps.access_token, "employee_id": employee_id, "status": status},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


@agent.tool(prepare=_require_module(MODULE_RH))
def propose_create_leave(
    ctx: RunContext[AgentDeps], employee_id: int, start_date: str, end_date: str, reason: str | None = None
) -> str:
    """Regista uma PROPOSTA de ausência/licença para um colaborador (datas em YYYY-MM-DD)."""
    description = f"Registar ausência do colaborador #{employee_id}: {start_date} a {end_date} ({reason or 'sem motivo indicado'})"
    action = ctx.deps.gate.propose(
        description=description,
        fn=rh_client.write_create_leave,
        kwargs={
            "access_token": ctx.deps.access_token,
            "employee_id": employee_id,
            "start_date": start_date,
            "end_date": end_date,
            "reason": reason,
        },
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


@agent.tool(prepare=_require_module(MODULE_RH))
def propose_create_department(ctx: RunContext[AgentDeps], name: str, color: str = "#6B7280") -> str:
    """Regista uma PROPOSTA de criação de um novo departamento."""
    description = f"Criar departamento '{name}'"
    action = ctx.deps.gate.propose(
        description=description,
        fn=rh_client.write_create_department,
        kwargs={"access_token": ctx.deps.access_token, "name": name, "color": color},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


@agent.tool(prepare=_require_module(MODULE_RH))
def propose_update_department(ctx: RunContext[AgentDeps], department_id: int, name: str, color: str = "#6B7280") -> str:
    """Regista uma PROPOSTA de alteração de um departamento existente."""
    description = f"Atualizar departamento #{department_id} para nome='{name}', cor='{color}'"
    action = ctx.deps.gate.propose(
        description=description,
        fn=rh_client.write_update_department,
        kwargs={"access_token": ctx.deps.access_token, "department_id": department_id, "name": name, "color": color},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


# --------------------------------------------------------------------------
# CRM — leitura, só visível com o módulo atribuído
# --------------------------------------------------------------------------

@agent.tool(prepare=_require_module(MODULE_CRM))
def crm_list_contacts(ctx: RunContext[AgentDeps]) -> list[dict] | str:
    """Lista os contactos/leads do CRM."""
    try:
        return crm_client.read_list_contacts(ctx.deps.access_token)
    except (PermissionDenied, ApiError) as exc:
        return _describe_api_error(exc)


@agent.tool(prepare=_require_module(MODULE_CRM))
def crm_get_contact(ctx: RunContext[AgentDeps], contact_id: int) -> dict | str:
    """Devolve os dados completos de um contacto/lead pelo ID."""
    try:
        return crm_client.read_get_contact(ctx.deps.access_token, contact_id)
    except (PermissionDenied, ApiError) as exc:
        return _describe_api_error(exc)


# --------------------------------------------------------------------------
# CRM — escrita, apenas propõe
# --------------------------------------------------------------------------

@agent.tool(prepare=_require_module(MODULE_CRM))
def propose_create_contact(ctx: RunContext[AgentDeps], name: str, **fields) -> str:
    """
    Regista uma PROPOSTA de criação de um novo contacto/lead no CRM.
    Passa em `fields` as propriedades adicionais, usando os nomes exatos:
    email, phone, company, notes, stage, pipeline_value, channel, owner,
    service_type, lead_date.
    """
    description = f"Criar contacto CRM '{name}': {fields}"
    action = ctx.deps.gate.propose(
        description=description,
        fn=crm_client.write_create_contact,
        kwargs={"access_token": ctx.deps.access_token, "name": name, **fields},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


@agent.tool(prepare=_require_module(MODULE_CRM))
def propose_update_contact(ctx: RunContext[AgentDeps], contact_id: int, **fields) -> str:
    """
    Regista uma PROPOSTA de alteração de propriedades de um contacto/lead.
    Passa em `fields` só as propriedades a mudar, usando os nomes exatos:
    name, email, phone, company, notes, stage, pipeline_value, channel,
    owner, service_type, lead_date. Nunca altera diretamente.
    """
    description = f"Atualizar contacto CRM #{contact_id}: {fields}"
    action = ctx.deps.gate.propose(
        description=description,
        fn=crm_client.write_update_contact,
        kwargs={"access_token": ctx.deps.access_token, "contact_id": contact_id, **fields},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


@agent.tool(prepare=_require_module(MODULE_CRM))
def propose_update_contact_stage(ctx: RunContext[AgentDeps], contact_id: int, stage: str) -> str:
    """Regista uma PROPOSTA de alteração do estágio do pipeline de um contacto/lead."""
    description = f"Mover contacto CRM #{contact_id} para o estágio '{stage}'"
    action = ctx.deps.gate.propose(
        description=description,
        fn=crm_client.write_update_contact_stage,
        kwargs={"access_token": ctx.deps.access_token, "contact_id": contact_id, "stage": stage},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


# --------------------------------------------------------------------------
# Automações (tarefas agendadas) — cruzam RH e CRM, só visíveis com pelo
# menos um módulo atribuído. Só geram relatórios (leitura); nunca alteram
# dados. Ver app/tasks_store.py e app/task_runner.py.
# --------------------------------------------------------------------------

def _task_summary(task: tasks_store.Task) -> dict:
    resumo = {
        "id": task.id,
        "modulo": task.module,
        "titulo": task.title,
        "frequencia": task.frequency,
        "hora": task.time_of_day,
        "estado": task.status,
        "proxima_execucao": task.next_run_at,
        "ultima_execucao": task.last_run_at,
    }
    if task.weekday is not None:
        resumo["dia_da_semana"] = tasks_store.WEEKDAYS[task.weekday]
    if task.run_date:
        resumo["data"] = task.run_date
    if task.last_output:
        resumo["ultimo_relatorio"] = task.last_output
    return resumo


def _own_task_or_none(ctx: RunContext[AgentDeps], task_id: int) -> tasks_store.Task | None:
    """Só devolve a tarefa se pertencer ao utilizador atual (ou for admin) e o módulo dela ainda lhe for acessível."""
    user = ctx.deps.user
    task = tasks_store.get_task(task_id)
    if task is None or user is None:
        return None
    if str(task.owner_id) != str(user.id) and not user.is_admin:
        return None
    if not user.has_module(task.module):
        return None
    return task


@agent.tool(prepare=_require_any_module())
def automation_list_tasks(ctx: RunContext[AgentDeps], module: str | None = None) -> list[dict]:
    """
    Lista as automações (tarefas agendadas) do utilizador atual, ex:
    relatórios periódicos de RH ou CRM. Passa `module` ("people" para RH ou
    "crm") para filtrar só as desse módulo; omite para ver todas.
    """
    user = ctx.deps.user
    tasks = tasks_store.list_tasks(owner_id=str(user.id), module=module)
    return [_task_summary(task) for task in tasks]


@agent.tool(prepare=_require_any_module())
def propose_create_automation(
    ctx: RunContext[AgentDeps],
    module: str,
    title: str,
    frequency: str,
    time_of_day: str,
    weekday: int | None = None,
    run_date: str | None = None,
) -> str:
    """
    Regista uma PROPOSTA de nova automação: gera periodicamente um relatório
    resumido do módulo indicado (só lê e resume dados, nunca os altera).
    - module: "people" (RH) ou "crm".
    - title: nome descritivo, ex: "Relatório diário de RH".
    - frequency: "diaria", "semanal" ou "unica".
    - time_of_day: hora no formato "HH:MM" (24h).
    - weekday: obrigatório se frequency="semanal" (0=segunda .. 6=domingo).
    - run_date: obrigatório se frequency="unica" (formato "YYYY-MM-DD").
    """
    user = ctx.deps.user
    if not user.has_module(module):
        return "Não consigo criar automações para essa área — não tens acesso a ela."

    try:
        tasks_store.compute_next_run(frequency, time_of_day, weekday, run_date)
    except ValueError as exc:
        return f"Não foi possível agendar: {exc}"

    description = f"Criar automação '{title}' ({module}, {frequency} às {time_of_day})"
    action = ctx.deps.gate.propose(
        description=description,
        fn=tasks_store.create_task,
        kwargs={
            "owner_id": str(user.id), "owner_nome": user.nome, "module": module, "title": title,
            "frequency": frequency, "time_of_day": time_of_day, "weekday": weekday, "run_date": run_date,
        },
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


@agent.tool(prepare=_require_any_module())
def propose_update_automation(
    ctx: RunContext[AgentDeps],
    task_id: int,
    title: str | None = None,
    frequency: str | None = None,
    time_of_day: str | None = None,
    weekday: int | None = None,
    run_date: str | None = None,
) -> str:
    """Regista uma PROPOSTA de alteração de uma automação existente (só os campos passados mudam)."""
    task = _own_task_or_none(ctx, task_id)
    if task is None:
        return f"Não encontrei nenhuma automação #{task_id} que possas gerir."

    try:
        tasks_store.compute_next_run(
            frequency or task.frequency,
            time_of_day or task.time_of_day,
            weekday if weekday is not None else task.weekday,
            run_date or task.run_date,
        )
    except ValueError as exc:
        return f"Não foi possível agendar: {exc}"

    fields = {"title": title, "frequency": frequency, "time_of_day": time_of_day, "weekday": weekday, "run_date": run_date}
    alteracoes = {key: value for key, value in fields.items() if value is not None}
    description = f"Atualizar automação #{task_id} ('{task.title}'): {alteracoes}"
    action = ctx.deps.gate.propose(
        description=description,
        fn=tasks_store.update_task,
        kwargs={"task_id": task_id, **fields},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


@agent.tool(prepare=_require_any_module())
def propose_pause_automation(ctx: RunContext[AgentDeps], task_id: int) -> str:
    """Regista uma PROPOSTA para pausar uma automação (deixa de correr até ser retomada)."""
    task = _own_task_or_none(ctx, task_id)
    if task is None:
        return f"Não encontrei nenhuma automação #{task_id} que possas gerir."
    description = f"Pausar automação #{task_id} ('{task.title}')"
    action = ctx.deps.gate.propose(
        description=description,
        fn=tasks_store.set_status,
        kwargs={"task_id": task_id, "status": tasks_store.STATUS_PAUSED},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


@agent.tool(prepare=_require_any_module())
def propose_resume_automation(ctx: RunContext[AgentDeps], task_id: int) -> str:
    """Regista uma PROPOSTA para retomar uma automação pausada."""
    task = _own_task_or_none(ctx, task_id)
    if task is None:
        return f"Não encontrei nenhuma automação #{task_id} que possas gerir."
    description = f"Retomar automação #{task_id} ('{task.title}')"
    action = ctx.deps.gate.propose(
        description=description,
        fn=tasks_store.set_status,
        kwargs={"task_id": task_id, "status": tasks_store.STATUS_ACTIVE},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


@agent.tool(prepare=_require_any_module())
def propose_cancel_automation(ctx: RunContext[AgentDeps], task_id: int) -> str:
    """Regista uma PROPOSTA para cancelar definitivamente uma automação (mantém o histórico, mas deixa de correr)."""
    task = _own_task_or_none(ctx, task_id)
    if task is None:
        return f"Não encontrei nenhuma automação #{task_id} que possas gerir."
    description = f"Cancelar automação #{task_id} ('{task.title}')"
    action = ctx.deps.gate.propose(
        description=description,
        fn=tasks_store.set_status,
        kwargs={"task_id": task_id, "status": tasks_store.STATUS_CANCELLED},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"


@agent.tool(prepare=_require_any_module())
def propose_delete_automation(ctx: RunContext[AgentDeps], task_id: int) -> str:
    """Regista uma PROPOSTA para apagar definitivamente uma automação e o seu histórico."""
    task = _own_task_or_none(ctx, task_id)
    if task is None:
        return f"Não encontrei nenhuma automação #{task_id} que possas gerir."
    description = f"Apagar automação #{task_id} ('{task.title}')"
    action = ctx.deps.gate.propose(
        description=description,
        fn=tasks_store.delete_task,
        kwargs={"task_id": task_id},
    )
    ctx.deps.pending_actions.append(action)
    return f"Proposta registada, pendente de confirmação: {description}"
