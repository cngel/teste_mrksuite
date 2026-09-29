"""
Testes da camada determinística — correm sem qualquer LLM ou rede.

Uso:
    python -m pytest tests/ -v
"""
from datetime import date

from app.resolvers import resolve_date_expression, resolve_department
from app.gate import ConfirmationGate


REFERENCE_DAY = date(2026, 7, 7)  # terça-feira


def test_semana_passada():
    # REFERENCE_DAY = terça-feira 07/07/2026 -> semana passada (seg-dom) = 29/06 a 05/07
    r = resolve_date_expression("Quem faltou semana passada?", today=REFERENCE_DAY)
    assert r is not None
    assert r.as_iso() == ("2026-06-29", "2026-07-05")


def test_ontem():
    r = resolve_date_expression("O que aconteceu ontem?", today=REFERENCE_DAY)
    assert r.as_iso() == ("2026-07-06", "2026-07-06")


def test_sem_expressao_conhecida():
    r = resolve_date_expression("Quero marcar uma reunião", today=REFERENCE_DAY)
    assert r is None


def test_resolve_department_sinonimo():
    assert resolve_department("equipa financeira") == "financeiro"
    assert resolve_department("o pessoal de TI") == "ti"
    assert resolve_department("marketing") is None


def test_gate_so_executa_apos_confirmacao():
    gate = ConfirmationGate()

    def fn_de_teste(x: int) -> dict:
        return {"status": "criada", "x": x}

    action = gate.propose(description="Ação de teste", fn=fn_de_teste, kwargs={"x": 5})
    # nada foi executado ainda
    historico_antes = gate.history()
    assert historico_antes[-1]["status"] == "pendente"

    resultado = gate.confirm(action)
    assert resultado["status"] == "criada"
    assert resultado["x"] == 5

    historico_depois = gate.history()
    assert historico_depois[-1]["status"] == "confirmada"


def test_gate_rejeitar_nao_executa():
    gate = ConfirmationGate()
    chamada = {"executada": False}

    def fn_que_nao_deve_correr():
        chamada["executada"] = True
        return "não devia ter corrido"

    action = gate.propose(description="Ação de teste", fn=fn_que_nao_deve_correr, kwargs={})
    gate.reject(action)

    assert chamada["executada"] is False
