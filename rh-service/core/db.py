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
            # Departamentos
            cur.execute("""
                CREATE TABLE IF NOT EXISTS departments (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    color VARCHAR(20) NOT NULL DEFAULT '#6B7280'
                );
            """)

            # Employees (núcleo do RH)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    id SERIAL PRIMARY KEY,
                    full_name VARCHAR(255) NOT NULL,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    phone VARCHAR(50),
                    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                    role VARCHAR(100),
                    status VARCHAR(50) DEFAULT 'online',
                    reports_to INTEGER REFERENCES employees(id) ON DELETE SET NULL,
                    birth_date DATE,
                    contract_type VARCHAR(50),
                    hire_date TIMESTAMP DEFAULT NOW(),
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Migração para instalações existentes que não têm as colunas novas
            for col, definition in [
                ("department_id",  "INTEGER REFERENCES departments(id) ON DELETE SET NULL"),
                ("status",         "VARCHAR(50) DEFAULT 'online'"),
                ("reports_to",     "INTEGER REFERENCES employees(id) ON DELETE SET NULL"),
                ("birth_date",     "DATE"),
                ("contract_type",  "VARCHAR(50)"),
                # Dados Pessoais
                ("employee_number",   "VARCHAR(50)"),
                ("first_name",        "VARCHAR(100)"),
                ("last_name",         "VARCHAR(100)"),
                ("gender",            "VARCHAR(20)"),
                ("nationality",       "VARCHAR(100)"),
                ("marital_status",    "VARCHAR(30)"),
                ("mobile",            "VARCHAR(50)"),
                ("address",           "VARCHAR(255)"),
                ("province",         "VARCHAR(100)"),
                ("city",              "VARCHAR(100)"),
                # Documentos
                ("bi_number",          "VARCHAR(50)"),
                ("bi_expiry",          "DATE"),
                ("nif",                "VARCHAR(50)"),
                ("niss",               "VARCHAR(50)"),
                ("passport_number",    "VARCHAR(50)"),
                ("passport_expiry",    "DATE"),
                # Financeiro (dados bancários do colaborador; salário vive em contracts)
                ("bank_name",     "VARCHAR(100)"),
                ("bank_account",  "VARCHAR(50)"),
                ("iban",          "VARCHAR(50)"),
                # Foto de perfil (separada do arquivo de documentos)
                ("photo_object_name", "VARCHAR(255)"),
            ]:
                cur.execute("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'employees' AND column_name = %s
                """, (col,))
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE employees ADD COLUMN {col} {definition}")

            # Check-in / Check-out (presença)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id SERIAL PRIMARY KEY,
                    employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                    check_in TIMESTAMP,
                    check_out TIMESTAMP
                );
            """)

            # Férias / pedidos de ausência
            cur.execute("""
                CREATE TABLE IF NOT EXISTS leave_requests (
                    id SERIAL PRIMARY KEY,
                    employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    reason TEXT,
                    status VARCHAR(50) DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Documentos dos funcionários
            cur.execute("""
                CREATE TABLE IF NOT EXISTS employee_documents (
                    id SERIAL PRIMARY KEY,
                    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,

                    document_type VARCHAR(50) NOT NULL,

                    filename VARCHAR(255) NOT NULL,
                    object_name VARCHAR(255) NOT NULL,
                    content_type VARCHAR(100),

                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Contratos (fonte de verdade de salário/subsídios/tipo de vínculo)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS contracts (
                    id SERIAL PRIMARY KEY,
                    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                    contract_type VARCHAR(50) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE,
                    trial_period_days INTEGER,
                    weekly_hours NUMERIC(5,2),
                    base_salary NUMERIC(14,2) NOT NULL,
                    meal_allowance NUMERIC(14,2) DEFAULT 0,
                    transport_allowance NUMERIC(14,2) DEFAULT 0,
                    apply_inss BOOLEAN DEFAULT true,
                    apply_irt BOOLEAN DEFAULT true,
                    notes TEXT,
                    status VARCHAR(20) DEFAULT 'ativo',
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Formações
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trainings (
                    id SERIAL PRIMARY KEY,
                    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                    title VARCHAR(255) NOT NULL,
                    provider VARCHAR(255),
                    status VARCHAR(20) DEFAULT 'ongoing',
                    start_date DATE,
                    end_date DATE,
                    hours INTEGER,
                    cert_url VARCHAR(500),
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Avaliações
            cur.execute("""
                CREATE TABLE IF NOT EXISTS evaluations (
                    id SERIAL PRIMARY KEY,
                    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                    cycle VARCHAR(20) DEFAULT 'trimestral',
                    period VARCHAR(50),
                    eval_date DATE NOT NULL,
                    rating INTEGER NOT NULL,
                    comments TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Recibos de vencimento
            cur.execute("""
                CREATE TABLE IF NOT EXISTS payslips (
                    id SERIAL PRIMARY KEY,
                    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                    month VARCHAR(2) NOT NULL,
                    year INTEGER NOT NULL,
                    base_salary NUMERIC(14,2) NOT NULL DEFAULT 0,
                    extras NUMERIC(14,2) DEFAULT 0,
                    deductions NUMERIC(14,2) DEFAULT 0,
                    irt NUMERIC(14,2) DEFAULT 0,
                    social_security NUMERIC(14,2) DEFAULT 0,
                    net NUMERIC(14,2) NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Onboarding
            cur.execute("""
                CREATE TABLE IF NOT EXISTS onboarding_checklists (
                    id SERIAL PRIMARY KEY,
                    employee_id INTEGER NOT NULL UNIQUE REFERENCES employees(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS onboarding_items (
                    id SERIAL PRIMARY KEY,
                    checklist_id INTEGER NOT NULL REFERENCES onboarding_checklists(id) ON DELETE CASCADE,
                    label VARCHAR(255) NOT NULL,
                    done BOOLEAN DEFAULT false,
                    position INTEGER DEFAULT 0
                );
            """)

            cur.execute('''
                    CREATE TABLE IF NOT EXISTS salary_profiles (
                    id SERIAL PRIMARY KEY,

                    employee_id INTEGER NOT NULL
                        REFERENCES employees(id)
                        ON DELETE CASCADE,

                    base_salary NUMERIC(15,2) NOT NULL,

                    food_allowance NUMERIC(15,2) DEFAULT 0,
                    transport_allowance NUMERIC(15,2) DEFAULT 0,
                    other_allowance NUMERIC(15,2) DEFAULT 0,

                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),

                    UNIQUE(employee_id)
                );''')
            
            cur.execute('''
                    CREATE TABLE IF NOT EXISTS payrolls (

                    id SERIAL PRIMARY KEY,

                    employee_id INTEGER NOT NULL
                        REFERENCES employees(id)
                        ON DELETE CASCADE,

                    month INTEGER NOT NULL,
                    year INTEGER NOT NULL,

                    total_earnings NUMERIC(15,2),
                    total_deductions NUMERIC(15,2),
                    net_salary NUMERIC(15,2),

                    status VARCHAR(30) DEFAULT 'draft',

                    created_at TIMESTAMP DEFAULT NOW(),

                    gross_salary NUMERIC(15,2),

                    UNIQUE(employee_id, month, year)
                    );''')
            cur.execute('''
                    CREATE TABLE IF NOT EXISTS payroll_items (

                        id SERIAL PRIMARY KEY,

                        payroll_id INTEGER
                            REFERENCES payrolls(id)
                            ON DELETE CASCADE,

                        item_type VARCHAR(20),

                        description VARCHAR(100),

                        amount NUMERIC(15,2),

                        created_value NUMERIC(15,2),

                        created_at TIMESTAMP DEFAULT NOW()
                    );
                    ''')

#            cur.execute('''
#                    ALTER TABLE deduction_rules
#                    ADD CONSTRAINT uq_deduction_rule_name_country
#                    UNIQUE (country_code, name);
#                        ''')

            cur.execute('''
                    CREATE TABLE IF NOT EXISTS deduction_rules (

                        id SERIAL PRIMARY KEY,

                        name VARCHAR(100) NOT NULL,

                        description VARCHAR(255),

                        calculation_type VARCHAR(20) NOT NULL,

                        calculation_base VARCHAR(50) DEFAULT 'gross_salary',

                        value NUMERIC(15,2) DEFAULT 0,

                        country_code VARCHAR(5) DEFAULT 'AO',

                        active BOOLEAN DEFAULT TRUE,

                        created_at TIMESTAMP DEFAULT NOW(),

                        updated_at TIMESTAMP DEFAULT NOW()
                    );
                    ''')

            cur.execute('''
                    CREATE TABLE IF NOT EXISTS payroll_deductions (

                        id SERIAL PRIMARY KEY,

                        payroll_id INTEGER
                            REFERENCES payrolls(id)
                            ON DELETE CASCADE,

                        deduction_rule_id INTEGER
                            REFERENCES deduction_rules(id),

                        description VARCHAR(100),

                        amount NUMERIC(15,2),

                        created_value NUMERIC(15,2),

                        created_at TIMESTAMP DEFAULT NOW()
                        );
                        ''')
            cur.execute('''
                    CREATE TABLE IF NOT EXISTS deduction_brackets (

                        id SERIAL PRIMARY KEY,

                        deduction_rule_id INTEGER
                            REFERENCES deduction_rules(id)
                            ON DELETE CASCADE,

                        minimum_amount NUMERIC(15,2),

                        maximum_amount NUMERIC(15,2),

                        percentage NUMERIC(5,2),

                        fixed_amount NUMERIC(15,2)
                    );
                    ''')

            # Isolamento entre empresas (multi-tenant): sem estas colunas, qualquer
            # utilizador autenticado via JWT válido conseguia ver/editar dados de RH
            # (funcionários, contratos, folhas salariais, etc.) de QUALQUER empresa —
            # a app.py passou a filtrar tudo por company_id.
            for table in (
                "departments", "employees", "attendance", "leave_requests", "employee_documents",
                "contracts", "trainings", "evaluations", "payslips", "onboarding_checklists",
                "onboarding_items", "salary_profiles", "deduction_rules", "payrolls", "payroll_items",
            ):
                cur.execute("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = %s AND column_name = 'company_id'
                """, (table,))
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN company_id INTEGER REFERENCES companies(id)")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_company ON {table}(company_id)")

            # O UNIQUE(name) original era global (pré multi-tenant): uma empresa
            # criar "TI" bloqueava todas as outras de usarem o mesmo nome. Troca-se
            # por UNIQUE(company_id, name), que isola a unicidade por empresa.
            cur.execute("""
                SELECT con.conname FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                WHERE rel.relname = 'departments' AND con.contype = 'u'
                  AND con.conkey = (
                      SELECT array_agg(attnum) FROM pg_attribute
                      WHERE attrelid = rel.oid AND attname = 'name'
                  )
            """)
            row = cur.fetchone()
            if row:
                constraint_name = row["conname"] if isinstance(row, dict) else row[0]
                cur.execute(f'ALTER TABLE departments DROP CONSTRAINT "{constraint_name}"')
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_departments_company_name
                ON departments (company_id, name)
            """)

            conn.commit()