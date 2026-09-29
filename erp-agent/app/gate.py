"""
Gate de confirmação.

Qualquer ação que escreva dados no ERP (alterar um colaborador, registar
uma ausência, atualizar um contacto do CRM, etc.) passa obrigatoriamente
por aqui antes de ser executada. O agente propõe a ação; o utilizador
humano confirma ou rejeita.

Isto compensa qualquer falha residual de raciocínio do modelo — mesmo que a
LLM "alucine" um ID ou valor errado, nada é escrito no sistema real sem
confirmação explícita.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PendingAction:
    description: str
    fn: Callable[..., Any]
    kwargs: dict = field(default_factory=dict)

    def execute(self) -> Any:
        return self.fn(**self.kwargs)


class ConfirmationGate:
    """
    Uso típico:

        gate = ConfirmationGate()
        pending = gate.propose(
            description="Alterar estado do colaborador #12 para 'inativo'",
            fn=rh_client.write_update_employee_status,
            kwargs={"access_token": token, "employee_id": 12, "status": "inativo"},
        )
        # mostrar pending.description ao utilizador...
        if utilizador_confirmou:
            resultado = gate.confirm(pending)
        else:
            gate.reject(pending)
    """

    def __init__(self) -> None:
        self._history: list[dict] = []

    def propose(self, description: str, fn: Callable[..., Any], kwargs: dict) -> PendingAction:
        action = PendingAction(description=description, fn=fn, kwargs=kwargs)
        self._history.append({"description": description, "status": "pendente"})
        return action

    def confirm(self, action: PendingAction) -> Any:
        result = action.execute()
        self._history.append({"description": action.description, "status": "confirmada", "resultado": result})
        return result

    def reject(self, action: PendingAction) -> None:
        self._history.append({"description": action.description, "status": "rejeitada"})

    def history(self) -> list[dict]:
        return list(self._history)
