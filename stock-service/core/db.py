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
                CREATE TABLE IF NOT EXISTS stk_counters (
                    doc_type VARCHAR(20) PRIMARY KEY,
                    next_seq INTEGER NOT NULL DEFAULT 1
                );
            """)

            # Armazéns e lojas
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stk_warehouses (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    code VARCHAR(30),
                    type VARCHAR(20) NOT NULL DEFAULT 'armazem',
                    address TEXT,
                    active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Artigos
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stk_items (
                    id SERIAL PRIMARY KEY,
                    sku VARCHAR(60),
                    barcode VARCHAR(60),
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    category VARCHAR(100),
                    unit VARCHAR(20) NOT NULL DEFAULT 'un',
                    min_stock NUMERIC(14,3) NOT NULL DEFAULT 0,
                    track_batches BOOLEAN NOT NULL DEFAULT false,
                    active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # Saldo em stock por artigo+armazém — quantidade e Custo Médio Ponderado (CMP) em AOA
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stk_item_stock (
                    item_id INTEGER NOT NULL REFERENCES stk_items(id) ON DELETE CASCADE,
                    warehouse_id INTEGER NOT NULL REFERENCES stk_warehouses(id) ON DELETE CASCADE,
                    quantity NUMERIC(14,3) NOT NULL DEFAULT 0,
                    avg_cost NUMERIC(14,4) NOT NULL DEFAULT 0,
                    min_stock NUMERIC(14,3),
                    company_id INTEGER,
                    PRIMARY KEY (item_id, warehouse_id)
                );
            """)

            # Lotes (só usados quando o artigo tem track_batches=true) — consumidos por FIFO
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stk_batches (
                    id SERIAL PRIMARY KEY,
                    item_id INTEGER NOT NULL REFERENCES stk_items(id) ON DELETE CASCADE,
                    warehouse_id INTEGER NOT NULL REFERENCES stk_warehouses(id) ON DELETE CASCADE,
                    batch_number VARCHAR(60),
                    expiry_date DATE,
                    quantity_received NUMERIC(14,3) NOT NULL,
                    quantity_remaining NUMERIC(14,3) NOT NULL,
                    unit_cost NUMERIC(14,4) NOT NULL DEFAULT 0,
                    received_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    company_id INTEGER
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_stk_batches_fifo
                ON stk_batches (item_id, warehouse_id, received_at);
            """)

            # Taxas de câmbio para AOA (lançadas manualmente, sem integração ao vivo)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stk_exchange_rates (
                    id SERIAL PRIMARY KEY,
                    currency_code VARCHAR(10) NOT NULL,
                    rate_to_aoa NUMERIC(14,6) NOT NULL,
                    effective_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    company_id INTEGER,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_stk_exchange_rates_lookup
                ON stk_exchange_rates (currency_code, effective_date);
            """)

            # Livro de movimentos de stock
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stk_movements (
                    id SERIAL PRIMARY KEY,
                    movement_type VARCHAR(20) NOT NULL,
                    item_id INTEGER NOT NULL REFERENCES stk_items(id),
                    warehouse_id INTEGER NOT NULL REFERENCES stk_warehouses(id),
                    destination_warehouse_id INTEGER REFERENCES stk_warehouses(id),
                    quantity NUMERIC(14,3) NOT NULL,
                    currency_code VARCHAR(10) NOT NULL DEFAULT 'AOA',
                    exchange_rate NUMERIC(14,6) NOT NULL DEFAULT 1,
                    unit_cost NUMERIC(14,4),
                    unit_cost_aoa NUMERIC(14,4),
                    total_cost_aoa NUMERIC(14,2),
                    sale_price NUMERIC(14,2),
                    batch_number VARCHAR(60),
                    expiry_date DATE,
                    reason VARCHAR(255),
                    reference_doc_type VARCHAR(30),
                    reference_doc_id INTEGER,
                    client_ref VARCHAR(80),
                    created_by VARCHAR(255),
                    created_at TIMESTAMP DEFAULT NOW(),
                    company_id INTEGER
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_stk_movements_item_wh
                ON stk_movements (item_id, warehouse_id);
            """)

            # Guias de transporte — os campos agt_* ficam nulos até a validação/certificação
            # junto da AGT (feita internamente pela empresa) os preencher.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stk_transport_guides (
                    id SERIAL PRIMARY KEY,
                    doc_number VARCHAR(30) NOT NULL UNIQUE,
                    origin_warehouse_id INTEGER NOT NULL REFERENCES stk_warehouses(id),
                    destination_warehouse_id INTEGER REFERENCES stk_warehouses(id),
                    destination_name VARCHAR(255),
                    destination_nif VARCHAR(50),
                    destination_address TEXT,
                    transporter_name VARCHAR(255),
                    vehicle_plate VARCHAR(30),
                    issue_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    status VARCHAR(20) NOT NULL DEFAULT 'rascunho',
                    atcud VARCHAR(60),
                    agt_validation_code VARCHAR(100),
                    agt_hash TEXT,
                    notes TEXT,
                    created_by VARCHAR(255),
                    created_at TIMESTAMP DEFAULT NOW(),
                    company_id INTEGER
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS stk_transport_guide_lines (
                    id SERIAL PRIMARY KEY,
                    guide_id INTEGER NOT NULL REFERENCES stk_transport_guides(id) ON DELETE CASCADE,
                    item_id INTEGER NOT NULL REFERENCES stk_items(id),
                    quantity NUMERIC(14,3) NOT NULL,
                    batch_id INTEGER REFERENCES stk_batches(id)
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_stk_transport_guide_lines_guide
                ON stk_transport_guide_lines (guide_id);
            """)

            # Documentos anexados a guias de transporte / movimentos (mesmo padrão de fin_documents)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stk_documents (
                    id SERIAL PRIMARY KEY,
                    entity_type VARCHAR(20) NOT NULL,
                    entity_id INTEGER NOT NULL,
                    document_type VARCHAR(50) NOT NULL,
                    filename VARCHAR(255) NOT NULL,
                    object_name VARCHAR(255) NOT NULL,
                    content_type VARCHAR(100),
                    uploaded_by VARCHAR(255),
                    created_at TIMESTAMP DEFAULT NOW(),
                    company_id INTEGER
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_stk_documents_entity
                ON stk_documents (entity_type, entity_id);
            """)

            # Isolamento entre empresas (multi-tenant): mesma abordagem dos restantes
            # microserviços — sem estas colunas, qualquer utilizador autenticado via
            # JWT válido conseguia ver/editar dados de stock de QUALQUER empresa.
            for table in (
                "stk_warehouses", "stk_items", "stk_item_stock", "stk_batches",
                "stk_exchange_rates", "stk_movements", "stk_transport_guides",
                "stk_transport_guide_lines", "stk_documents",
            ):
                cur.execute("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = %s AND column_name = 'company_id'
                """, (table,))
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN company_id INTEGER REFERENCES companies(id)")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_company ON {table}(company_id)")

            # client_ref só é único dentro da mesma empresa (idempotência de reenvio
            # de um cliente offline/POS) — sem isto duas empresas nunca poderiam usar
            # o mesmo UUID gerado localmente, o que não devia ser um requisito.
            cur.execute("""
                SELECT 1 FROM pg_indexes WHERE indexname = 'stk_movements_client_ref_company_key'
            """)
            if not cur.fetchone():
                cur.execute("""
                    CREATE UNIQUE INDEX stk_movements_client_ref_company_key
                    ON stk_movements (client_ref, company_id) WHERE client_ref IS NOT NULL
                """)

            conn.commit()


# -------------------------
# NUMERAÇÃO SEQUENCIAL IMUTÁVEL
# -------------------------

DOC_PREFIX = {"guia": "GT"}


def next_doc_number(cur, doc_type: str) -> str:
    cur.execute(
        "INSERT INTO stk_counters (doc_type, next_seq) VALUES (%s, 1) ON CONFLICT DO NOTHING",
        (doc_type,),
    )
    cur.execute(
        "SELECT next_seq FROM stk_counters WHERE doc_type=%s FOR UPDATE",
        (doc_type,),
    )
    seq = cur.fetchone()["next_seq"]
    cur.execute(
        "UPDATE stk_counters SET next_seq = next_seq + 1 WHERE doc_type=%s",
        (doc_type,),
    )
    return f"{DOC_PREFIX[doc_type]}-{seq:06d}"
