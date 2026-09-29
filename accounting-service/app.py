import os
import uuid
from datetime import date

import psycopg2
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordBearer

if __package__:
    from .core.db import get_connection, init_db, next_doc_number, seed_default_accounts
    from .core.storage import upload_bytes, ensure_bucket, delete_object
    from .core.url_service import get_presigned_url
else:
    from core.db import get_connection, init_db, next_doc_number, seed_default_accounts
    from core.storage import upload_bytes, ensure_bucket, delete_object
    from core.url_service import get_presigned_url

app = FastAPI(title="Accounting Service (Contabilidade)", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS",
        "http://localhost,http://localhost:80,http://127.0.0.1,http://localhost:5002",
    ).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="http://ms_auth:5000/login"
)


# -------------------------
# STARTUP
# -------------------------

@app.on_event("startup")
def startup():
    init_db()
    ensure_bucket()


# -------------------------
# AUTH
# -------------------------

def get_current_claims(token: str = Depends(oauth2_scheme)) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Token inválido")

    jti = payload.get("jti")
    if jti:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT jti FROM jwt_blocklist WHERE jti = %s AND expires_at > NOW()",
                    (jti,),
                )
                if cur.fetchone():
                    raise HTTPException(status_code=401, detail="Token revogado")

    return payload


def get_current_user(claims: dict = Depends(get_current_claims)) -> str:
    return claims["sub"]


def get_current_company(claims: dict = Depends(get_current_claims)) -> int:
    """Isolamento entre empresas: toda a leitura/escrita de contas e lançamentos tem
    de ser filtrada por company_id — nunca basta um JWT válido, seja lá de que
    empresa for."""
    company_id = claims.get("company_id")
    if company_id is None:
        raise HTTPException(status_code=403, detail="Utilizador sem empresa associada")
    return company_id


# -------------------------
# MODELS
# -------------------------

VALID_ACCOUNT_CLASSES = {"ativo", "passivo", "patrimonio", "receita", "despesa"}


class AccountCreate(BaseModel):
    code: str
    name: str
    account_class: str
    account_type: str = "analitica"
    parent_id: int | None = None


class AccountUpdate(BaseModel):
    name: str
    active: bool = True


class EntryLineIn(BaseModel):
    account_id: int | None = None
    account_code: str | None = None
    debit: float = 0
    credit: float = 0
    memo: str | None = None


class EntryCreate(BaseModel):
    entry_date: str | None = None
    description: str
    lines: list[EntryLineIn]
    source: str = "manual"
    source_type: str | None = None
    source_id: int | None = None


# -------------------------
# HELPERS
# -------------------------

def resolve_account(cur, company_id: int, account_id: int | None, account_code: str | None):
    if account_id is not None:
        cur.execute("SELECT * FROM cont_accounts WHERE id=%s AND company_id=%s", (account_id, company_id))
    elif account_code is not None:
        cur.execute("SELECT * FROM cont_accounts WHERE code=%s AND company_id=%s", (account_code, company_id))
    else:
        raise HTTPException(status_code=422, detail="Cada linha precisa de account_id ou account_code")
    account = cur.fetchone()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    if not account["active"]:
        raise HTTPException(status_code=409, detail=f"Conta '{account['name']}' está inativa")
    if account["account_type"] != "analitica":
        raise HTTPException(status_code=409, detail=f"Conta '{account['name']}' é sintética — não pode receber lançamentos")
    return account


def get_entry_detail(cur, entry_id: int, company_id: int):
    cur.execute("SELECT * FROM cont_entries WHERE id=%s AND company_id=%s", (entry_id, company_id))
    entry = cur.fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="Lançamento não encontrado")
    cur.execute("""
        SELECT l.id, l.account_id, l.debit, l.credit, l.memo, a.code AS account_code, a.name AS account_name
        FROM cont_entry_lines l JOIN cont_accounts a ON a.id = l.account_id
        WHERE l.entry_id = %s ORDER BY l.id
    """, (entry_id,))
    entry["lines"] = cur.fetchall()
    return entry


# -------------------------
# PLANO DE CONTAS
# -------------------------

