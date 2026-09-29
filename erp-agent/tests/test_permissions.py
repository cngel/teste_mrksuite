"""
Testes da camada de permissões e dos clientes RH/CRM — correm sem rede real,
usando mocks de requests.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from app import crm_client, rh_client
from app.agent import AgentDeps, agent, build_persona_scope
from app.api_client import ApiError, PermissionDenied
from app.permissions import MODULE_CRM, MODULE_RH, CurrentUser


@dataclass
class _FakeCtx:
    """Stand-in mínimo para RunContext: os prepare-hooks só leem ctx.deps."""
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


def test_has_module_admin_bypassa_tudo():
    user = CurrentUser(id="1", nome="Ana", email="ana@x.com", is_admin=True, modules=[])
    assert user.has_module(MODULE_RH)
    assert user.has_module(MODULE_CRM)


def test_has_module_respeita_lista_atribuida():
    user = CurrentUser(id="1", nome="Ana", email="ana@x.com", is_admin=False, modules=[MODULE_RH])
    assert user.has_module(MODULE_RH)
    assert not user.has_module(MODULE_CRM)


def _mock_response(status_code: int, payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = b"{}" if payload is not None else b""
    resp.json.return_value = payload or {}
    return resp


def test_rh_client_encaminha_para_o_proxy_web_service_com_o_token():
    with patch("app.api_client.requests.request") as mock_request:
        mock_request.return_value = _mock_response(200, [{"id": 1, "name": "TI"}])
        result = rh_client.read_list_departments("tok123")

    assert result == [{"id": 1, "name": "TI"}]
    args, kwargs = mock_request.call_args
    assert args[0] == "GET"
    assert args[1] == "http://localhost:5002/api/rh/departments"
    assert kwargs["headers"]["Authorization"] == "Bearer tok123"


def test_write_levanta_permission_denied_em_403():
    with patch("app.api_client.requests.request") as mock_request:
        mock_request.return_value = _mock_response(403, {"detail": "Sem permissão para este módulo"})
        with pytest.raises(PermissionDenied):
            rh_client.write_update_employee_status("tok123", employee_id=1, status="ausente")


def test_write_levanta_api_error_noutros_erros():
    with patch("app.api_client.requests.request") as mock_request:
        mock_request.return_value = _mock_response(404, {"detail": "Funcionário não encontrado"})
        with pytest.raises(ApiError):
            rh_client.write_update_employee_status("tok123", employee_id=999, status="ausente")


def test_crm_client_atualiza_contacto_fazendo_merge_dos_dados_atuais():
    with patch("app.api_client.requests.request") as mock_request:
        mock_request.side_effect = [
            _mock_response(200, {"id": 5, "name": "Empresa X", "stage": "novo"}),
            _mock_response(200, {"id": 5, "name": "Empresa X", "stage": "negociação"}),
        ]
        result = crm_client.write_update_contact("tok123", contact_id=5, stage="negociação")

    assert result["stage"] == "negociação"
    put_call = mock_request.call_args_list[1]
    assert put_call.args[0] == "PUT"
    assert put_call.kwargs["json"] == {"id": 5, "name": "Empresa X", "stage": "negociação"}


@pytest.mark.anyio
async def test_sem_utilizador_nao_ve_ferramentas_de_rh_nem_crm():
    visiveis = await _visible_tool_names(user=None)
    assert visiveis == set()


@pytest.mark.anyio
async def test_utilizador_so_ve_ferramentas_do_modulo_atribuido():
    user = CurrentUser(id="1", nome="Rita", email="rita@x.com", is_admin=False, modules=[MODULE_RH])
    visiveis = await _visible_tool_names(user)
    assert "rh_list_employees" in visiveis
    assert "propose_update_employee" in visiveis
    assert "crm_list_contacts" not in visiveis
    assert "propose_update_contact" not in visiveis


@pytest.mark.anyio
async def test_admin_ve_ferramentas_de_todos_os_modulos():
    user = CurrentUser(id="1", nome="Ana", email="ana@x.com", is_admin=True, modules=[])
    visiveis = await _visible_tool_names(user)
    assert "rh_list_employees" in visiveis
    assert "crm_list_contacts" in visiveis


def test_persona_com_rh_lista_capacidades_e_nao_incentiva_recusa():
    user = CurrentUser(id="1", nome="Rita", email="rita@x.com", is_admin=False, modules=[MODULE_RH])
    texto = build_persona_scope(user)
    assert "RH" in texto
    assert "listar departamentos e colaboradores" in texto
    assert "nunca digas que não podes" in texto
    assert "propor" in texto.lower()
    # utilizador só de RH não deve ver capacidades de CRM no inventário
    assert "contactos/leads do CRM" not in texto


def test_persona_admin_inclui_rh_crm_e_automacoes():
    user = CurrentUser(id="1", nome="Ana", email="ana@x.com", is_admin=True, modules=[])
    texto = build_persona_scope(user)
    assert "listar departamentos e colaboradores" in texto
    assert "contactos/leads do CRM" in texto
    assert "automações" in texto.lower()


def test_persona_sem_modulos_so_conversa():
    user = CurrentUser(id="1", nome="X", email="x@x.com", is_admin=False, modules=["kora"])
    texto = build_persona_scope(user)
    assert "só podes conversar" in texto
    assert "listar departamentos" not in texto
