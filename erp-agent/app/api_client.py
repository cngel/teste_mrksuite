# """
# HTTP helper partilhado por rh_client.py e crm_client.py.

# Todas as chamadas passam pelo proxy do web-service (/api/{serviço}/...),
# nunca diretamente pelos microserviços — é aí que o módulo do utilizador
# (claims do JWT) é validado antes do pedido seguir para o rh-service ou
# crm-service (ver web-service/app.py).
# """
# from __future__ import annotations

# import requests

# from app import config


# class ApiError(Exception):
#     """Erro genérico devolvido pelo backend (4xx/5xx que não seja 403)."""


# class PermissionDenied(Exception):
#     """O utilizador está autenticado mas não tem o módulo necessário (403)."""


# def request(method: str, service: str, path: str, access_token: str, **kwargs) -> dict | list | None:
#     url = f"{config.WEB_SERVICE_URL}/api/{service}/{path.lstrip('/')}"
#     headers = kwargs.pop("headers", {})
#     headers["Authorization"] = f"Bearer {access_token}"

#     try:
#         resp = requests.request(method, url, headers=headers, timeout=config.HTTP_TIMEOUT, **kwargs)
#     except requests.RequestException as exc:
#         raise ApiError(f"Falha de rede a contactar '{service}': {exc}") from exc

#     if resp.status_code == 403:
#         raise PermissionDenied(_detail(resp))
#     if resp.status_code >= 400:
#         raise ApiError(_detail(resp))
#     if resp.status_code == 204 or not resp.content:
#         return None
#     return resp.json()


# def _detail(resp: requests.Response) -> str:
#     try:
#         return resp.json().get("detail", resp.text)
#     except ValueError:
#         return resp.text
