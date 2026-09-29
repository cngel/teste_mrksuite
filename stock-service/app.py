import os
import uuid
import xml.etree.ElementTree as ET
from datetime import date
from xml.dom import minidom

import psycopg2
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordBearer

if __package__:
    from .core.db import get_connection, init_db, next_doc_number
    from .core.storage import upload_bytes, ensure_bucket, delete_object
    from .core.url_service import get_presigned_url
    from .core.accounting_client import (
        post_journal_entry, STOCK_ACCOUNT_CODE, CMV_ACCOUNT_CODE, SUPPLIERS_ACCOUNT_CODE,
    )
else:
    from core.db import get_connection, init_db, next_doc_number
    from core.storage import upload_bytes, ensure_bucket, delete_object
    from core.url_service import get_presigned_url
    from core.accounting_client import (
        post_journal_entry, STOCK_ACCOUNT_CODE, CMV_ACCOUNT_CODE, SUPPLIERS_ACCOUNT_CODE,
    )

app = FastAPI(title="Stock Service (Inventário)", version="1.0.0")

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

VALID_MOVEMENT_TYPES = {"entrada", "saida", "transferencia", "ajuste", "devolucao"}


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
    """Isolamento entre empresas: toda a leitura/escrita de stock tem de ser
    filtrada por company_id — nunca basta um JWT válido, seja lá de que
    empresa for."""
    company_id = claims.get("company_id")
    if company_id is None:
        raise HTTPException(status_code=403, detail="Utilizador sem empresa associada")
    return company_id


# -------------------------
# MODELS
# -------------------------

class WarehouseCreate(BaseModel):
    name: str
    code: str | None = None
    type: str = "armazem"
    address: str | None = None


class WarehouseUpdate(WarehouseCreate):
    active: bool = True


class ItemCreate(BaseModel):
    sku: str | None = None
    barcode: str | None = None
    name: str
    description: str | None = None
    category: str | None = None
    unit: str = "un"
    min_stock: float = 0
    track_batches: bool = False


class ItemUpdate(ItemCreate):
    active: bool = True


class ExchangeRateCreate(BaseModel):
    currency_code: str
    rate_to_aoa: float
    effective_date: str | None = None


class MovementCreate(BaseModel):
    movement_type: str
    item_id: int
    warehouse_id: int
    destination_warehouse_id: int | None = None
    quantity: float
    currency_code: str = "AOA"
    unit_cost: float | None = None
    sale_price: float | None = None
    batch_number: str | None = None
    expiry_date: str | None = None
    reason: str | None = None
    reference_doc_type: str | None = None
    reference_doc_id: int | None = None
    client_ref: str | None = None


class TransportGuideLineIn(BaseModel):
    item_id: int
    quantity: float
    batch_id: int | None = None


class TransportGuideCreate(BaseModel):
    origin_warehouse_id: int
    destination_warehouse_id: int | None = None
    destination_name: str | None = None
    destination_nif: str | None = None
    destination_address: str | None = None
    transporter_name: str | None = None
    vehicle_plate: str | None = None
    issue_date: str | None = None
    notes: str | None = None
    lines: list[TransportGuideLineIn]


class TransportGuideValidate(BaseModel):
    atcud: str | None = None
    agt_validation_code: str | None = None
    agt_hash: str | None = None


# -------------------------
# HELPERS — CUSTEIO (CMP / FIFO)
# -------------------------

