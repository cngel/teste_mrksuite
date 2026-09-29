"""
Camada de permissões do agente.

Os módulos atribuíveis a um utilizador vêm do claim "modules" do JWT (ver
auth-service/app.py: MODULE_REGISTRY e user_module_permissions), o mesmo
usado pela sidebar do dashboard e pelo proxy do web-service. O agente
espelha aqui a mesma regra — admin da empresa tem acesso total,
caso contrário só aos módulos explicitamente atribuídos.

Isto é apenas a primeira barreira (evita que o agente sequer proponha uma
ação fora do alcance do utilizador). A barreira real, que não pode ser
contornada por um raciocínio errado do modelo, é o proxy do web-service:
qualquer chamada a /api/rh/* ou /api/crm/* é validada de novo lá, contra o
JWT, antes de chegar ao serviço de dados.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MODULE_RH = "people"
MODULE_CRM = "crm"


@dataclass
class CurrentUser:
    """Identidade resolvida a partir de /api/auth/me para a sessão do agente."""
    id: str
    nome: str
    email: str
    is_admin: bool = False
    modules: list[str] = field(default_factory=list)

    def has_module(self, module: str) -> bool:
        return self.is_admin or module in self.modules