@app.get("/accounts")
def list_accounts(
    active: bool | None = None,
    account_class: str | None = None,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            seed_default_accounts(cur, company_id)
            conn.commit()
            clauses, params = ["company_id=%s"], [company_id]
            if active is not None:
                clauses.append("active=%s")
                params.append(active)
            if account_class:
                clauses.append("account_class=%s")
                params.append(account_class)
            where = " AND ".join(clauses)
            cur.execute(f"SELECT * FROM cont_accounts WHERE {where} ORDER BY code", params)
            return cur.fetchall()


@app.post("/accounts", status_code=201)
def create_account(body: AccountCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    if body.account_class not in VALID_ACCOUNT_CLASSES:
        raise HTTPException(status_code=422, detail="account_class inválido")
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            seed_default_accounts(cur, company_id)
            if body.parent_id is not None:
                cur.execute("SELECT 1 FROM cont_accounts WHERE id=%s AND company_id=%s", (body.parent_id, company_id))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Conta-mãe não encontrada")
            try:
                cur.execute("""
                    INSERT INTO cont_accounts (code, name, account_class, account_type, parent_id, is_system, company_id)
                    VALUES (%s, %s, %s, %s, %s, false, %s)
                    RETURNING *
                """, (body.code, body.name, body.account_class, body.account_type, body.parent_id, company_id))
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Já existe uma conta com este código")
            conn.commit()
            return cur.fetchone()


@app.get("/accounts/{account_id}")
def get_account(account_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM cont_accounts WHERE id=%s AND company_id=%s", (account_id, company_id))
            account = cur.fetchone()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    return account


@app.put("/accounts/{account_id}")
def update_account(account_id: int, body: AccountUpdate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "UPDATE cont_accounts SET name=%s, active=%s WHERE id=%s AND company_id=%s RETURNING *",
                (body.name, body.active, account_id, company_id),
            )
            account = cur.fetchone()
            conn.commit()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    return account


@app.delete("/accounts/{account_id}", status_code=204)
def delete_account(account_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM cont_accounts WHERE id=%s AND company_id=%s", (account_id, company_id))
            account = cur.fetchone()
            if not account:
                raise HTTPException(status_code=404, detail="Conta não encontrada")
            if account["is_system"]:
                raise HTTPException(status_code=409, detail="Não é possível apagar uma conta do sistema")
            cur.execute("SELECT 1 FROM cont_entry_lines WHERE account_id=%s LIMIT 1", (account_id,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Não é possível apagar: há lançamentos associados a esta conta")
            cur.execute("SELECT 1 FROM cont_accounts WHERE parent_id=%s LIMIT 1", (account_id,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="A conta tem subcontas")
            cur.execute("DELETE FROM cont_accounts WHERE id=%s AND company_id=%s", (account_id, company_id))
            conn.commit()


# -------------------------
# LANÇAMENTOS (PARTIDAS DOBRADAS)
# -------------------------

@app.get("/entries")
def list_entries(
    date_from: str | None = None,
    date_to: str | None = None,
    source: str | None = None,
    account_id: int | None = None,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            seed_default_accounts(cur, company_id)
            conn.commit()
            clauses, params = ["e.company_id=%s"], [company_id]
            if date_from:
                clauses.append("e.entry_date >= %s")
                params.append(date_from)
            if date_to:
                clauses.append("e.entry_date <= %s")
                params.append(date_to)
            if source:
                clauses.append("e.source = %s")
                params.append(source)
            if account_id is not None:
                clauses.append("EXISTS (SELECT 1 FROM cont_entry_lines l WHERE l.entry_id = e.id AND l.account_id = %s)")
                params.append(account_id)
            where = " AND ".join(clauses)
            cur.execute(
                f"SELECT e.* FROM cont_entries e WHERE {where} ORDER BY e.entry_date DESC, e.id DESC",
                params,
            )
            entries = cur.fetchall()
            for entry in entries:
                cur.execute("""
                    SELECT l.id, l.account_id, l.debit, l.credit, l.memo, a.code AS account_code, a.name AS account_name
                    FROM cont_entry_lines l JOIN cont_accounts a ON a.id = l.account_id
                    WHERE l.entry_id = %s ORDER BY l.id
                """, (entry["id"],))
                entry["lines"] = cur.fetchall()
            return entries


@app.post("/entries", status_code=201)
def create_entry(body: EntryCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    if len(body.lines) < 2:
        raise HTTPException(status_code=422, detail="Um lançamento precisa de pelo menos 2 linhas")

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            seed_default_accounts(cur, company_id)

            total_debit = 0.0
            total_credit = 0.0
            resolved_lines = []
            for line in body.lines:
                debit = round(line.debit or 0, 2)
                credit = round(line.credit or 0, 2)
                if debit < 0 or credit < 0:
                    raise HTTPException(status_code=422, detail="Débito/crédito não podem ser negativos")
                if (debit > 0) == (credit > 0):
                    raise HTTPException(status_code=422, detail="Cada linha deve ter exatamente um valor: débito OU crédito")
                account = resolve_account(cur, company_id, line.account_id, line.account_code)
                resolved_lines.append((account["id"], debit, credit, line.memo))
                total_debit += debit
                total_credit += credit

            if round(total_debit, 2) != round(total_credit, 2):
                raise HTTPException(
                    status_code=422,
                    detail=f"Lançamento desequilibrado: débito {total_debit:.2f} != crédito {total_credit:.2f}",
                )

            doc_number = next_doc_number(cur, "lancamento")
            cur.execute("""
                INSERT INTO cont_entries
                    (doc_number, entry_date, description, source, source_type, source_id, created_by, company_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                doc_number, body.entry_date or date.today().isoformat(), body.description,
                body.source, body.source_type, body.source_id, user, company_id,
            ))
            entry = cur.fetchone()

            for account_id, debit, credit, memo in resolved_lines:
                cur.execute("""
                    INSERT INTO cont_entry_lines (entry_id, account_id, debit, credit, memo, company_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (entry["id"], account_id, debit, credit, memo, company_id))

            conn.commit()
            return get_entry_detail(cur, entry["id"], company_id)


@app.get("/entries/{entry_id}")
def get_entry(entry_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            return get_entry_detail(cur, entry_id, company_id)


@app.post("/entries/{entry_id}/reverse", status_code=201)
def reverse_entry(entry_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM cont_entries WHERE id=%s AND company_id=%s", (entry_id, company_id))
            original = cur.fetchone()
            if not original:
                raise HTTPException(status_code=404, detail="Lançamento não encontrado")
            if original["status"] == "estornado":
                raise HTTPException(status_code=409, detail="Lançamento já foi estornado")

            cur.execute("SELECT * FROM cont_entry_lines WHERE entry_id=%s ORDER BY id", (entry_id,))
            lines = cur.fetchall()

            doc_number = next_doc_number(cur, "lancamento")
            cur.execute("""
                INSERT INTO cont_entries
                    (doc_number, entry_date, description, source, source_type, source_id, status, reversed_entry_id, created_by, company_id)
                VALUES (%s, %s, %s, 'manual', %s, %s, 'lancado', %s, %s, %s)
                RETURNING *
            """, (
                doc_number, date.today().isoformat(), f"Estorno de {original['doc_number']}",
                original["source_type"], original["source_id"], original["id"], user, company_id,
            ))
            reversal = cur.fetchone()

            for line in lines:
                cur.execute("""
                    INSERT INTO cont_entry_lines (entry_id, account_id, debit, credit, memo, company_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (reversal["id"], line["account_id"], line["credit"], line["debit"], line["memo"], company_id))

            cur.execute(
                "UPDATE cont_entries SET status='estornado' WHERE id=%s AND company_id=%s",
                (entry_id, company_id),
            )
            conn.commit()
            return get_entry_detail(cur, reversal["id"], company_id)


# -------------------------
# LIVRO RAZÃO
# -------------------------

@app.get("/ledger/{account_id}")
def account_ledger(
    account_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM cont_accounts WHERE id=%s AND company_id=%s", (account_id, company_id))
            account = cur.fetchone()
            if not account:
                raise HTTPException(status_code=404, detail="Conta não encontrada")

            opening = 0.0
            if date_from:
                cur.execute("""
                    SELECT COALESCE(SUM(l.debit - l.credit), 0) AS saldo
                    FROM cont_entry_lines l JOIN cont_entries e ON e.id = l.entry_id
                    WHERE l.account_id=%s AND l.company_id=%s AND e.entry_date < %s
                """, (account_id, company_id, date_from))
                opening = float(cur.fetchone()["saldo"])

            clauses, params = ["l.account_id=%s", "l.company_id=%s"], [account_id, company_id]
            if date_from:
                clauses.append("e.entry_date >= %s")
                params.append(date_from)
            if date_to:
                clauses.append("e.entry_date <= %s")
                params.append(date_to)
            where = " AND ".join(clauses)
            cur.execute(f"""
                SELECT e.doc_number, e.entry_date, e.description, l.debit, l.credit, l.memo
                FROM cont_entry_lines l JOIN cont_entries e ON e.id = l.entry_id
                WHERE {where}
                ORDER BY e.entry_date, e.id
            """, params)
            movements = cur.fetchall()

    running = opening
    for m in movements:
        m["debit"] = float(m["debit"])
        m["credit"] = float(m["credit"])
        running += m["debit"] - m["credit"]
        m["running_balance"] = round(running, 2)

    return {
        "account": account,
        "opening_balance": round(opening, 2),
        "movements": movements,
        "closing_balance": round(running, 2),
    }


# -------------------------
# BALANCETE
# -------------------------

@app.get("/balancete")
def balancete(
    date_from: str | None = None,
    date_to: str | None = None,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            seed_default_accounts(cur, company_id)
            conn.commit()
            clauses, params = [], []
            if date_from:
                clauses.append("e.entry_date >= %s")
                params.append(date_from)
            if date_to:
                clauses.append("e.entry_date <= %s")
                params.append(date_to)
            extra_where = (" AND " + " AND ".join(clauses)) if clauses else ""
            cur.execute(f"""
                SELECT a.id, a.code, a.name, a.account_class,
                       COALESCE(SUM(l.debit), 0) AS debit_total,
                       COALESCE(SUM(l.credit), 0) AS credit_total
                FROM cont_accounts a
                JOIN cont_entry_lines l ON l.account_id = a.id
                JOIN cont_entries e ON e.id = l.entry_id
                WHERE a.company_id=%s{extra_where}
                GROUP BY a.id, a.code, a.name, a.account_class
                ORDER BY a.code
            """, [company_id] + params)
            rows = cur.fetchall()

    total_debit = total_credit = 0.0
    for r in rows:
        r["debit_total"] = float(r["debit_total"])
        r["credit_total"] = float(r["credit_total"])
        r["balance"] = round(r["debit_total"] - r["credit_total"], 2)
        total_debit += r["debit_total"]
        total_credit += r["credit_total"]

    return {
        "accounts": rows,
        "total_debit": round(total_debit, 2),
        "total_credit": round(total_credit, 2),
        "balanced": round(total_debit, 2) == round(total_credit, 2),
    }


# -------------------------
# DRE — DEMONSTRAÇÃO DE RESULTADOS
# -------------------------

@app.get("/dre")
def dre(
    date_from: str | None = None,
    date_to: str | None = None,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            seed_default_accounts(cur, company_id)
            conn.commit()
            clauses, params = [], []
            if date_from:
                clauses.append("e.entry_date >= %s")
                params.append(date_from)
            if date_to:
                clauses.append("e.entry_date <= %s")
                params.append(date_to)
            extra_where = (" AND " + " AND ".join(clauses)) if clauses else ""
            cur.execute(f"""
                SELECT a.id, a.code, a.name, a.account_class,
                       CASE WHEN a.account_class = 'receita'
                            THEN COALESCE(SUM(l.credit), 0) - COALESCE(SUM(l.debit), 0)
                            ELSE COALESCE(SUM(l.debit), 0) - COALESCE(SUM(l.credit), 0)
                       END AS total
                FROM cont_accounts a
                JOIN cont_entry_lines l ON l.account_id = a.id
                JOIN cont_entries e ON e.id = l.entry_id
                WHERE a.company_id=%s AND a.account_class IN ('receita', 'despesa'){extra_where}
                GROUP BY a.id, a.code, a.name, a.account_class
                ORDER BY a.code
            """, [company_id] + params)
            rows = cur.fetchall()

    for r in rows:
        r["total"] = float(r["total"])
    receitas = [r for r in rows if r["account_class"] == "receita"]
    despesas = [r for r in rows if r["account_class"] == "despesa"]
    total_receitas = round(sum(r["total"] for r in receitas), 2)
    total_despesas = round(sum(r["total"] for r in despesas), 2)

    return {
        "receitas": receitas,
        "despesas": despesas,
        "total_receitas": total_receitas,
        "total_despesas": total_despesas,
        "resultado_liquido": round(total_receitas - total_despesas, 2),
    }


# -------------------------
# BALANÇO PATRIMONIAL
# -------------------------

@app.get("/balanco")
def balanco(
    as_of: str | None = None,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    as_of_date = as_of or date.today().isoformat()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            seed_default_accounts(cur, company_id)
            conn.commit()
            cur.execute("""
                SELECT a.id, a.code, a.name, a.account_class,
                       CASE WHEN a.account_class = 'ativo'
                            THEN COALESCE(SUM(l.debit), 0) - COALESCE(SUM(l.credit), 0)
                            ELSE COALESCE(SUM(l.credit), 0) - COALESCE(SUM(l.debit), 0)
                       END AS total
                FROM cont_accounts a
                JOIN cont_entry_lines l ON l.account_id = a.id
                JOIN cont_entries e ON e.id = l.entry_id
                WHERE a.company_id=%s AND a.account_class IN ('ativo', 'passivo', 'patrimonio')
                      AND e.entry_date <= %s
                GROUP BY a.id, a.code, a.name, a.account_class
                ORDER BY a.code
            """, (company_id, as_of_date))
            rows = cur.fetchall()

            # O balanço só fecha (Ativo = Passivo + Património) se o resultado do
            # período (receitas - despesas ainda não apuradas para Resultados
            # Acumulados) entrar como componente implícito do Património Líquido —
            # tal como nos relatórios normais, que mostram o resultado do exercício
            # dentro do capital próprio antes do fecho contabilístico formal.
            cur.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN a.account_class='receita' THEN l.credit - l.debit ELSE 0 END), 0) AS receitas,
                    COALESCE(SUM(CASE WHEN a.account_class='despesa' THEN l.debit - l.credit ELSE 0 END), 0) AS despesas
                FROM cont_entry_lines l
                JOIN cont_accounts a ON a.id = l.account_id
                JOIN cont_entries e ON e.id = l.entry_id
                WHERE l.company_id=%s AND e.entry_date <= %s
            """, (company_id, as_of_date))
            resultado_row = cur.fetchone()

    for r in rows:
        r["total"] = float(r["total"])
    ativo = [r for r in rows if r["account_class"] == "ativo"]
    passivo = [r for r in rows if r["account_class"] == "passivo"]
    patrimonio = [r for r in rows if r["account_class"] == "patrimonio"]

    resultado_periodo = round(float(resultado_row["receitas"]) - float(resultado_row["despesas"]), 2)
    if resultado_periodo:
        patrimonio.append({
            "id": None, "code": "3.9", "name": "Resultado do Período (não apurado)",
            "account_class": "patrimonio", "total": resultado_periodo,
        })

    total_ativo = round(sum(r["total"] for r in ativo), 2)
    total_passivo = round(sum(r["total"] for r in passivo), 2)
    total_patrimonio = round(sum(r["total"] for r in patrimonio), 2)

    return {
        "as_of": as_of_date,
        "ativo": ativo,
        "passivo": passivo,
        "patrimonio": patrimonio,
        "total_ativo": total_ativo,
        "total_passivo": total_passivo,
        "total_patrimonio": total_patrimonio,
        "balanceado": total_ativo == round(total_passivo + total_patrimonio, 2),
    }


# -------------------------
# RESUMO (DASHBOARD)
# -------------------------

@app.get("/summary")
def summary(user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    month_start = date.today().replace(day=1).isoformat()

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            seed_default_accounts(cur, company_id)
            conn.commit()

            cur.execute("""
                SELECT COALESCE(SUM(l.debit - l.credit), 0) AS saldo
                FROM cont_entry_lines l JOIN cont_accounts a ON a.id = l.account_id
                WHERE l.company_id=%s AND a.code IN ('1.1.1', '1.1.2')
            """, (company_id,))
            saldo_caixa_bancos = float(cur.fetchone()["saldo"])

            cur.execute("""
                SELECT COALESCE(SUM(l.debit - l.credit), 0) AS saldo
                FROM cont_entry_lines l JOIN cont_accounts a ON a.id = l.account_id
                WHERE l.company_id=%s AND a.code = '1.1.3'
            """, (company_id,))
            total_a_receber = float(cur.fetchone()["saldo"])

            cur.execute("""
                SELECT COALESCE(SUM(l.credit - l.debit), 0) AS saldo
                FROM cont_entry_lines l JOIN cont_accounts a ON a.id = l.account_id
                WHERE l.company_id=%s AND a.code = '2.1.1'
            """, (company_id,))
            total_a_pagar = float(cur.fetchone()["saldo"])

            cur.execute(
                "SELECT COUNT(*) AS n FROM cont_entries WHERE company_id=%s AND entry_date >= %s",
                (company_id, month_start),
            )
            lancamentos_mes = int(cur.fetchone()["n"])

            cur.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN a.account_class='receita' THEN l.credit - l.debit ELSE 0 END), 0) AS receitas,
                    COALESCE(SUM(CASE WHEN a.account_class='despesa' THEN l.debit - l.credit ELSE 0 END), 0) AS despesas
                FROM cont_entry_lines l
                JOIN cont_accounts a ON a.id = l.account_id
                JOIN cont_entries e ON e.id = l.entry_id
                WHERE l.company_id=%s AND e.entry_date >= %s
            """, (company_id, month_start))
            row = cur.fetchone()
            resultado_mes = float(row["receitas"]) - float(row["despesas"])

    return {
        "saldo_caixa_bancos": round(saldo_caixa_bancos, 2),
        "total_a_receber": round(total_a_receber, 2),
        "total_a_pagar": round(total_a_pagar, 2),
        "lancamentos_mes": lancamentos_mes,
        "resultado_mes": round(resultado_mes, 2),
    }


# -------------------------
# DOCUMENTOS (ANEXOS DE LANÇAMENTOS)
# -------------------------

ENTITY_TABLE = {"entry": "cont_entries"}


def upload_file(file: UploadFile, entity_type: str, entity_id: int) -> str:
    ext = file.filename.split(".")[-1]
    object_name = f"{entity_type}/{entity_id}/{uuid.uuid4()}.{ext}"
    data = file.file.read()
    upload_bytes(data, object_name, file.content_type or "application/octet-stream")
    return object_name


def get_file_url(object_name: str) -> str:
    return get_presigned_url(object_name)


def assert_entity_exists(cur, entity_type: str, entity_id: int, company_id: int):
    table = ENTITY_TABLE.get(entity_type)
    if not table:
        raise HTTPException(status_code=400, detail="Tipo de entidade inválido")
    cur.execute(f"SELECT id FROM {table} WHERE id=%s AND company_id=%s", (entity_id, company_id))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Registo não encontrado")


@app.post("/entries/{entry_id}/documents", status_code=201)
def upload_entry_document(
    entry_id: int,
    document_type: str,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            assert_entity_exists(cur, "entry", entry_id, company_id)
            object_name = upload_file(file, "entry", entry_id)
            cur.execute("""
                INSERT INTO cont_documents
                    (entity_type, entity_id, document_type, filename, object_name, content_type, uploaded_by, company_id)
                VALUES ('entry', %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (entry_id, document_type, file.filename, object_name, file.content_type, user, company_id))
            conn.commit()
            return cur.fetchone()


@app.get("/entries/{entry_id}/documents")
def list_entry_documents(entry_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM cont_documents
                WHERE entity_type='entry' AND entity_id=%s AND company_id=%s
                ORDER BY created_at DESC
            """, (entry_id, company_id))
            return cur.fetchall()


@app.get("/documents")
def list_documents(
    document_type: str | None = None,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            clauses, params = ["company_id=%s"], [company_id]
            if document_type:
                clauses.append("document_type=%s")
                params.append(document_type)
            where = " AND ".join(clauses)
            cur.execute(f"SELECT * FROM cont_documents WHERE {where} ORDER BY created_at DESC", params)
            return cur.fetchall()


@app.get("/documents/{document_id}/presigned-url")
def get_document_url(document_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT object_name FROM cont_documents WHERE id=%s AND company_id=%s", (document_id, company_id))
            doc = cur.fetchone()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return {"url": get_file_url(doc["object_name"])}


@app.delete("/documents/{document_id}")
def delete_document(document_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT object_name FROM cont_documents WHERE id=%s AND company_id=%s", (document_id, company_id))
            doc = cur.fetchone()
            if not doc:
                raise HTTPException(status_code=404, detail="Documento não encontrado")

            try:
                delete_object(doc["object_name"])
            except Exception:
                raise HTTPException(500, "Erro ao apagar ficheiro no storage")

            cur.execute("DELETE FROM cont_documents WHERE id=%s AND company_id=%s", (document_id, company_id))
            conn.commit()

    return {"message": "Documento removido com sucesso"}
