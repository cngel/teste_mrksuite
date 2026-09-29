import os
import psycopg2
from psycopg2.extras import RealDictCursor


def get_connection():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def init_db():
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # Contador de numeração sequencial imutável por tipo de documento
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cont_counters (
                    doc_type VARCHAR(20) PRIMARY KEY,
                    next_seq INTEGER NOT NULL DEFAULT 1
                );
            """)

            # Plano de Contas
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cont_accounts (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(20) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    account_class VARCHAR(20) NOT NULL,
                    account_type VARCHAR(30) NOT NULL DEFAULT 'analitica',
                    parent_id INTEGER REFERENCES cont_accounts(id),
                    is_system BOOLEAN NOT NULL DEFAULT false,
                    active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Lançamentos contabilísticos (cabeçalho)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cont_entries (
                    id SERIAL PRIMARY KEY,
                    doc_number VARCHAR(30) NOT NULL UNIQUE,
                    entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    description TEXT NOT NULL,
                    source VARCHAR(30) NOT NULL DEFAULT 'manual',
                    source_type VARCHAR(30),
                    source_id INTEGER,
                    status VARCHAR(20) NOT NULL DEFAULT 'lancado',
                    reversed_entry_id INTEGER REFERENCES cont_entries(id),
                    created_by VARCHAR(255),
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_cont_entries_source
                ON cont_entries (source_type, source_id);
            """)

            # Linhas de débito/crédito de cada lançamento
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cont_entry_lines (
                    id SERIAL PRIMARY KEY,
                    entry_id INTEGER NOT NULL REFERENCES cont_entries(id) ON DELETE CASCADE,
                    account_id INTEGER NOT NULL REFERENCES cont_accounts(id),
                    debit NUMERIC(14,2) NOT NULL DEFAULT 0,
                    credit NUMERIC(14,2) NOT NULL DEFAULT 0,
                    memo VARCHAR(255)
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_cont_entry_lines_entry
                ON cont_entry_lines (entry_id);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_cont_entry_lines_account
                ON cont_entry_lines (account_id);
            """)

            # Documentos anexados a lançamentos (comprovativos, contratos, digitalizações)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cont_documents (
                    id SERIAL PRIMARY KEY,
                    entity_type VARCHAR(20) NOT NULL,
                    entity_id INTEGER NOT NULL,
                    document_type VARCHAR(50) NOT NULL,
                    filename VARCHAR(255) NOT NULL,
                    object_name VARCHAR(255) NOT NULL,
                    content_type VARCHAR(100),
                    uploaded_by VARCHAR(255),
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_cont_documents_entity
                ON cont_documents (entity_type, entity_id);
            """)

            # Isolamento entre empresas (multi-tenant): mesma abordagem dos restantes
            # microserviços — sem estas colunas, qualquer utilizador autenticado via
            # JWT válido conseguia ver/editar contas e lançamentos de QUALQUER empresa.
            for table in ("cont_accounts", "cont_entries", "cont_entry_lines", "cont_documents"):
                cur.execute("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = %s AND column_name = 'company_id'
                """, (table,))
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN company_id INTEGER REFERENCES companies(id)")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_company ON {table}(company_id)")

            # code é único por empresa (contas de empresas diferentes podem repetir código)
            cur.execute("""
                SELECT 1 FROM pg_indexes WHERE indexname = 'cont_accounts_code_company_key'
            """)
            if not cur.fetchone():
                cur.execute("""
                    CREATE UNIQUE INDEX cont_accounts_code_company_key
                    ON cont_accounts (code, company_id)
                """)

            conn.commit()


# -------------------------
# NUMERAÇÃO SEQUENCIAL IMUTÁVEL
# -------------------------

DOC_PREFIX = {"lancamento": "LC"}


def next_doc_number(cur, doc_type: str) -> str:
    cur.execute(
        "INSERT INTO cont_counters (doc_type, next_seq) VALUES (%s, 1) ON CONFLICT DO NOTHING",
        (doc_type,),
    )
    cur.execute(
        "SELECT next_seq FROM cont_counters WHERE doc_type=%s FOR UPDATE",
        (doc_type,),
    )
    seq = cur.fetchone()["next_seq"]
    cur.execute(
        "UPDATE cont_counters SET next_seq = next_seq + 1 WHERE doc_type=%s",
        (doc_type,),
    )
    return f"{DOC_PREFIX[doc_type]}-{seq:06d}"


# -------------------------
# PLANO DE CONTAS POR OMISSÃO
# -------------------------

# (code, name, account_class, account_type, parent_code)
DEFAULT_CHART = [
    ("1",     "ATIVO",                          "ativo",      "sintetica", None),
    ("1.1",   "Ativo Circulante",                "ativo",      "sintetica", "1"),
    ("1.1.1", "Caixa",                           "ativo",      "analitica", "1.1"),
    ("1.1.2", "Bancos",                          "ativo",      "analitica", "1.1"),
    ("1.1.3", "Clientes / Contas a Receber",     "ativo",      "analitica", "1.1"),
    ("2",     "PASSIVO",                         "passivo",    "sintetica", None),
    ("2.1",   "Passivo Circulante",              "passivo",    "sintetica", "2"),
    ("2.1.1", "Fornecedores / Contas a Pagar",   "passivo",    "analitica", "2.1"),
    ("2.1.2", "IVA a Pagar",                     "passivo",    "analitica", "2.1"),
    ("3",     "PATRIMÓNIO LÍQUIDO",              "patrimonio", "sintetica", None),
    ("3.1",   "Capital Social",                  "patrimonio", "analitica", "3"),
    ("3.2",   "Resultados Acumulados",           "patrimonio", "analitica", "3"),
    ("4",     "RECEITAS",                        "receita",    "sintetica", None),
    ("4.1",   "Receita de Vendas/Serviços",      "receita",    "analitica", "4"),
    ("5",     "DESPESAS",                        "despesa",    "sintetica", None),
    ("5.1",   "Despesas Operacionais",           "despesa",    "analitica", "5"),
]


def seed_default_accounts(cur, company_id: int) -> None:
    """Cria (de forma preguiçosa, na primeira vez que a empresa acede a /accounts) o
    plano de contas por omissão para essa empresa — cada empresa tem o seu próprio,
    isolado dos restantes. Idempotente: nunca duplica contas já existentes."""
    code_to_id: dict[str, int] = {}
    for code, name, account_class, account_type, parent_code in DEFAULT_CHART:
        cur.execute(
            "SELECT id FROM cont_accounts WHERE code=%s AND company_id=%s",
            (code, company_id),
        )
        row = cur.fetchone()
        if row:
            code_to_id[code] = row["id"]
            continue
        parent_id = code_to_id.get(parent_code) if parent_code else None
        cur.execute(
            """INSERT INTO cont_accounts
                   (code, name, account_class, account_type, parent_id, is_system, company_id)
               VALUES (%s, %s, %s, %s, %s, true, %s)
               RETURNING id""",
            (code, name, account_class, account_type, parent_id, company_id),
        )
        code_to_id[code] = cur.fetchone()["id"]
