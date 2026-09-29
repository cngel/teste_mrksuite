"""
Configuração central do agente.

Todos os valores podem ser sobrepostos por variáveis de ambiente (ver .env.example).
O .env na raiz do erp-agent é carregado automaticamente (nunca commitado —
ver .gitignore da raiz do projeto).
"""
import os

from dotenv import load_dotenv

load_dotenv()

# Qual backend de LLM usar: "ollama" (local, por omissão), "groq", "gemini" ou "anthropic".
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "ollama").lower()

# Ollama (local, via API compatível com OpenAI)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "gemma4:e4b")
FAKE_API_KEY = "local"

# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL_NAME = os.getenv("GROQ_MODEL_NAME", "llama-3.3-70b-versatile")

# Gemini (Google)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")

# Anthropic (Claude)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL_NAME = os.getenv("ANTHROPIC_MODEL_NAME", "claude-opus-4-8")

LANGUAGE = os.getenv("AGENT_LANGUAGE", "pt")

# Web-service expõe o proxy /api/{serviço}/... que já aplica a camada de
# permissões por módulo (ver web-service/app.py). O agente fala sempre com
# o RH e o CRM através deste proxy, nunca diretamente com rh-service ou
# crm-service, para herdar exatamente a mesma restrição usada pelo browser.
WEB_SERVICE_URL = os.getenv("WEB_SERVICE_URL", "http://localhost:5002")

HTTP_TIMEOUT = float(os.getenv("AGENT_HTTP_TIMEOUT", "15"))

# Ficheiro SQLite local onde ficam guardadas as tarefas/automações criadas
# pelo Kora (ver app/tasks_store.py). Caminho relativo ao diretório onde o
# processo corre (erp-agent/); a pasta "data/" já está no .gitignore.
TASKS_DB_PATH = os.getenv("TASKS_DB_PATH", "data/tasks.db")

# Intervalo (segundos) entre verificações de tarefas agendadas vencidas,
# usado pelo agendador em segundo plano da UI web (ver app/web.py).
TASKS_POLL_INTERVAL = float(os.getenv("TASKS_POLL_INTERVAL", "30"))
