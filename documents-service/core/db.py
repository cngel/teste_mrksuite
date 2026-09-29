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


TOP_LEVEL_FOLDERS = [
    "Colaboradores",
    "Contratos",
    "Recursos Humanos",
    "Financeiro",
    "Jurídico",
    "Formação",
    "Saúde e Segurança",
    "Políticas Internas",
    "Modelos",
    "Projetos",
    "Departamentos",
    "Contabilidade",
    "Arquivados",
]

DEFAULT_TAGS = [
    ("Urgente", "#EF4444"),
    ("Assinado", "#10B981"),
    ("Pendente", "#F59E0B"),
    ("Confidencial", "#6B7280"),
    ("Financeiro", "#0EA5E9"),
    ("RH", "#8B5CF6"),
    ("Jurídico", "#DC2626"),
    ("Expirado", "#B91C1C"),
    ("Importante", "#1D4ED8"),
]

EMPLOYEE_SUBFOLDERS = [
    "Documentos Pessoais",
    "Contrato",
    "Recibos Salariais",
    "Avaliações",
    "Certificados",
    "Férias",
    "Advertências",
    "Outros",
]

# Taxonomia de categorias por tipo de departamento. As chaves são padrões
# (minúsculas, sem acento) comparados contra o nome real do departamento
# vindo do RH; o primeiro que corresponder (substring) é usado. Cada valor
# é uma lista de categorias, cada uma com subcategorias opcionais.
DEPARTMENT_CATEGORY_TAXONOMY = [
    (["rh", "recursos humanos"], [
        ("Funcionários", ["Contratos de trabalho", "Documentos pessoais", "Currículos",
                           "Dados cadastrais", "Fichas de colaboradores", "Termos assinados"]),
        ("Gestão Interna", ["Regulamentos internos", "Políticas da empresa",
                             "Manual do colaborador", "Código de conduta"]),
        ("Formação", ["Certificados", "Comprovativos de participação", "Planos de formação"]),
        ("Avaliações", ["Avaliações de desempenho", "Feedbacks", "Planos de desenvolvimento"]),
    ]),
    (["comercial", "vendas"], [
        ("Clientes", ["Contratos comerciais", "Propostas enviadas", "Documentação do cliente", "Fichas comerciais"]),
        ("Vendas", ["Orçamentos", "Apresentações comerciais", "Catálogos", "Tabelas de preços"]),
        ("Negociações", ["Documentos de negociação", "Relatórios de reuniões", "Termos comerciais"]),
    ]),
    (["financeiro", "finanças"], [
        ("Faturas", []), ("Recibos", []), ("Notas de crédito", []),
        ("Relatórios financeiros", []), ("Comprovativos", []),
        ("Documentos fiscais", []), ("Auditorias", []),
    ]),
    (["administrativo", "administração"], [
        ("Atas", []), ("Licenças", []), ("Documentação legal", []),
        ("Regulamentos", []), ("Processos internos", []), ("Correspondências", []),
    ]),
    (["projeto", "projecto"], [
        ("Contratos do projecto", []), ("Briefings", []), ("Documentação técnica", []),
        ("Relatórios", []), ("Aprovações", []), ("Entregáveis", []),
    ]),
]

DEFAULT_DEPARTMENT_CATEGORIES = [("Documentos Gerais", [])]


def _strip_accents(text: str) -> str:
    import unicodedata
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def categories_for_department(department_name: str):
    normalized = _strip_accents(department_name or "").lower()
    for patterns, categories in DEPARTMENT_CATEGORY_TAXONOMY:
        if any(p in normalized for p in patterns):
            return categories
    return DEFAULT_DEPARTMENT_CATEGORIES


