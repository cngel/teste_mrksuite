# """
# CLI de demonstração.

# Pede as credenciais reais do utilizador (as mesmas do dashboard web),
# autentica-se contra o auth-service e resolve os módulos atribuídos
# (RH="people", CRM="crm"). Só depois entra numa conversa no terminal com o
# agente — as ferramentas de RH/CRM só aparecem para o modelo se o
# utilizador tiver o módulo correspondente (ver app/agent.py).

# Sempre que o agente propuser uma ação de escrita (compra, bloqueio,
# alteração de colaborador, alteração de contacto CRM, etc.), o CLI pede
# confirmação explícita ao utilizador antes de a executar de verdade contra
# o backend.

# Uso:
#     python -m app.main
# """
# from __future__ import annotations

# import asyncio
# import getpass
# from datetime import date

# from app import auth_client, task_runner
# from app.agent import agent, AgentDeps
# from app.api_client import ApiError, PermissionDenied
# from app.auth_client import AuthError
# from app.context_builder import build_context_preamble


# async def handle_turn(user_message: str, deps: AgentDeps) -> None:
#     preamble = build_context_preamble(user_message, today=date.today())
#     full_prompt = f"{preamble}\n\nMensagem do utilizador: {user_message}" if preamble else user_message

#     result = await agent.run(full_prompt, deps=deps)
#     print(f"\nAgente: {result.output}\n")

#     # Processa quaisquer ações pendentes de confirmação geradas nesta interação
#     while deps.pending_actions:
#         action = deps.pending_actions.pop(0)
#         resposta = input(f"[CONFIRMAÇÃO NECESSÁRIA] {action.description}\nConfirmar? (s/n): ").strip().lower()
#         if resposta == "s":
#             try:
#                 resultado = deps.gate.confirm(action)
#                 print(f"✔ Ação executada: {resultado}\n")
#             except PermissionDenied as exc:
#                 print(f"✘ Sem permissão para executar esta ação: {exc}\n")
#             except ApiError as exc:
#                 print(f"✘ Erro ao executar a ação no backend: {exc}\n")
#         else:
#             deps.gate.reject(action)
#             print("✘ Ação rejeitada.\n")


# def login() -> AgentDeps:
#     print("=== Login ===")
#     while True:
#         email = input("Email: ").strip()
#         senha = getpass.getpass("Senha: ")
#         try:
#             tokens = auth_client.login(email, senha)
#             user = auth_client.me(tokens["access_token"])
#             break
#         except AuthError as exc:
#             print(f"[erro de autenticação] {exc}\n")

#     modulos = "admin (acesso total)" if user.is_admin else (", ".join(user.modules) or "nenhum")
#     print(f"\nBem-vindo, {user.nome}. Módulos atribuídos: {modulos}\n")
#     return AgentDeps(access_token=tokens["access_token"], user=user)


# async def main() -> None:
#     print("=== Agente ERP Kora ===")
#     deps = login()
#     print("Escreve 'sair' para terminar, 'log' para ver o histórico de ações.\n")

#     while True:
#         try:
#             task_runner.run_due_tasks(deps.access_token)
#         except Exception:  # noqa: BLE001
#             pass  # a próxima verificação tenta de novo; não interrompe a conversa

#         user_message = input("Tu: ").strip()
#         if not user_message:
#             continue
#         if user_message.lower() in {"sair", "exit", "quit"}:
#             break
#         if user_message.lower() == "log":
#             for entry in deps.gate.history():
#                 print(entry)
#             continue

#         try:
#             await handle_turn(user_message, deps)
#         except Exception as exc:  # noqa: BLE001
#             print(f"[erro ao contactar o modelo] {exc}")
#             print("Confirma que o Ollama está a correr (`ollama serve`) e que o modelo foi feito pull.\n")


# if __name__ == "__main__":
#     asyncio.run(main())
