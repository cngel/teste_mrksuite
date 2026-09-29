# Agente ERP — Protótipo (Gemma 4 E4B + PydanticAI + Ollama)

Protótipo funcional da arquitetura discutida: um modelo pequeno (Gemma 4 E4B)
a atuar como **planeador/comunicador**, com todo o trabalho pesado (datas,
consultas ao ERP, regras de negócio, confirmação de ações) feito por código
determinístico à volta dele.

```
Utilizador
    │
    ▼
Resolvedor determinístico (datas, departamentos)  ──► app/resolvers.py
    │
    ▼
Context builder (injeta factos já calculados)     ──► app/context_builder.py
    │
    ▼
Agente PydanticAI + Gemma 4 E2B (via Ollama)       ──► app/agent.py
    │
    ├─── ferramentas de LEITURA → executam direto  ──► app/rh_client.py, app/crm_client.py
    │
    └─── ferramentas de ESCRITA → só propõem       ──► app/gate.py
              │
              ▼
       Confirmação humana (CLI)                    ──► app/main.py
              │
              ▼
       Execução real no ERP
```

## 1. Instalar

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Preparar o modelo (Ollama)

```bash
# instalar o Ollama: https://ollama.com/download
ollama pull gemma4:e4b     # ajusta o tag exato conforme o que estiver disponível
ollama serve                # normalmente já fica a correr em background após instalar
```

Se o teu tag de modelo no `ollama list` for diferente de `gemma4:e4b`,
ajusta a variável `MODEL_NAME` (ver `.env.example`) ou edita
`app/config.py` diretamente.

### Usar um provedor cloud em vez do Ollama

O agente também suporta Groq, Gemini (Google) e Anthropic (Claude) como
alternativa ao Ollama local. Basta definir no `.env`:

```bash
MODEL_PROVIDER=groq        # ou: gemini | anthropic | ollama (por omissão)

# e a respetiva API key:
GROQ_API_KEY=...
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
```

Cada provedor tem um `*_MODEL_NAME` próprio com um valor por omissão
razoável (ver `.env.example`) — ajusta se quiseres outro modelo.

## 3. Correr os testes determinísticos (não precisam do Ollama)

```bash
python -m pytest tests/ -v
```

Estes testes validam a parte que **não pode falhar em silêncio**: resolução
de datas, resolução de departamentos, aplicação de regras de negócio, e o
comportamento do gate de confirmação. Já apanharam 2 bugs reais durante o
desenvolvimento deste protótipo — ver comentários em `app/resolvers.py`.

## 4. Correr o agente

Por CLI:

```bash
python -m app.main
```

Ou pela API web (é o que o dashboard usa, através do proxy `/api/kora/...`
do web-service — ver secção 5):

```bash
python -m app.web
# API em http://127.0.0.1:8010
```

Sem ecrã de login próprio: cada pedido chega com o mesmo Bearer token da
sessão já autenticada no dashboard (reencaminhado pelo proxy do
web-service), e a sessão da Kora para esse token é criada automaticamente
no primeiro pedido — vários utilizadores podem estar ligados em
simultâneo, cada um com a sua própria conversa, propostas pendentes e
automações, sem interferirem uns com os outros (ver `_sessions` em
`app/web.py`). O estado é só em memória e expira ao fim de
`SESSION_IDLE_TTL` sem pedidos (12h por omissão), por isso também se
perde num restart do container.

Experimenta perguntas sobre RH e CRM (precisas de um utilizador com o
módulo "people" e/ou "crm" atribuído — ver a camada de permissões em
`app/permissions.py`):

```
Lista os departamentos da empresa.
Quem são os colaboradores do departamento de TI?
Regista uma ausência do colaborador #3 entre 2026-07-20 e 2026-07-22.
Muda o estágio do contacto #5 no CRM para "negociação".
Cria uma automação que me gera todos os dias às 09:00 um resumo do RH.
Pausa a automação Resumo diário CRM.
```

### Automações (tarefas agendadas)

O Kora também sabe criar, editar, pausar, retomar, cancelar e apagar
automações — tarefas que geram periodicamente um relatório resumido de RH
ou CRM (ex: "envia-me todos os dias um resumo do RH"). Tal como as
restantes ações de escrita, tudo passa pelo gate de confirmação.

Por segurança, uma automação **só gera relatórios de leitura** — nunca
altera dados sozinha; qualquer alteração continua a exigir uma proposta
confirmada no momento em que é pedida.