def init_db():
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS folders (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
                    kind VARCHAR(30) DEFAULT 'custom',
                    employee_id INTEGER,
                    is_system BOOLEAN DEFAULT false,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # NOTA: TOP_LEVEL_FOLDERS e DEFAULT_TAGS deixaram de ser criadas aqui no
            # arranque — a partir da introdução do isolamento multi-tenant (company_id),
            # cada empresa tem a sua própria árvore de pastas e etiquetas, criada de
            # forma preguiçosa (lazy) na primeira vez que a empresa acede a /folders ou
            # /tags (ver ensure_top_level_folders / ensure_default_tags mais abaixo).

            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'folders' AND column_name = 'department_id'
            """)
            if not cur.fetchone():
                cur.execute("ALTER TABLE folders ADD COLUMN department_id INTEGER")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
                    name VARCHAR(255) NOT NULL,
                    object_name VARCHAR(255) NOT NULL,
                    content_type VARCHAR(100),
                    size_bytes BIGINT DEFAULT 0,
                    category VARCHAR(100),
                    department_id INTEGER,
                    owner_id UUID,
                    owner_name VARCHAR(255),
                    status VARCHAR(20) DEFAULT 'ativo',
                    expiry_date DATE,
                    signature_status VARCHAR(20),
                    signed_by VARCHAR(255),
                    signed_at TIMESTAMP,
                    is_favorite BOOLEAN DEFAULT false,
                    deleted_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Nome "tags" já está em uso (projects-service) na mesma base de dados
            # partilhada — usa-se "doc_tags" para não colidir com esse esquema.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS doc_tags (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(50) NOT NULL,
                    color VARCHAR(20) DEFAULT '#6B7280'
                );
            """)

            # Antes do isolamento multi-tenant, o nome da etiqueta era globalmente único;
            # agora cada empresa tem as suas próprias etiquetas, por isso a unicidade
            # (aplicada em app.py) passa a ser por (name, company_id) — a constraint
            # antiga tem de ser removida em instalações já existentes.
            cur.execute("""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'doc_tags_name_key'
                    ) THEN
                        ALTER TABLE doc_tags DROP CONSTRAINT doc_tags_name_key;
                    END IF;
                END $$;
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS document_tags (
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    tag_id INTEGER NOT NULL REFERENCES doc_tags(id) ON DELETE CASCADE,
                    PRIMARY KEY (document_id, tag_id)
                );
            """)

            # Migração para instalações existentes que não têm as colunas novas
            for col, definition in [
                ("workflow_state",   "VARCHAR(20) DEFAULT 'publicado'"),
                ("current_version",  "INTEGER DEFAULT 1"),
                ("contact_id",       "INTEGER"),
                ("project_id",       "INTEGER"),
            ]:
                cur.execute(f"""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='documents' AND column_name='{col}'
                        ) THEN
                            ALTER TABLE documents ADD COLUMN {col} {definition};
                        END IF;
                    END $$;
                """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS document_versions (
                    id SERIAL PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    version_number INTEGER NOT NULL,
                    object_name VARCHAR(255) NOT NULL,
                    content_type VARCHAR(100),
                    size_bytes BIGINT DEFAULT 0,
                    uploaded_by UUID,
                    uploaded_by_name VARCHAR(255),
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE (document_id, version_number)
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS document_workflow_log (
                    id SERIAL PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    from_state VARCHAR(20),
                    to_state VARCHAR(20) NOT NULL,
                    user_id UUID,
                    user_name VARCHAR(255),
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS document_permissions (
                    id SERIAL PRIMARY KEY,
                    scope_type VARCHAR(20) NOT NULL CHECK (scope_type IN ('company', 'department', 'role', 'user')),
                    scope_value VARCHAR(255),
                    folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
                    can_view BOOLEAN DEFAULT true,
                    can_edit BOOLEAN DEFAULT false,
                    can_delete BOOLEAN DEFAULT false,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS doc_templates (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    category VARCHAR(100),
                    object_name VARCHAR(255) NOT NULL,
                    content_type VARCHAR(100),
                    size_bytes BIGINT DEFAULT 0,
                    created_by UUID,
                    created_by_name VARCHAR(255),
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS doc_notifications (
                    id SERIAL PRIMARY KEY,
                    user_id UUID NOT NULL,
                    type VARCHAR(30) NOT NULL,
                    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                    message TEXT NOT NULL,
                    is_read BOOLEAN DEFAULT false,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Defensivo: garante is_admin em "usuarios" mesmo que o auth-service
            # ainda não tenha corrido a sua própria migração (arranque em paralelo,
            # mesma base de dados partilhada).
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'usuarios' AND column_name = 'is_admin'
            """)
            if not cur.fetchone():
                cur.execute("ALTER TABLE usuarios ADD COLUMN is_admin BOOLEAN DEFAULT false")

            # Isolamento entre empresas (multi-tenant): sem estas colunas, qualquer
            # utilizador autenticado via JWT válido conseguia ver/editar pastas e
            # documentos de QUALQUER empresa — a app.py passou a filtrar tudo por
            # company_id.
            for table in (
                "folders", "documents", "doc_tags", "document_versions", "document_workflow_log",
                "document_permissions", "doc_templates", "doc_notifications",
            ):
                cur.execute("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = %s AND column_name = 'company_id'
                """, (table,))
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN company_id INTEGER REFERENCES companies(id)")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_company ON {table}(company_id)")

            # Substitui a antiga unicidade global de doc_tags.name (removida acima) por
            # uma unicidade por empresa, aplicada pela própria BD — sem isto, dois pedidos
            # concorrentes de criação da mesma etiqueta na mesma empresa podiam passar
            # ambos pela verificação em app.py e criar duplicados.
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS doc_tags_name_company_key
                ON doc_tags (name, company_id)
            """)

            conn.commit()


def get_or_create_top_folder(cur, name: str, company_id: int) -> int:
    cur.execute(
        "SELECT id FROM folders WHERE name = %s AND parent_id IS NULL AND is_system = true AND company_id = %s",
        (name, company_id),
    )
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute(
        """INSERT INTO folders (name, parent_id, kind, is_system, company_id)
           VALUES (%s, NULL, 'system', true, %s) RETURNING id""",
        (name, company_id),
    )
    return cur.fetchone()["id"]


def ensure_top_level_folders(cur, company_id: int) -> None:
    """Cria (de forma preguiçosa, na primeira vez que a empresa acede a /folders) a
    árvore de pastas de sistema para essa empresa — cada empresa tem a sua própria,
    isolada das restantes."""
    for name in TOP_LEVEL_FOLDERS:
        get_or_create_top_folder(cur, name, company_id)


def ensure_default_tags(cur, company_id: int) -> None:
    """Idem para as etiquetas por omissão — criadas por empresa, não globalmente."""
    for name, color in DEFAULT_TAGS:
        cur.execute("SELECT 1 FROM doc_tags WHERE name = %s AND company_id = %s", (name, company_id))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO doc_tags (name, color, company_id) VALUES (%s, %s, %s)",
                (name, color, company_id),
            )


def get_user_profile(cur, user_id: str) -> dict:
    cur.execute("""
        SELECT u.id AS user_id, u.nome, u.email, COALESCE(u.is_admin, false) AS is_admin,
               e.id AS employee_id, e.role, e.department_id
        FROM usuarios u
        LEFT JOIN employees e ON e.email = u.email
        WHERE u.id = %s
    """, (user_id,))
    row = cur.fetchone()
    if row:
        return row
    return {
        "user_id": user_id, "nome": None, "email": None, "is_admin": False,
        "employee_id": None, "role": None, "department_id": None,
    }


def resolve_folder_permission(cur, user_id: str, folder_id: int | None) -> dict:
    """Sobe a árvore de pastas a partir de folder_id à procura da política mais próxima.
    Sem nenhuma regra definida em toda a cadeia, o acesso é livre (mantém o comportamento
    anterior à introdução de permissões)."""
    default = {"can_view": True, "can_edit": True, "can_delete": True}
    if folder_id is None:
        return default

    profile = get_user_profile(cur, user_id)
    if profile["is_admin"]:
        return default

    current_id = folder_id
    seen = set()
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        cur.execute("SELECT * FROM document_permissions WHERE folder_id = %s", (current_id,))
        rules = cur.fetchall()
        if rules:
            can_view = can_edit = can_delete = False
            for rule in rules:
                matches = (
                    rule["scope_type"] == "company"
                    or (rule["scope_type"] == "role" and profile["role"] and rule["scope_value"] == profile["role"])
                    or (rule["scope_type"] == "department" and profile["department_id"] is not None
                        and rule["scope_value"] == str(profile["department_id"]))
                    or (rule["scope_type"] == "user" and rule["scope_value"] == str(user_id))
                )
                if matches:
                    can_view = can_view or rule["can_view"]
                    can_edit = can_edit or rule["can_edit"]
                    can_delete = can_delete or rule["can_delete"]
            return {"can_view": can_view, "can_edit": can_edit, "can_delete": can_delete}

        cur.execute("SELECT parent_id FROM folders WHERE id = %s", (current_id,))
        parent = cur.fetchone()
        current_id = parent["parent_id"] if parent else None

    return default
