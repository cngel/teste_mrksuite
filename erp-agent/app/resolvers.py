"""
Resolvedor determinístico de datas e entidades.

Ponto central da arquitetura discutida: a LLM (especialmente um modelo pequeno
como o Gemma 4 E2B) não é fiável a calcular datas relativas ("semana passada",
"ontem", "mês passado"). Este módulo resolve essas expressões em código Python
puro, sem qualquer chamada ao modelo, e devolve datas ISO exatas.

O agente recebe sempre o resultado já calculado — nunca lhe é pedido que
"pense" sobre que dia é hoje menos sete dias.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class DateRange:
    start: date
    end: date
    label: str  # a expressão original detetada, para debug/log

    def as_iso(self) -> tuple[str, str]:
        return self.start.isoformat(), self.end.isoformat()


def _semana_passada(today: date) -> DateRange:
    dias_desde_segunda = today.weekday()  # 0 = segunda
    inicio_desta_semana = today - timedelta(days=dias_desde_segunda)
    inicio_semana_passada = inicio_desta_semana - timedelta(days=7)
    fim_semana_passada = inicio_semana_passada + timedelta(days=6)
    return DateRange(inicio_semana_passada, fim_semana_passada, "semana passada")


def _esta_semana(today: date) -> DateRange:
    dias_desde_segunda = today.weekday()
    inicio = today - timedelta(days=dias_desde_segunda)
    fim = inicio + timedelta(days=6)
    return DateRange(inicio, fim, "esta semana")


def _ontem(today: date) -> DateRange:
    d = today - timedelta(days=1)
    return DateRange(d, d, "ontem")


def _hoje(today: date) -> DateRange:
    return DateRange(today, today, "hoje")


def _mes_passado(today: date) -> DateRange:
    primeiro_dia_mes_atual = today.replace(day=1)
    ultimo_dia_mes_passado = primeiro_dia_mes_atual - timedelta(days=1)
    primeiro_dia_mes_passado = ultimo_dia_mes_passado.replace(day=1)
    return DateRange(primeiro_dia_mes_passado, ultimo_dia_mes_passado, "mês passado")


def _ultimos_n_dias(n: int):
    def _fn(today: date) -> DateRange:
        return DateRange(today - timedelta(days=n), today, f"últimos {n} dias")
    return _fn


_PATTERNS: list[tuple[re.Pattern, callable]] = [
    (re.compile(r"\bsemana passada\b", re.IGNORECASE), _semana_passada),
    (re.compile(r"\besta semana\b", re.IGNORECASE), _esta_semana),
    (re.compile(r"\bontem\b", re.IGNORECASE), _ontem),
    (re.compile(r"\bhoje\b", re.IGNORECASE), _hoje),
    (re.compile(r"\bm[êe]s passado\b", re.IGNORECASE), _mes_passado),
]


def resolve_date_expression(text: str, today: date | None = None) -> DateRange | None:
    """
    Procura uma expressão de data relativa conhecida no texto e devolve o
    DateRange correspondente. Devolve None se nenhuma expressão for
    reconhecida — nesse caso, quem chama deve pedir esclarecimento ao
    utilizador em vez de adivinhar.
    """
    today = today or date.today()

    m = re.search(r"\b[uú]ltimos? (\d+) dias\b", text, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        return _ultimos_n_dias(n)(today)

    for pattern, fn in _PATTERNS:
        if pattern.search(text):
            return fn(today)

    return None


# Departamentos/equipas conhecidos e os seus sinónimos — resolução determinística
# de entidades para evitar que a LLM "invente" o nome exato do departamento no ERP.
_DEPARTMENTS = {
    "financeiro": ["financeira", "financeiro", "finanças", "equipa financeira"],
    "ti": ["ti", "tecnologia", "informática", "equipa de ti", "equipa ti"],
    "rh": ["rh", "recursos humanos", "pessoal"],
    "comercial": ["comercial", "vendas", "equipa comercial", "equipa de vendas"],
}


def resolve_department(text: str) -> str | None:
    """
    Resolve o nome canónico do departamento a partir de sinónimos comuns.

    Usa fronteiras de palavra (\\b) para evitar falsos positivos como
    "marketing" a ser confundido com "ti" (a substring "ti" aparece dentro
    de "marke-ti-ng"). Bug real apanhado pelos testes automáticos.
    """
    text_lower = text.lower()
    for canonical, synonyms in _DEPARTMENTS.items():
        for syn in synonyms:
            if re.search(rf"\b{re.escape(syn)}\b", text_lower):
                return canonical
    return None


if __name__ == "__main__":
    hoje = date(2026, 7, 7)  # terça-feira
    for frase in ["semana passada", "ontem", "esta semana", "mês passado", "últimos 10 dias"]:
        r = resolve_date_expression(frase, today=hoje)
        print(f"{frase!r:25} -> {r.as_iso()}")
