import os
import threading

import psycopg2
import psycopg2.extras
import psycopg2.pool

psycopg2.extras.register_uuid()

_pool = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        dsn = (
            f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
            f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:5432/{os.environ['POSTGRES_DB']}"
        )
        _pool = psycopg2.pool.ThreadedConnectionPool(
            2, 20,
            dsn=dsn,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
    return _pool


def execute(sql: str, params=()):
    pool = _get_pool()
    for attempt in range(2):
        conn = pool.getconn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.NamedTupleCursor) as cur:
                cur.execute(sql, params or ())
                conn.commit()
                try:
                    rows = cur.fetchall()
                except psycopg2.ProgrammingError:
                    rows = []
            pool.putconn(conn)
            return rows
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            try:
                pool.putconn(conn, close=True)
            except Exception:
                pass
            if attempt == 1:
                raise
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            pool.putconn(conn)
            raise


def init_db():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    nome      TEXT NOT NULL,
                    email     TEXT UNIQUE NOT NULL,
                    senha     TEXT NOT NULL,
                    criado_em TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS jwt_blocklist (
                    jti        TEXT PRIMARY KEY,
                    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 days')
                );
            """)

            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'usuarios' AND column_name = 'is_admin'
            """)
            if not cur.fetchone():
                cur.execute("ALTER TABLE usuarios ADD COLUMN is_admin BOOLEAN DEFAULT false")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id             SERIAL PRIMARY KEY,
                    name           TEXT NOT NULL,
                    owner_user_id  UUID NOT NULL,
                    created_at     TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # Adiciona a coluna whatsapp à tabela companies
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'companies' AND column_name = 'whatsapp'
            """)
            if not cur.fetchone():
                cur.execute("ALTER TABLE companies ADD COLUMN whatsapp TEXT")

            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'usuarios' AND column_name = 'whatsapp'
            """)
            if not cur.fetchone():
                cur.execute("ALTER TABLE usuarios ADD COLUMN whatsapp TEXT")

            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'usuarios' AND column_name = 'company_id'
            """)
            if not cur.fetchone():
                cur.execute("ALTER TABLE usuarios ADD COLUMN company_id INTEGER REFERENCES companies(id)")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_module_permissions (
                    user_id     UUID NOT NULL,
                    module      VARCHAR(30) NOT NULL,
                    granted_at  TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (user_id, module)
                );
            """)
        conn.commit()
    finally:
        pool.putconn(conn)


def _connection_warmer():
    """Mantém o pool vivo (evita timeout em bases de dados serverless) e limpa
    entradas expiradas do jwt_blocklist (evita crescimento infinito da tabela)."""
    import time
    while True:
        time.sleep(45)
        try:
            pool = _get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.execute("DELETE FROM jwt_blocklist WHERE expires_at <= NOW()")
                conn.commit()
            pool.putconn(conn)
        except Exception:
            pass


threading.Thread(target=_connection_warmer, daemon=True).start()