As tarefas ficam guardadas localmente em SQLite (`data/tasks.db`, ver
`app/tasks_store.py` e `TASKS_DB_PATH` em `.env.example`). Um agendador em
segundo plano (`app/task_runner.py`) verifica a cada `TASKS_POLL_INTERVAL`
segundos (30s por omissão) se há tarefas vencidas e executa-as. Na API web
isto corre num thread próprio (ver `app/web.py`) e percorre todas as
sessões ativas — cada utilizador só executa as suas próprias tarefas, com
o seu próprio token; na CLI é verificado no início de cada turno.

Além das ferramentas do agente (via chat), a UI web (`app/web.py`) também
expõe uma API REST direta para gerir automações sem passar pelo LLM — útil
para um painel/dashboard próprio. Exige o mesmo Bearer token da sessão
autenticada e respeita as mesmas regras de posse e de módulo:

```
GET    /api/tasks                 lista as automações do utilizador atual (?module=people|crm)
POST   /api/tasks                 cria uma automação
GET    /api/tasks/{id}            devolve uma automação
PUT    /api/tasks/{id}            atualiza campos (title/frequency/time_of_day/weekday/run_date)
POST   /api/tasks/{id}/pause      pausa
POST   /api/tasks/{id}/resume     retoma
POST   /api/tasks/{id}/cancel     cancela (mantém histórico)
DELETE /api/tasks/{id}            apaga definitivamente
```

Diferente das ferramentas `propose_*_automation` do agente, estas rotas
executam de imediato — não há um LLM a poder alucinar o pedido, por isso
não passam pelo gate de confirmação.

Quando o agente propuser uma ação de escrita (alterar colaborador,
registar ausência, atualizar contacto, etc.), o CLI pede confirmação
`(s/n)` antes de a executar de verdade contra o backend. Escreve `log`
a qualquer momento para ver o histórico de ações propostas/confirmadas.

## 5. Correr junto com o resto da stack (docker-compose)

O `erp-agent` já tem `Dockerfile` próprio e está registado no
`docker-compose.yml` da raiz do projeto como o serviço `erp-agent`
(container `ms_kora`, porta `8010`). O dashboard (`web-service`) fala com
ele através do mesmo proxy `/api/{serviço}/...` usado pelos restantes
módulos — ver `SERVICE_MAP["kora"]` em `web-service/app.py` — em vez de um
URL fixo, por isso funciona a partir de qualquer máquina que aceda ao
dashboard, não só localhost. O acesso exige que o utilizador tenha o
módulo `kora` atribuído (ver `MODULE_REGISTRY` em `auth-service/app.py`).

```bash
docker compose up -d erp-agent
```

## Estrutura do projeto

```
app/
  config.py             configuração (provedor de LLM, endpoints, API keys)
  resolvers.py           resolução determinística de datas e departamentos
  context_builder.py      monta o preâmbulo de contexto injetado no prompt
  permissions.py           camada de permissões por módulo (RH="people", CRM="crm")
  auth_client.py            login e /me contra o auth-service (via proxy do web-service)
  api_client.py              helper HTTP partilhado por rh_client.py e crm_client.py
  rh_client.py                 chamadas ao módulo RH real (rh-service)
  crm_client.py                 chamadas ao módulo CRM real (crm-service)
  tasks_store.py                 persistência (SQLite) das automações agendadas
  task_runner.py                  geração de relatórios e execução das tarefas vencidas
  gate.py                          gate de confirmação para ações de escrita
  agent.py                          definição do agente PydanticAI e das suas ferramentas
  main.py                            CLI interativo (login + conversa)
  web.py                              API web (FastAPI), multi-utilizador, consumida pelo dashboard
tests/
  test_deterministic.py   testes que não dependem do LLM
  test_permissions.py      testes da camada de permissões e dos clientes RH/CRM
  test_model_provider.py    testes da seleção de provedor de LLM
  test_tasks.py               testes das automações (persistência e agendamento)
```

## Próximos passos sugeridos

1. **Medir latência real** do modelo escolhido com `thinking` ligado vs
   desligado, e decidir se compensa ativá-lo só para pedidos que envolvem
   múltiplas regras em cadeia.
2. **Adicionar mais expressões de data** a `resolvers.py` conforme os
   pedidos reais dos utilizadores (ex: "há 3 dias", "no trimestre passado").
3. **Persistir as sessões** (hoje em memória, em `_sessions`) num backend
   partilhado (ex: Redis) se o `erp-agent` vier a correr em mais do que
   uma réplica, para não perder sessões ativas num restart/deploy.
4. **Alargar as ferramentas de RH/CRM** conforme os endpoints reais destes
   serviços forem crescendo (contratos, formações, avaliações, etc.).
