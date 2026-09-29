"""
Context builder.

Antes de qualquer mensagem chegar à LLM, este módulo tenta resolver
expressões de data conhecidas no texto do utilizador. Se encontrar uma,
injeta o resultado já calculado no prompt — a LLM nunca precisa (nem deve)
calcular isto sozinha.

Isto implementa diretamente a lição aprendida nos testes: o E2B "acertou por
sorte" o dia da semana numa das experiências. Não vamos depender de sorte
em produção.
"""
from __future__ import annotations

from datetime import date

from app.resolvers import resolve_date_expression, resolve_department


def build_context_preamble(user_message: str, today: date | None = None) -> str:
    """
    Devolve um bloco de texto para anexar ao prompt do utilizador, contendo
    factos já resolvidos deterministicamente (data de hoje, range de datas
    detetado, departamento detetado). Vazio se nada for detetado.
    """
    today = today or date.today()
    lines = [f"[Data de hoje: {today.isoformat()}]"]

    date_range = resolve_date_expression(user_message, today=today)
    if date_range:
        start, end = date_range.as_iso()
        lines.append(
            f'[Expressão de data detetada: "{date_range.label}" '
            f"-> já resolvida para start_date={start}, end_date={end}. "
            f"Usa estes valores diretamente, não os recalcules.]"
        )

    dept = resolve_department(user_message)
    if dept:
        lines.append(f'[Departamento detetado: "{dept}" -> usa este valor exato nos parâmetros de ferramentas.]')

    return "\n".join(lines)
