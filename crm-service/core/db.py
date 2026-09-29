import os
import psycopg2
from psycopg2.extras import RealDictCursor


def get_connection():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=5432,
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def init_db():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id           SERIAL PRIMARY KEY,
                    name         VARCHAR(255) NOT NULL,
                    email        VARCHAR(255),
                    phone        VARCHAR(50),
                    company      VARCHAR(255),
                    notes        TEXT,
                    stage        VARCHAR(50)  DEFAULT 'novo',
                    pipeline_value NUMERIC(14,2) DEFAULT 0,
                    channel      VARCHAR(50)  DEFAULT 'outros',
                    owner        VARCHAR(255) DEFAULT '',
                    service_type VARCHAR(255) DEFAULT '',
                    lead_date    DATE         DEFAULT CURRENT_DATE,
                    created_at   TIMESTAMP    DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS attachments (
                    id           SERIAL PRIMARY KEY,
                    contact_id   INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
                    filename     VARCHAR(255) NOT NULL,
                    object_name  VARCHAR(255) NOT NULL,
                    content_type VARCHAR(100),
                    created_at   TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS jwt_blocklist (
                    jti        TEXT PRIMARY KEY,
                    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 days')
                );
            """)

            # Migrate existing installs that lack the new columns
            for col, definition in [
                ("stage",          "VARCHAR(50)    DEFAULT 'novo'"),
                ("pipeline_value", "NUMERIC(14,2)  DEFAULT 0"),
                ("channel",        "VARCHAR(50)    DEFAULT 'outros'"),
                ("owner",          "VARCHAR(255)   DEFAULT ''"),
                ("service_type",   "VARCHAR(255)   DEFAULT ''"),
                ("lead_date",      "DATE           DEFAULT CURRENT_DATE"),
                # Isolamento entre empresas (multi-tenant): sem esta coluna, qualquer
                # utilizador autenticado via JWT válido conseguia ver/editar contactos
                # de QUALQUER empresa — a app.py passou a filtrar tudo por company_id.
                ("company_id",     "INTEGER REFERENCES companies(id)"),
            ]:
                cur.execute("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'contacts' AND column_name = %s
                """, (col,))
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE contacts ADD COLUMN {col} {definition}")

            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'attachments' AND column_name = 'company_id'
            """)
            if not cur.fetchone():
                cur.execute("ALTER TABLE attachments ADD COLUMN company_id INTEGER REFERENCES companies(id)")

            cur.execute("CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_attachments_company ON attachments(company_id)")

            conn.commit()
