# """
# Cliente para o auth-service, sempre através do proxy do web-service
# (/api/auth/...), para se comportar exatamente como o browser.
# """
# from __future__ import annotations

# import requests

# from app import config
# from app.permissions import CurrentUser


# class AuthError(Exception):
#     """Erro de autenticação ou autorização devolvido pelo backend."""


# def login(email: str, senha: str) -> dict:
#     """Autentica no auth-service e devolve {access_token, refresh_token, ...}."""
#     resp = requests.post(
#         f"{config.WEB_SERVICE_URL}/api/auth/login",
#         json={"email": email, "senha": senha},
#         timeout=config.HTTP_TIMEOUT,
#     )
#     if resp.status_code != 200:
#         raise AuthError(_extract_detail(resp))
#     return resp.json()


# def me(access_token: str) -> CurrentUser:
#     """Resolve a identidade e os módulos atribuídos ao utilizador autenticado."""
#     resp = requests.get(
#         f"{config.WEB_SERVICE_URL}/api/auth/me",
#         headers={"Authorization": f"Bearer {access_token}"},
#         timeout=config.HTTP_TIMEOUT,
#     )
#     if resp.status_code != 200:
#         raise AuthError(_extract_detail(resp))
#     data = resp.json()
#     return CurrentUser(
#         id=data["id"],
#         nome=data["nome"],
#         email=data["email"],
#         is_admin=bool(data.get("is_admin")),
#         modules=list(data.get("modules") or []),
#     )


# def _extract_detail(resp: requests.Response) -> str:
#     try:
#         return resp.json().get("detail", resp.text)
#     except ValueError:
#         return resp.text
