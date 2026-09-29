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
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    color VARCHAR(20) DEFAULT '#3B82F6',
                    status VARCHAR(20) DEFAULT 'ativo',
                    due_date DATE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    status VARCHAR(30) DEFAULT 'todo',
                    priority VARCHAR(20) DEFAULT 'media',
                    assignee_id INTEGER,
                    due_date DATE,
                    position INTEGER DEFAULT 0,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    id SERIAL PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    name VARCHAR(50) NOT NULL,
                    color VARCHAR(20) DEFAULT '#6B7280'
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS task_tags (
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    PRIMARY KEY (task_id, tag_id)
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS task_comments (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    author_id UUID,
                    author_name VARCHAR(255),
                    body TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS task_attachments (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    filename VARCHAR(255) NOT NULL,
                    object_name VARCHAR(255) NOT NULL,
                    content_type VARCHAR(100),
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Isolamento entre empresas (multi-tenant): sem estas colunas, qualquer
            # utilizador autenticado via JWT válido conseguia ver/editar projectos,
            # tarefas e anexos de QUALQUER empresa — a app.py passou a filtrar tudo
            # por company_id.
            for table in ("projects", "tasks", "tags", "task_comments", "task_attachments"):
                cur.execute("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = %s AND column_name = 'company_id'
                """, (table,))
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN company_id INTEGER REFERENCES companies(id)")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_company ON {table}(company_id)")

            conn.commit()