def get_exchange_rate(cur, currency_code: str, company_id: int) -> float:
    if currency_code == "AOA":
        return 1.0
    cur.execute("""
        SELECT rate_to_aoa FROM stk_exchange_rates
        WHERE currency_code=%s AND company_id=%s AND effective_date <= CURRENT_DATE
        ORDER BY effective_date DESC LIMIT 1
    """, (currency_code, company_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=422, detail=f"Não há taxa de câmbio registada para {currency_code}")
    return float(row["rate_to_aoa"])


def get_or_create_stock_row(cur, item_id: int, warehouse_id: int, company_id: int) -> dict:
    cur.execute("""
        SELECT * FROM stk_item_stock WHERE item_id=%s AND warehouse_id=%s AND company_id=%s FOR UPDATE
    """, (item_id, warehouse_id, company_id))
    row = cur.fetchone()
    if row:
        return row
    cur.execute("""
        INSERT INTO stk_item_stock (item_id, warehouse_id, quantity, avg_cost, company_id)
        VALUES (%s, %s, 0, 0, %s)
        ON CONFLICT (item_id, warehouse_id) DO NOTHING
    """, (item_id, warehouse_id, company_id))
    cur.execute("""
        SELECT * FROM stk_item_stock WHERE item_id=%s AND warehouse_id=%s AND company_id=%s FOR UPDATE
    """, (item_id, warehouse_id, company_id))
    return cur.fetchone()


def apply_entrada(cur, item: dict, warehouse_id: int, quantity: float, unit_cost_aoa: float, company_id: int) -> float:
    """Actualiza o Custo Médio Ponderado (CMP) do artigo/armazém após uma entrada."""
    stock = get_or_create_stock_row(cur, item["id"], warehouse_id, company_id)
    old_qty = float(stock["quantity"])
    old_cost = float(stock["avg_cost"])
    new_qty = old_qty + quantity
    new_avg = (old_qty * old_cost + quantity * unit_cost_aoa) / new_qty if new_qty > 0 else 0.0
    cur.execute("""
        UPDATE stk_item_stock SET quantity=%s, avg_cost=%s
        WHERE item_id=%s AND warehouse_id=%s AND company_id=%s
    """, (round(new_qty, 3), round(new_avg, 4), item["id"], warehouse_id, company_id))
    return new_avg


def consume_fifo(cur, item_id: int, warehouse_id: int, quantity: float, company_id: int) -> float:
    """Consome os lotes mais antigos primeiro (FIFO) até satisfazer a quantidade;
    devolve o custo total efectivamente consumido. Lança 409 se os lotes não
    cobrirem a quantidade pedida."""
    cur.execute("""
        SELECT * FROM stk_batches
        WHERE item_id=%s AND warehouse_id=%s AND company_id=%s AND quantity_remaining > 0
        ORDER BY received_at ASC FOR UPDATE
    """, (item_id, warehouse_id, company_id))
    batches = cur.fetchall()
    remaining = quantity
    total_cost = 0.0
    for b in batches:
        if remaining <= 1e-9:
            break
        take = min(remaining, float(b["quantity_remaining"]))
        total_cost += take * float(b["unit_cost"])
        cur.execute(
            "UPDATE stk_batches SET quantity_remaining=%s WHERE id=%s",
            (round(float(b["quantity_remaining"]) - take, 3), b["id"]),
        )
        remaining -= take
    if remaining > 1e-9:
        raise HTTPException(status_code=409, detail="Stock insuficiente nos lotes disponíveis")
    return total_cost


def apply_saida(cur, item: dict, warehouse_id: int, quantity: float, company_id: int) -> tuple[float, float]:
    """Dá baixa em stock; devolve (custo_unitário_usado, custo_total). Usa FIFO por
    lotes quando o artigo tem track_batches, caso contrário usa o CMP corrente."""
    stock = get_or_create_stock_row(cur, item["id"], warehouse_id, company_id)
    if float(stock["quantity"]) < quantity - 1e-9:
        raise HTTPException(status_code=409, detail="Stock insuficiente")

    if item["track_batches"]:
        total_cost = consume_fifo(cur, item["id"], warehouse_id, quantity, company_id)
        unit_cost_used = total_cost / quantity if quantity else 0.0
    else:
        unit_cost_used = float(stock["avg_cost"])
        total_cost = unit_cost_used * quantity

    new_qty = float(stock["quantity"]) - quantity
    cur.execute("""
        UPDATE stk_item_stock SET quantity=%s WHERE item_id=%s AND warehouse_id=%s AND company_id=%s
    """, (round(new_qty, 3), item["id"], warehouse_id, company_id))
    return unit_cost_used, total_cost


# -------------------------
# HELPERS — DOCUMENTOS
# -------------------------

ENTITY_TABLE = {"guide": "stk_transport_guides"}


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


# -------------------------
# ARMAZÉNS
# -------------------------

@app.get("/warehouses")
def list_warehouses(active: bool | None = None, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            clauses, params = ["company_id=%s"], [company_id]
            if active is not None:
                clauses.append("active=%s")
                params.append(active)
            where = " AND ".join(clauses)
            cur.execute(f"SELECT * FROM stk_warehouses WHERE {where} ORDER BY name", params)
            return cur.fetchall()


@app.post("/warehouses", status_code=201)
def create_warehouse(body: WarehouseCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO stk_warehouses (name, code, type, address, company_id)
                VALUES (%s, %s, %s, %s, %s) RETURNING *
            """, (body.name, body.code, body.type, body.address, company_id))
            conn.commit()
            return cur.fetchone()


@app.get("/warehouses/{warehouse_id}")
def get_warehouse(warehouse_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM stk_warehouses WHERE id=%s AND company_id=%s", (warehouse_id, company_id))
            warehouse = cur.fetchone()
    if not warehouse:
        raise HTTPException(status_code=404, detail="Armazém não encontrado")
    return warehouse


@app.put("/warehouses/{warehouse_id}")
def update_warehouse(warehouse_id: int, body: WarehouseUpdate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE stk_warehouses SET name=%s, code=%s, type=%s, address=%s, active=%s
                WHERE id=%s AND company_id=%s RETURNING *
            """, (body.name, body.code, body.type, body.address, body.active, warehouse_id, company_id))
            warehouse = cur.fetchone()
            conn.commit()
    if not warehouse:
        raise HTTPException(status_code=404, detail="Armazém não encontrado")
    return warehouse


@app.delete("/warehouses/{warehouse_id}", status_code=204)
def delete_warehouse(warehouse_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM stk_warehouses WHERE id=%s AND company_id=%s", (warehouse_id, company_id))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Armazém não encontrado")
            cur.execute("""
                SELECT 1 FROM stk_item_stock WHERE warehouse_id=%s AND company_id=%s AND quantity > 0 LIMIT 1
            """, (warehouse_id, company_id))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Não é possível apagar: ainda há stock neste armazém. Desactive-o em vez disso.")
            cur.execute("DELETE FROM stk_warehouses WHERE id=%s AND company_id=%s", (warehouse_id, company_id))
            conn.commit()


# -------------------------
# ARTIGOS
# -------------------------

@app.get("/items")
def list_items(
    q: str | None = None,
    barcode: str | None = None,
    category: str | None = None,
    active: bool | None = None,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            clauses, params = ["company_id=%s"], [company_id]
            if q:
                clauses.append("(name ILIKE %s OR sku ILIKE %s OR barcode ILIKE %s)")
                params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
            if barcode:
                clauses.append("barcode=%s")
                params.append(barcode)
            if category:
                clauses.append("category=%s")
                params.append(category)
            if active is not None:
                clauses.append("active=%s")
                params.append(active)
            where = " AND ".join(clauses)
            cur.execute(f"SELECT * FROM stk_items WHERE {where} ORDER BY name", params)
            return cur.fetchall()


@app.post("/items", status_code=201)
def create_item(body: ItemCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO stk_items (sku, barcode, name, description, category, unit, min_stock, track_batches, company_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
            """, (body.sku, body.barcode, body.name, body.description, body.category,
                  body.unit, body.min_stock, body.track_batches, company_id))
            conn.commit()
            return cur.fetchone()


@app.get("/items/{item_id}")
def get_item(item_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM stk_items WHERE id=%s AND company_id=%s", (item_id, company_id))
            item = cur.fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="Artigo não encontrado")
    return item


@app.put("/items/{item_id}")
def update_item(item_id: int, body: ItemUpdate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE stk_items SET sku=%s, barcode=%s, name=%s, description=%s, category=%s,
                       unit=%s, min_stock=%s, track_batches=%s, active=%s
                WHERE id=%s AND company_id=%s RETURNING *
            """, (body.sku, body.barcode, body.name, body.description, body.category, body.unit,
                  body.min_stock, body.track_batches, body.active, item_id, company_id))
            item = cur.fetchone()
            conn.commit()
    if not item:
        raise HTTPException(status_code=404, detail="Artigo não encontrado")
    return item


@app.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM stk_items WHERE id=%s AND company_id=%s", (item_id, company_id))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Artigo não encontrado")
            cur.execute("SELECT 1 FROM stk_movements WHERE item_id=%s AND company_id=%s LIMIT 1", (item_id, company_id))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Não é possível apagar: já há movimentos para este artigo. Desactive-o em vez disso.")
            cur.execute("DELETE FROM stk_items WHERE id=%s AND company_id=%s", (item_id, company_id))
            conn.commit()


@app.get("/items/{item_id}/stock")
def get_item_stock(item_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM stk_items WHERE id=%s AND company_id=%s", (item_id, company_id))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Artigo não encontrado")
            cur.execute("""
                SELECT s.warehouse_id, w.name AS warehouse_name, s.quantity, s.avg_cost,
                       COALESCE(s.min_stock, (SELECT min_stock FROM stk_items WHERE id=%s)) AS min_stock
                FROM stk_item_stock s JOIN stk_warehouses w ON w.id = s.warehouse_id
                WHERE s.item_id=%s AND s.company_id=%s
                ORDER BY w.name
            """, (item_id, item_id, company_id))
            return cur.fetchall()


# -------------------------
# TAXAS DE CÂMBIO
# -------------------------

@app.get("/exchange-rates")
def list_exchange_rates(currency_code: str | None = None, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            clauses, params = ["company_id=%s"], [company_id]
            if currency_code:
                clauses.append("currency_code=%s")
                params.append(currency_code)
            where = " AND ".join(clauses)
            cur.execute(f"SELECT * FROM stk_exchange_rates WHERE {where} ORDER BY effective_date DESC", params)
            return cur.fetchall()


@app.post("/exchange-rates", status_code=201)
def create_exchange_rate(body: ExchangeRateCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    if body.currency_code == "AOA":
        raise HTTPException(status_code=422, detail="Não é preciso registar taxa de câmbio para AOA")
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO stk_exchange_rates (currency_code, rate_to_aoa, effective_date, company_id)
                VALUES (%s, %s, %s, %s) RETURNING *
            """, (body.currency_code.upper(), body.rate_to_aoa, body.effective_date or date.today().isoformat(), company_id))
            conn.commit()
            return cur.fetchone()


# -------------------------
# MOVIMENTOS DE STOCK
# -------------------------

@app.get("/movements")
def list_movements(
    item_id: int | None = None,
    warehouse_id: int | None = None,
    movement_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            clauses, params = ["m.company_id=%s"], [company_id]
            if item_id is not None:
                clauses.append("m.item_id=%s")
                params.append(item_id)
            if warehouse_id is not None:
                clauses.append("(m.warehouse_id=%s OR m.destination_warehouse_id=%s)")
                params.extend([warehouse_id, warehouse_id])
            if movement_type:
                clauses.append("m.movement_type=%s")
                params.append(movement_type)
            if date_from:
                clauses.append("m.created_at::date >= %s")
                params.append(date_from)
            if date_to:
                clauses.append("m.created_at::date <= %s")
                params.append(date_to)
            where = " AND ".join(clauses)
            cur.execute(f"""
                SELECT m.*, i.name AS item_name, i.sku AS item_sku, w.name AS warehouse_name
                FROM stk_movements m
                JOIN stk_items i ON i.id = m.item_id
                JOIN stk_warehouses w ON w.id = m.warehouse_id
                WHERE {where}
                ORDER BY m.created_at DESC, m.id DESC
            """, params)
            return cur.fetchall()


@app.post("/movements", status_code=201)
def create_movement(
    body: MovementCreate,
    token: str = Depends(oauth2_scheme),
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    if body.movement_type not in VALID_MOVEMENT_TYPES:
        raise HTTPException(status_code=422, detail="movement_type inválido")

    # Idempotência: um cliente offline (POS/terminal) pode reenviar o mesmo
    # movimento em segurança — devolve o registo já criado em vez de duplicar.
    if body.client_ref:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM stk_movements WHERE client_ref=%s AND company_id=%s",
                    (body.client_ref, company_id),
                )
                existing = cur.fetchone()
        if existing:
            return existing

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM stk_items WHERE id=%s AND company_id=%s", (body.item_id, company_id))
            item = cur.fetchone()
            if not item:
                raise HTTPException(status_code=404, detail="Artigo não encontrado")
            cur.execute("SELECT id FROM stk_warehouses WHERE id=%s AND company_id=%s", (body.warehouse_id, company_id))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Armazém não encontrado")

            # A taxa de câmbio só é relevante quando o movimento recebe um custo próprio
            # (entrada/devolução/ajuste positivo) — pedir uma taxa registada para saída/
            # transferência/ajuste negativo, que nunca a usam, seria um 422 sem sentido.
            rate = 1.0
            unit_cost_aoa = None
            total_cost_aoa = None

            if body.movement_type in ("entrada", "devolucao"):
                if body.quantity <= 0:
                    raise HTTPException(status_code=422, detail="Quantidade tem de ser positiva")
                if body.movement_type == "entrada" and body.unit_cost is None:
                    raise HTTPException(status_code=422, detail="unit_cost é obrigatório numa entrada")
                rate = get_exchange_rate(cur, body.currency_code, company_id)
                unit_cost = body.unit_cost
                if unit_cost is None:
                    stock_row = get_or_create_stock_row(cur, item["id"], body.warehouse_id, company_id)
                    unit_cost = float(stock_row["avg_cost"])
                unit_cost_aoa = round(unit_cost * rate, 4)
                apply_entrada(cur, item, body.warehouse_id, body.quantity, unit_cost_aoa, company_id)
                total_cost_aoa = round(unit_cost_aoa * body.quantity, 2)
                if item["track_batches"]:
                    cur.execute("""
                        INSERT INTO stk_batches
                            (item_id, warehouse_id, batch_number, expiry_date, quantity_received,
                             quantity_remaining, unit_cost, company_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (item["id"], body.warehouse_id, body.batch_number, body.expiry_date,
                          body.quantity, body.quantity, unit_cost_aoa, company_id))

            elif body.movement_type == "saida":
                if body.quantity <= 0:
                    raise HTTPException(status_code=422, detail="Quantidade tem de ser positiva")
                unit_cost_used, total_cost = apply_saida(cur, item, body.warehouse_id, body.quantity, company_id)
                unit_cost_aoa = round(unit_cost_used, 4)
                total_cost_aoa = round(total_cost, 2)

            elif body.movement_type == "transferencia":
                if not body.destination_warehouse_id:
                    raise HTTPException(status_code=422, detail="destination_warehouse_id é obrigatório numa transferência")
                if body.destination_warehouse_id == body.warehouse_id:
                    raise HTTPException(status_code=422, detail="Armazém de destino tem de ser diferente da origem")
                if body.quantity <= 0:
                    raise HTTPException(status_code=422, detail="Quantidade tem de ser positiva")
                cur.execute("SELECT id FROM stk_warehouses WHERE id=%s AND company_id=%s",
                            (body.destination_warehouse_id, company_id))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Armazém de destino não encontrado")
                unit_cost_used, total_cost = apply_saida(cur, item, body.warehouse_id, body.quantity, company_id)
                apply_entrada(cur, item, body.destination_warehouse_id, body.quantity, unit_cost_used, company_id)
                unit_cost_aoa = round(unit_cost_used, 4)
                total_cost_aoa = round(total_cost, 2)

            elif body.movement_type == "ajuste":
                if body.quantity == 0:
                    raise HTTPException(status_code=422, detail="Quantidade do ajuste não pode ser zero")
                if body.quantity > 0:
                    rate = get_exchange_rate(cur, body.currency_code, company_id)
                    unit_cost = body.unit_cost
                    if unit_cost is None:
                        stock_row = get_or_create_stock_row(cur, item["id"], body.warehouse_id, company_id)
                        unit_cost = float(stock_row["avg_cost"])
                    unit_cost_aoa = round(unit_cost * rate, 4)
                    apply_entrada(cur, item, body.warehouse_id, body.quantity, unit_cost_aoa, company_id)
                    total_cost_aoa = round(unit_cost_aoa * body.quantity, 2)
                else:
                    unit_cost_used, total_cost = apply_saida(cur, item, body.warehouse_id, -body.quantity, company_id)
                    unit_cost_aoa = round(unit_cost_used, 4)
                    total_cost_aoa = round(total_cost, 2)

            try:
                cur.execute("""
                    INSERT INTO stk_movements
                        (movement_type, item_id, warehouse_id, destination_warehouse_id, quantity, currency_code,
                         exchange_rate, unit_cost, unit_cost_aoa, total_cost_aoa, sale_price, batch_number,
                         expiry_date, reason, reference_doc_type, reference_doc_id, client_ref, created_by, company_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING *
                """, (
                    body.movement_type, item["id"], body.warehouse_id, body.destination_warehouse_id, body.quantity,
                    body.currency_code, rate, body.unit_cost, unit_cost_aoa, total_cost_aoa, body.sale_price,
                    body.batch_number, body.expiry_date, body.reason, body.reference_doc_type,
                    body.reference_doc_id, body.client_ref, user, company_id,
                ))
            except psycopg2.errors.UniqueViolation:
                # Corrida entre dois pedidos com o mesmo client_ref: devolve o que já ficou gravado.
                conn.rollback()
                with conn.cursor(cursor_factory=RealDictCursor) as cur2:
                    cur2.execute(
                        "SELECT * FROM stk_movements WHERE client_ref=%s AND company_id=%s",
                        (body.client_ref, company_id),
                    )
                    return cur2.fetchone()

            movement = cur.fetchone()
            conn.commit()

    _post_movement_journal_entry(token, item, body.movement_type, body.quantity, total_cost_aoa, movement["id"])
    return movement


def _post_movement_journal_entry(token, item, movement_type, quantity, total_cost_aoa, movement_id):
    """Contrapartida contabilística em partidas dobradas para movimentos com valor.
    Melhor-esforço (ver core/accounting_client.py) — nunca faz falhar o movimento
    de stock já confirmado."""
    if not total_cost_aoa:
        return
    amount = round(total_cost_aoa, 2)
    desc = f"Movimento de stock ({movement_type}) — {item['name']}"

    if movement_type == "entrada":
        lines = [{"account_code": STOCK_ACCOUNT_CODE, "debit": amount, "credit": 0},
                 {"account_code": SUPPLIERS_ACCOUNT_CODE, "debit": 0, "credit": amount}]
    elif movement_type == "saida":
        lines = [{"account_code": CMV_ACCOUNT_CODE, "debit": amount, "credit": 0},
                 {"account_code": STOCK_ACCOUNT_CODE, "debit": 0, "credit": amount}]
    elif movement_type == "devolucao" or (movement_type == "ajuste" and quantity > 0):
        lines = [{"account_code": STOCK_ACCOUNT_CODE, "debit": amount, "credit": 0},
                 {"account_code": CMV_ACCOUNT_CODE, "debit": 0, "credit": amount}]
    elif movement_type == "ajuste" and quantity < 0:
        lines = [{"account_code": CMV_ACCOUNT_CODE, "debit": amount, "credit": 0},
                 {"account_code": STOCK_ACCOUNT_CODE, "debit": 0, "credit": amount}]
    else:
        return  # transferência entre armazéns não tem impacto contabilístico líquido

    post_journal_entry(token, date.today().isoformat(), desc, lines, f"stock_{movement_type}", movement_id)


# -------------------------
# LOTES
# -------------------------

@app.get("/batches")
def list_batches(
    item_id: int | None = None,
    warehouse_id: int | None = None,
    expiring_within_days: int | None = None,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            clauses, params = ["b.company_id=%s", "b.quantity_remaining > 0"], [company_id]
            if item_id is not None:
                clauses.append("b.item_id=%s")
                params.append(item_id)
            if warehouse_id is not None:
                clauses.append("b.warehouse_id=%s")
                params.append(warehouse_id)
            if expiring_within_days is not None:
                clauses.append("b.expiry_date IS NOT NULL AND b.expiry_date <= CURRENT_DATE + %s * INTERVAL '1 day'")
                params.append(expiring_within_days)
            where = " AND ".join(clauses)
            cur.execute(f"""
                SELECT b.*, i.name AS item_name, i.sku AS item_sku, w.name AS warehouse_name
                FROM stk_batches b
                JOIN stk_items i ON i.id = b.item_id
                JOIN stk_warehouses w ON w.id = b.warehouse_id
                WHERE {where}
                ORDER BY b.expiry_date ASC NULLS LAST, b.received_at ASC
            """, params)
            return cur.fetchall()


# -------------------------
# ALERTAS DE STOCK MÍNIMO / ROTURA
# -------------------------

@app.get("/stock/alerts")
def stock_alerts(user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT s.item_id, i.name AS item_name, i.sku, i.barcode, s.warehouse_id,
                       w.name AS warehouse_name, s.quantity,
                       COALESCE(s.min_stock, i.min_stock) AS min_stock
                FROM stk_item_stock s
                JOIN stk_items i ON i.id = s.item_id
                JOIN stk_warehouses w ON w.id = s.warehouse_id
                WHERE s.company_id=%s AND i.active=true
                      AND s.quantity <= COALESCE(s.min_stock, i.min_stock)
                ORDER BY s.quantity ASC
            """, (company_id,))
            return cur.fetchall()


# -------------------------
# GUIAS DE TRANSPORTE
# -------------------------

@app.get("/transport-guides")
def list_transport_guides(
    status: str | None = None,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            clauses, params = ["company_id=%s"], [company_id]
            if status:
                clauses.append("status=%s")
                params.append(status)
            where = " AND ".join(clauses)
            cur.execute(f"SELECT * FROM stk_transport_guides WHERE {where} ORDER BY created_at DESC", params)
            return cur.fetchall()


def _get_guide_detail(cur, guide_id: int, company_id: int) -> dict:
    cur.execute("SELECT * FROM stk_transport_guides WHERE id=%s AND company_id=%s", (guide_id, company_id))
    guide = cur.fetchone()
    if not guide:
        raise HTTPException(status_code=404, detail="Guia de transporte não encontrada")
    cur.execute("""
        SELECT l.*, i.name AS item_name, i.sku AS item_sku
        FROM stk_transport_guide_lines l JOIN stk_items i ON i.id = l.item_id
        WHERE l.guide_id=%s ORDER BY l.id
    """, (guide_id,))
    guide["lines"] = cur.fetchall()
    return guide


@app.post("/transport-guides", status_code=201)
def create_transport_guide(
    body: TransportGuideCreate,
    token: str = Depends(oauth2_scheme),
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    if not body.lines:
        raise HTTPException(status_code=422, detail="A guia precisa de pelo menos uma linha")

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM stk_warehouses WHERE id=%s AND company_id=%s",
                        (body.origin_warehouse_id, company_id))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Armazém de origem não encontrado")

            doc_number = next_doc_number(cur, "guia")
            cur.execute("""
                INSERT INTO stk_transport_guides
                    (doc_number, origin_warehouse_id, destination_warehouse_id, destination_name, destination_nif,
                     destination_address, transporter_name, vehicle_plate, issue_date, status, notes, created_by, company_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'emitida', %s, %s, %s)
                RETURNING *
            """, (
                doc_number, body.origin_warehouse_id, body.destination_warehouse_id, body.destination_name,
                body.destination_nif, body.destination_address, body.transporter_name, body.vehicle_plate,
                body.issue_date or date.today().isoformat(), body.notes, user, company_id,
            ))
            guide = cur.fetchone()

            guide_total_cost = 0.0
            for line in body.lines:
                cur.execute("SELECT * FROM stk_items WHERE id=%s AND company_id=%s", (line.item_id, company_id))
                item = cur.fetchone()
                if not item:
                    raise HTTPException(status_code=404, detail=f"Artigo {line.item_id} não encontrado")
                # A guia acompanha a saída física do stock do armazém de origem —
                # o movimento correspondente fica registado ligado a esta guia.
                unit_cost_used, total_cost = apply_saida(cur, item, body.origin_warehouse_id, line.quantity, company_id)
                guide_total_cost += total_cost
                cur.execute("""
                    INSERT INTO stk_movements
                        (movement_type, item_id, warehouse_id, destination_warehouse_id, quantity, currency_code,
                         exchange_rate, unit_cost_aoa, total_cost_aoa, reason, reference_doc_type, reference_doc_id,
                         created_by, company_id)
                    VALUES ('saida', %s, %s, %s, %s, 'AOA', 1, %s, %s, 'guia_transporte', 'guide', %s, %s, %s)
                """, (item["id"], body.origin_warehouse_id, body.destination_warehouse_id, line.quantity,
                      round(unit_cost_used, 4), round(total_cost, 2), guide["id"], user, company_id))
                cur.execute("""
                    INSERT INTO stk_transport_guide_lines (guide_id, item_id, quantity, batch_id, company_id)
                    VALUES (%s, %s, %s, %s, %s)
                """, (guide["id"], line.item_id, line.quantity, line.batch_id, company_id))

            conn.commit()
            detail = _get_guide_detail(cur, guide["id"], company_id)

    if guide_total_cost:
        amount = round(guide_total_cost, 2)
        post_journal_entry(
            token, date.today().isoformat(), f"Guia de transporte {doc_number} — saída de stock",
            [{"account_code": CMV_ACCOUNT_CODE, "debit": amount, "credit": 0},
             {"account_code": STOCK_ACCOUNT_CODE, "debit": 0, "credit": amount}],
            "stock_guia_transporte", guide["id"],
        )
    return detail


@app.get("/transport-guides/{guide_id}")
def get_transport_guide(guide_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            return _get_guide_detail(cur, guide_id, company_id)


@app.patch("/transport-guides/{guide_id}/validate")
def validate_transport_guide(
    guide_id: int, body: TransportGuideValidate,
    user=Depends(get_current_user), company_id: int = Depends(get_current_company),
):
    """Regista o resultado da validação/certificação junto da AGT (feita
    internamente pela empresa) — este endpoint só grava os códigos devolvidos,
    não faz nenhuma submissão nem assinatura."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE stk_transport_guides
                SET status='validada_agt', atcud=COALESCE(%s, atcud),
                    agt_validation_code=%s, agt_hash=%s
                WHERE id=%s AND company_id=%s AND status != 'anulada' RETURNING *
            """, (body.atcud, body.agt_validation_code, body.agt_hash, guide_id, company_id))
            guide = cur.fetchone()
            conn.commit()
    if not guide:
        raise HTTPException(status_code=404, detail="Guia não encontrada ou já anulada")
    return guide


@app.patch("/transport-guides/{guide_id}/cancel")
def cancel_transport_guide(guide_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE stk_transport_guides SET status='anulada'
                WHERE id=%s AND company_id=%s AND status != 'anulada' RETURNING *
            """, (guide_id, company_id))
            guide = cur.fetchone()
            conn.commit()
    if not guide:
        raise HTTPException(status_code=404, detail="Guia não encontrada ou já anulada")
    return guide


# -------------------------
# DOCUMENTOS (ANEXOS DE GUIAS DE TRANSPORTE)
# -------------------------

@app.post("/transport-guides/{guide_id}/documents", status_code=201)
def upload_guide_document(
    guide_id: int,
    document_type: str,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            assert_entity_exists(cur, "guide", guide_id, company_id)
            object_name = upload_file(file, "guide", guide_id)
            cur.execute("""
                INSERT INTO stk_documents
                    (entity_type, entity_id, document_type, filename, object_name, content_type, uploaded_by, company_id)
                VALUES ('guide', %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (guide_id, document_type, file.filename, object_name, file.content_type, user, company_id))
            conn.commit()
            return cur.fetchone()


@app.get("/transport-guides/{guide_id}/documents")
def list_guide_documents(guide_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM stk_documents WHERE entity_type='guide' AND entity_id=%s AND company_id=%s
                ORDER BY created_at DESC
            """, (guide_id, company_id))
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
            cur.execute(f"SELECT * FROM stk_documents WHERE {where} ORDER BY created_at DESC", params)
            return cur.fetchall()


@app.get("/documents/{document_id}/presigned-url")
def get_document_url(document_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT object_name FROM stk_documents WHERE id=%s AND company_id=%s", (document_id, company_id))
            doc = cur.fetchone()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return {"url": get_file_url(doc["object_name"])}


@app.delete("/documents/{document_id}")
def delete_document(document_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT object_name FROM stk_documents WHERE id=%s AND company_id=%s", (document_id, company_id))
            doc = cur.fetchone()
            if not doc:
                raise HTTPException(status_code=404, detail="Documento não encontrado")
            try:
                delete_object(doc["object_name"])
            except Exception:
                raise HTTPException(500, "Erro ao apagar ficheiro no storage")
            cur.execute("DELETE FROM stk_documents WHERE id=%s AND company_id=%s", (document_id, company_id))
            conn.commit()
    return {"message": "Documento removido com sucesso"}


# -------------------------
# RELATÓRIOS
# -------------------------

@app.get("/reports/turnover")
def report_turnover(date_from: str, date_to: str, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT m.item_id, i.name AS item_name, i.sku,
                       SUM(m.quantity) AS quantity_saida, SUM(m.total_cost_aoa) AS custo_total
                FROM stk_movements m JOIN stk_items i ON i.id = m.item_id
                WHERE m.company_id=%s AND m.movement_type='saida'
                      AND m.created_at::date BETWEEN %s AND %s
                GROUP BY m.item_id, i.name, i.sku
                ORDER BY quantity_saida DESC
            """, (company_id, date_from, date_to))
            rows = cur.fetchall()
    for r in rows:
        r["quantity_saida"] = float(r["quantity_saida"])
        r["custo_total"] = float(r["custo_total"] or 0)
    return rows


@app.get("/reports/margins")
def report_margins(date_from: str, date_to: str, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT m.item_id, i.name AS item_name, i.sku,
                       SUM(m.quantity) AS quantity_vendida,
                       SUM(m.total_cost_aoa) AS custo_total,
                       SUM(m.sale_price * m.quantity) AS receita_total
                FROM stk_movements m JOIN stk_items i ON i.id = m.item_id
                WHERE m.company_id=%s AND m.movement_type='saida' AND m.sale_price IS NOT NULL
                      AND m.created_at::date BETWEEN %s AND %s
                GROUP BY m.item_id, i.name, i.sku
                ORDER BY item_name
            """, (company_id, date_from, date_to))
            rows = cur.fetchall()
    for r in rows:
        r["quantity_vendida"] = float(r["quantity_vendida"])
        r["custo_total"] = float(r["custo_total"] or 0)
        r["receita_total"] = float(r["receita_total"] or 0)
        r["margem"] = round(r["receita_total"] - r["custo_total"], 2)
        r["margem_percentual"] = round((r["margem"] / r["receita_total"] * 100), 2) if r["receita_total"] else 0
    return rows


# -------------------------
# RESUMO (DASHBOARD)
# -------------------------

@app.get("/summary")
def summary(user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS n FROM stk_items WHERE company_id=%s AND active=true", (company_id,))
            total_items = int(cur.fetchone()["n"])

            cur.execute("SELECT COUNT(*) AS n FROM stk_warehouses WHERE company_id=%s AND active=true", (company_id,))
            total_warehouses = int(cur.fetchone()["n"])

            cur.execute("""
                SELECT COALESCE(SUM(quantity * avg_cost), 0) AS total FROM stk_item_stock WHERE company_id=%s
            """, (company_id,))
            stock_value_aoa = float(cur.fetchone()["total"])

            cur.execute("""
                SELECT COUNT(*) AS n FROM stk_item_stock s JOIN stk_items i ON i.id = s.item_id
                WHERE s.company_id=%s AND i.active=true AND s.quantity <= COALESCE(s.min_stock, i.min_stock)
            """, (company_id,))
            alerts_count = int(cur.fetchone()["n"])

            cur.execute("""
                SELECT COUNT(*) AS n FROM stk_transport_guides WHERE company_id=%s AND status='emitida'
            """, (company_id,))
            pending_guides = int(cur.fetchone()["n"])

    return {
        "total_items": total_items,
        "total_warehouses": total_warehouses,
        "stock_value_aoa": round(stock_value_aoa, 2),
        "alerts_count": alerts_count,
        "pending_guides": pending_guides,
    }


# -------------------------
# EXPORTAÇÃO SAF-T (AO)
# -------------------------
#
# NOTA: esta exportação segue a estrutura geral de um ficheiro SAF-T (Header,
# MasterFiles, MovementOfGoods) mas NÃO foi validada contra o XSD oficial da
# AGT. A validação/certificação e a submissão real ao portal da AGT ficam a
# cargo da equipa interna responsável por essa integração.

@app.get("/saft/export")
def saft_export(date_from: str, date_to: str, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM stk_items WHERE company_id=%s ORDER BY id", (company_id,))
            items = cur.fetchall()
            cur.execute("""
                SELECT m.*, i.sku AS item_sku, w.code AS warehouse_code
                FROM stk_movements m
                JOIN stk_items i ON i.id = m.item_id
                JOIN stk_warehouses w ON w.id = m.warehouse_id
                WHERE m.company_id=%s AND m.created_at::date BETWEEN %s AND %s
                ORDER BY m.created_at
            """, (company_id, date_from, date_to))
            movements = cur.fetchall()

    root = ET.Element("AuditFile")
    header = ET.SubElement(root, "Header")
    ET.SubElement(header, "CompanyID").text = str(company_id)
    ET.SubElement(header, "StartDate").text = date_from
    ET.SubElement(header, "EndDate").text = date_to
    ET.SubElement(header, "Currency").text = "AOA"

    master_files = ET.SubElement(root, "MasterFiles")
    product_table = ET.SubElement(master_files, "ProductTable")
    for item in items:
        product = ET.SubElement(product_table, "Product")
        ET.SubElement(product, "ProductCode").text = item["sku"] or str(item["id"])
        ET.SubElement(product, "ProductDescription").text = item["name"]
        ET.SubElement(product, "ProductNumberCode").text = item["barcode"] or ""

    movement_of_goods = ET.SubElement(root, "MovementOfGoods")
    for m in movements:
        doc = ET.SubElement(movement_of_goods, "StockMovement")
        ET.SubElement(doc, "MovementType").text = m["movement_type"]
        ET.SubElement(doc, "MovementDate").text = m["created_at"].date().isoformat()
        ET.SubElement(doc, "ProductCode").text = m["item_sku"] or str(m["item_id"])
        ET.SubElement(doc, "WarehouseCode").text = m["warehouse_code"] or str(m["warehouse_id"])
        ET.SubElement(doc, "Quantity").text = str(m["quantity"])
        ET.SubElement(doc, "UnitPrice").text = str(m["unit_cost_aoa"] or 0)

    xml_bytes = minidom.parseString(ET.tostring(root, encoding="unicode")).toprettyxml(indent="  ").encode("utf-8")
    return Response(content=xml_bytes, media_type="application/xml")
