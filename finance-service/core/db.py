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
                CREATE TABLE IF NOT EXISTS fin_counters (
                    doc_type VARCHAR(20) PRIMARY KEY,
                    next_seq INTEGER NOT NULL DEFAULT 1
                );
            """)

            # Facturas
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fin_invoices (
                    id SERIAL PRIMARY KEY,
                    doc_number VARCHAR(30) NOT NULL UNIQUE,
                    client_name VARCHAR(255) NOT NULL,
                    client_nif VARCHAR(50),
                    client_email VARCHAR(255),
                    client_contact_id INTEGER,
                    description TEXT,
                    subtotal NUMERIC(14,2) NOT NULL DEFAULT 0,
                    iva_rate NUMERIC(5,2) NOT NULL DEFAULT 14,
                    iva_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
                    total NUMERIC(14,2) NOT NULL DEFAULT 0,
                    paid_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
                    status VARCHAR(20) NOT NULL DEFAULT 'emitida',
                    issue_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    due_date DATE,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Recibos (comprovativos de pagamento contra uma factura)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fin_receipts (
                    id SERIAL PRIMARY KEY,
                    doc_number VARCHAR(30) NOT NULL UNIQUE,
                    invoice_id INTEGER NOT NULL REFERENCES fin_invoices(id) ON DELETE CASCADE,
                    amount NUMERIC(14,2) NOT NULL,
                    payment_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    payment_method VARCHAR(30) DEFAULT 'transferencia',
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Fornecedores (registo de contas a pagar)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fin_suppliers (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    nif VARCHAR(50),
                    email VARCHAR(255),
                    phone VARCHAR(50),
                    category VARCHAR(100),
                    address TEXT,
                    notes TEXT,
                    active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Despesas (contas a pagar + aprovação hierárquica)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fin_expenses (
                    id SERIAL PRIMARY KEY,
                    description VARCHAR(255) NOT NULL,
                    supplier_name VARCHAR(255),
                    category VARCHAR(100),
                    department_id INTEGER,
                    requested_by INTEGER,
                    approver_id INTEGER,
                    amount NUMERIC(14,2) NOT NULL,
                    due_date DATE,
                    status VARCHAR(20) NOT NULL DEFAULT 'pendente',
                    approval_note TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    decided_at TIMESTAMP,
                    paid_at TIMESTAMP
                );
            """)

            # Migração: despesas passam a poder ligar-se a um fornecedor registado
            # (supplier_name mantém-se como cópia do nome para despesas antigas / fornecedores avulsos)
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'fin_expenses' AND column_name = 'supplier_id'
            """)
            if not cur.fetchone():
                cur.execute("ALTER TABLE fin_expenses ADD COLUMN supplier_id INTEGER REFERENCES fin_suppliers(id)")

            # Documentos anexados a facturas/despesas (contratos, comprovativos, digitalizações)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fin_documents (
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
                CREATE INDEX IF NOT EXISTS idx_fin_documents_entity
                ON fin_documents (entity_type, entity_id);
            """)

            # Isolamento entre empresas (multi-tenant): sem estas colunas, qualquer
            # utilizador autenticado via JWT válido conseguia ver/editar faturas,
            # recibos, despesas e fornecedores de QUALQUER empresa — a app.py
            # passou a filtrar tudo por company_id.
            for table in ("fin_invoices", "fin_receipts", "fin_suppliers", "fin_expenses", "fin_documents"):
                cur.execute("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = %s AND column_name = 'company_id'
                """, (table,))
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN company_id INTEGER REFERENCES companies(id)")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_company ON {table}(company_id)")

            conn.commit()
