import os
import uuid
from datetime import date, datetime

import httpx
#from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
#from fastapi.middleware.cors import CORSMiddleware
#from psycopg2.extras import RealDictCursor
#from pydantic import BaseModel
#from jose import jwt, JWTError
#from fastapi.security import OAuth2PasswordBearer
#
#from core.db import get_connection, init_db
#from core.storage import upload_bytes, ensure_bucket, delete_object
#from core.url_service import get_presigned_url
#from core.accounting_client import post_journal_entry
#
#app = FastAPI(title="Finance Service (Books)", version="1.0.0")
#
#app.add_middleware(
#    CORSMiddleware,
#    allow_origins=os.environ.get(
#        "CORS_ORIGINS",
#        "http://localhost,http://localhost:80,http://127.0.0.1,http://localhost:5002",
#    ).split(","),
#    allow_credentials=True,
#    allow_methods=["*"],
#    allow_headers=["*"],
#)
#
#JWT_SECRET = os.environ["JWT_SECRET"]
#JWT_ALGORITHM = "HS256"
#RH_SERVICE_URL = os.environ.get("RH_SERVICE_URL", "http://ms_rh:5003")
#
#oauth2_scheme = OAuth2PasswordBearer(
#    tokenUrl="http://ms_auth:5000/login"
#)
#
#
## -------------------------
## STARTUP
## -------------------------
#
#@app.on_event("startup")
#def startup():
#    init_db()
#    ensure_bucket()
#
#
## -------------------------
## AUTH
## -------------------------
#
#def get_current_claims(token: str = Depends(oauth2_scheme)) -> dict:
#    try:
#        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
#    except JWTError:
#        raise HTTPException(status_code=401, detail="Token inválido")
#
#    if payload.get("type") != "access":
#        raise HTTPException(status_code=401, detail="Token inválido")
#
#    # Sem esta verificação, um token revogado por logout continuava válido aqui
#    # até expirar (15 min) — os outros serviços já verificam a blocklist.
#    jti = payload.get("jti")
#    if jti:
#        with get_connection() as conn:
#            with conn.cursor() as cur:
#                cur.execute(
#                    "SELECT jti FROM jwt_blocklist WHERE jti = %s AND expires_at > NOW()",
#                    (jti,),
#                )
#                if cur.fetchone():
#                    raise HTTPException(status_code=401, detail="Token revogado")
#
#    return payload
#
#
#def get_current_user(claims: dict = Depends(get_current_claims)) -> str:
#    return claims["sub"]
#
#
#def get_current_company(claims: dict = Depends(get_current_claims)) -> int:
#    """Isolamento entre empresas: toda a leitura/escrita de dados financeiros tem
#    de ser filtrada por company_id — nunca basta um JWT válido, seja lá de que
#    empresa for."""
#    company_id = claims.get("company_id")
#    if company_id is None:
#        raise HTTPException(status_code=403, detail="Utilizador sem empresa associada")
#    return company_id
#
#
## -------------------------
## UPLOAD DE FICHEIROS
## -------------------------
#
#ENTITY_TABLE = {"invoice": "fin_invoices", "expense": "fin_expenses"}
#
#
#def upload_file(file: UploadFile, entity_type: str, entity_id: int) -> str:
#    ext = file.filename.split(".")[-1]
#    object_name = f"{entity_type}/{entity_id}/{uuid.uuid4()}.{ext}"
#    data = file.file.read()
#    upload_bytes(data, object_name, file.content_type or "application/octet-stream")
#    return object_name
#
#
#def get_file_url(object_name: str) -> str:
#    return get_presigned_url(object_name)
#
#
#def assert_entity_exists(cur, entity_type: str, entity_id: int, company_id: int):
#    table = ENTITY_TABLE.get(entity_type)
#    if not table:
#        raise HTTPException(status_code=400, detail="Tipo de entidade inválido")
#    cur.execute(f"SELECT id FROM {table} WHERE id=%s AND company_id=%s", (entity_id, company_id))
#    if not cur.fetchone():
#        raise HTTPException(status_code=404, detail="Registo não encontrado")
#
#
## -------------------------
## NUMERAÇÃO SEQUENCIAL IMUTÁVEL
## -------------------------
#
#DOC_PREFIX = {"fatura": "FAT", "recibo": "REC"}
#
#
#def next_doc_number(cur, doc_type: str) -> str:
#    cur.execute(
#        "INSERT INTO fin_counters (doc_type, next_seq) VALUES (%s, 1) ON CONFLICT DO NOTHING",
#        (doc_type,),
#    )
#    cur.execute(
#        "SELECT next_seq FROM fin_counters WHERE doc_type=%s FOR UPDATE",
#        (doc_type,),
#    )
#    seq = cur.fetchone()["next_seq"]
#    cur.execute(
#        "UPDATE fin_counters SET next_seq = next_seq + 1 WHERE doc_type=%s",
#        (doc_type,),
#    )
#    return f"{DOC_PREFIX[doc_type]}-{seq:06d}"
#
#
## -------------------------
## MODELS
## -------------------------
#
#class InvoiceCreate(BaseModel):
#    client_name: str
#    client_nif: str | None = None
#    client_email: str | None = None
#    client_contact_id: int | None = None
#    description: str | None = None
#    subtotal: float
#    iva_rate: float = 14
#    issue_date: str | None = None
#    due_date: str | None = None
#    notes: str | None = None
#
#
#class ReceiptCreate(BaseModel):
#    amount: float
#    payment_date: str | None = None
#    payment_method: str = "transferencia"
#
#
#class ExpenseCreate(BaseModel):
#    description: str
#    supplier_id: int | None = None
#    supplier_name: str | None = None
#    category: str | None = None
#    department_id: int | None = None
#    requested_by: int | None = None
#    amount: float
#    due_date: str | None = None
#
#
#class ExpenseDecision(BaseModel):
#    approval_note: str | None = None
#
#
#class SupplierCreate(BaseModel):
#    name: str
#    nif: str | None = None
#    email: str | None = None
#    phone: str | None = None
#    category: str | None = None
#    address: str | None = None
#    notes: str | None = None
#
#
#class SupplierUpdate(SupplierCreate):
#    active: bool = True
#
#
## -------------------------
## FACTURAS
## -------------------------
#
#@app.get("/invoices")
#def list_invoices(status: str | None = None, client: str | None = None,
#                   user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            clauses, params = ["company_id=%s"], [company_id]
#            if status:
#                clauses.append("status=%s")
#                params.append(status)
#            if client:
#                clauses.append("client_name ILIKE %s")
#                params.append(f"%{client}%")
#            where = f"WHERE {' AND '.join(clauses)}"
#            cur.execute(f"SELECT * FROM fin_invoices {where} ORDER BY created_at DESC", params)
#            return cur.fetchall()
#
#
#@app.post("/invoices", status_code=201)
#def create_invoice(body: InvoiceCreate, token: str = Depends(oauth2_scheme), user=Depends(get_current_user),
#                    company_id: int = Depends(get_current_company)):
#    subtotal = round(body.subtotal, 2)
#    iva_amount = round(subtotal * body.iva_rate / 100, 2)
#    total = round(subtotal + iva_amount, 2)
#    issue_date = body.issue_date or date.today().isoformat()
#
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            doc_number = next_doc_number(cur, "fatura")
#            cur.execute("""
#                INSERT INTO fin_invoices
#                    (doc_number, client_name, client_nif, client_email, client_contact_id,
#                     description, subtotal, iva_rate, iva_amount, total, issue_date, due_date, notes, company_id)
#                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#                RETURNING *
#            """, (
#                doc_number, body.client_name, body.client_nif, body.client_email, body.client_contact_id,
#                body.description, subtotal, body.iva_rate, iva_amount, total,
#                issue_date, body.due_date, body.notes, company_id,
#            ))
#            conn.commit()
#            invoice = cur.fetchone()
#
#    lines = [{"account_code": "1.1.3", "debit": total, "credit": 0}]
#    if subtotal:
#        lines.append({"account_code": "4.1", "debit": 0, "credit": subtotal})
#    if iva_amount:
#        lines.append({"account_code": "2.1.2", "debit": 0, "credit": iva_amount})
#    post_journal_entry(
#        token, issue_date, f"Factura {doc_number} — {body.client_name}",
#        lines, "invoice", invoice["id"],
#    )
#    return invoice
#
#
#@app.get("/invoices/{invoice_id}")
#def get_invoice(invoice_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("SELECT * FROM fin_invoices WHERE id=%s AND company_id=%s", (invoice_id, company_id))
#            invoice = cur.fetchone()
#    if not invoice:
#        raise HTTPException(status_code=404, detail="Factura não encontrada")
#    return invoice
#
#
## NOTA: anular uma factura aqui não estorna automaticamente o lançamento
## contabilístico criado em create_invoice (accounting-service) — isso exigiria
## o accounting-service localizar a entrada por source_type/source_id antes de
## anular, com semântica de falha pouco clara para uma operação de anulação.
## Em v1, um utilizador tem de reverter manualmente o lançamento em Contabilidade.
#@app.patch("/invoices/{invoice_id}/cancel")
#def cancel_invoice(invoice_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("SELECT id FROM fin_invoices WHERE id=%s AND company_id=%s", (invoice_id, company_id))
#            if not cur.fetchone():
#                raise HTTPException(status_code=404, detail="Factura não encontrada")
#            cur.execute("SELECT id FROM fin_receipts WHERE invoice_id=%s LIMIT 1", (invoice_id,))
#            if cur.fetchone():
#                raise HTTPException(status_code=409, detail="Não é possível anular: já existem recibos emitidos")
#            cur.execute("""
#                UPDATE fin_invoices SET status='anulada' WHERE id=%s AND company_id=%s RETURNING *
#            """, (invoice_id, company_id))
#            invoice = cur.fetchone()
#            conn.commit()
#    return invoice
#
#
#@app.delete("/invoices/{invoice_id}", status_code=204)
#def delete_invoice(invoice_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("SELECT id FROM fin_invoices WHERE id=%s AND company_id=%s", (invoice_id, company_id))
#            if not cur.fetchone():
#                raise HTTPException(status_code=404, detail="Factura não encontrada")
#            cur.execute("SELECT id FROM fin_receipts WHERE invoice_id=%s LIMIT 1", (invoice_id,))
#            if cur.fetchone():
#                raise HTTPException(status_code=409, detail="Não é possível apagar: já existem recibos emitidos")
#            cur.execute("DELETE FROM fin_invoices WHERE id=%s AND company_id=%s", (invoice_id, company_id))
#            conn.commit()
#
#
## -------------------------
## RECIBOS
## -------------------------
#
#@app.post("/invoices/{invoice_id}/receipts", status_code=201)
#def create_receipt(invoice_id: int, body: ReceiptCreate, token: str = Depends(oauth2_scheme),
#                    user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    payment_date = body.payment_date or date.today().isoformat()
#
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("SELECT * FROM fin_invoices WHERE id=%s AND company_id=%s FOR UPDATE", (invoice_id, company_id))
#            invoice = cur.fetchone()
#            if not invoice:
#                raise HTTPException(status_code=404, detail="Factura não encontrada")
#            if invoice["status"] == "anulada":
#                raise HTTPException(status_code=409, detail="Factura anulada não pode receber recibos")
#
#            doc_number = next_doc_number(cur, "recibo")
#            cur.execute("""
#                INSERT INTO fin_receipts (doc_number, invoice_id, amount, payment_date, payment_method, company_id)
#                VALUES (%s, %s, %s, %s, %s, %s)
#                RETURNING *
#            """, (
#                doc_number, invoice_id, round(body.amount, 2),
#                payment_date, body.payment_method, company_id,
#            ))
#            receipt = cur.fetchone()
#
#            new_paid = round(float(invoice["paid_amount"]) + body.amount, 2)
#            new_status = "paga" if new_paid >= float(invoice["total"]) else "parcial"
#            cur.execute("""
#                UPDATE fin_invoices SET paid_amount=%s, status=%s WHERE id=%s AND company_id=%s
#            """, (new_paid, new_status, invoice_id, company_id))
#
#            conn.commit()
#
#    amount = round(body.amount, 2)
#    post_journal_entry(
#        token, payment_date, f"Recibo {doc_number} — Factura {invoice['doc_number']}",
#        [
#            {"account_code": "1.1.2", "debit": amount, "credit": 0},
#            {"account_code": "1.1.3", "debit": 0, "credit": amount},
#        ],
#        "receipt", receipt["id"],
#    )
#    return receipt
#
#
#@app.get("/invoices/{invoice_id}/receipts")
#def list_invoice_receipts(invoice_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("SELECT id FROM fin_invoices WHERE id=%s AND company_id=%s", (invoice_id, company_id))
#            if not cur.fetchone():
#                raise HTTPException(status_code=404, detail="Factura não encontrada")
#            cur.execute("SELECT * FROM fin_receipts WHERE invoice_id=%s ORDER BY payment_date DESC", (invoice_id,))
#            return cur.fetchall()
#
#
#@app.get("/receipts")
#def list_receipts(date_from: str | None = None, date_to: str | None = None,
#                   user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            clauses, params = ["company_id=%s"], [company_id]
#            if date_from:
#                clauses.append("payment_date >= %s")
#                params.append(date_from)
#            if date_to:
#                clauses.append("payment_date <= %s")
#                params.append(date_to)
#            where = f"WHERE {' AND '.join(clauses)}"
#            cur.execute(f"SELECT * FROM fin_receipts {where} ORDER BY payment_date DESC", params)
#            return cur.fetchall()
#
#
## -------------------------
## DESPESAS (CONTAS A PAGAR + APROVAÇÃO HIERÁRQUICA)
## -------------------------
#
#def resolve_approver(employee_id: int, token: str) -> int | None:
#    if not employee_id:
#        return None
#    try:
#        with httpx.Client(timeout=3.0) as client:
#            res = client.get(
#                f"{RH_SERVICE_URL}/employees/{employee_id}",
#                headers={"Authorization": f"Bearer {token}"},
#            )
#        if res.status_code == 200:
#            return res.json().get("reports_to")
#    except httpx.HTTPError:
#        pass
#    return None
#
#
#@app.get("/expenses")
#def list_expenses(
#    status: str | None = None,
#    department_id: int | None = None,
#    requested_by: int | None = None,
#    user=Depends(get_current_user),
#    company_id: int = Depends(get_current_company),
#):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            clauses, params = ["company_id=%s"], [company_id]
#            if status:
#                clauses.append("status=%s")
#                params.append(status)
#            if department_id:
#                clauses.append("department_id=%s")
#                params.append(department_id)
#            if requested_by:
#                clauses.append("requested_by=%s")
#                params.append(requested_by)
#            where = f"WHERE {' AND '.join(clauses)}"
#            cur.execute(f"SELECT * FROM fin_expenses {where} ORDER BY created_at DESC", params)
#            return cur.fetchall()
#
#
#@app.post("/expenses", status_code=201)
#def create_expense(body: ExpenseCreate, token: str = Depends(oauth2_scheme), user=Depends(get_current_user),
#                    company_id: int = Depends(get_current_company)):
#    approver_id = resolve_approver(body.requested_by, token) if body.requested_by else None
#
#    supplier_name = body.supplier_name
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            if body.supplier_id:
#                cur.execute("SELECT name FROM fin_suppliers WHERE id=%s AND company_id=%s", (body.supplier_id, company_id))
#                supplier = cur.fetchone()
#                if not supplier:
#                    raise HTTPException(status_code=404, detail="Fornecedor não encontrado")
#                supplier_name = supplier["name"]
#
#            cur.execute("""
#                INSERT INTO fin_expenses
#                    (description, supplier_id, supplier_name, category, department_id, requested_by, approver_id,
#                     amount, due_date, company_id)
#                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#                RETURNING *
#            """, (
#                body.description, body.supplier_id, supplier_name, body.category, body.department_id,
#                body.requested_by, approver_id, round(body.amount, 2), body.due_date, company_id,
#            ))
#            conn.commit()
#            return cur.fetchone()
#
#
#@app.patch("/expenses/{expense_id}/approve")
#def approve_expense(expense_id: int, body: ExpenseDecision, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("""
#                UPDATE fin_expenses
#                SET status='aprovada', approval_note=%s, decided_at=NOW()
#                WHERE id=%s AND company_id=%s AND status='pendente' RETURNING *
#            """, (body.approval_note, expense_id, company_id))
#            expense = cur.fetchone()
#            conn.commit()
#    if not expense:
#        raise HTTPException(status_code=404, detail="Despesa não encontrada ou já decidida")
#    return expense
#
#
#@app.patch("/expenses/{expense_id}/reject")
#def reject_expense(expense_id: int, body: ExpenseDecision, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("""
#                UPDATE fin_expenses
#                SET status='rejeitada', approval_note=%s, decided_at=NOW()
#                WHERE id=%s AND company_id=%s AND status='pendente' RETURNING *
#            """, (body.approval_note, expense_id, company_id))
#            expense = cur.fetchone()
#            conn.commit()
#    if not expense:
#        raise HTTPException(status_code=404, detail="Despesa não encontrada ou já decidida")
#    return expense
#
#
#@app.patch("/expenses/{expense_id}/pay")
#def pay_expense(expense_id: int, token: str = Depends(oauth2_scheme), user=Depends(get_current_user),
#                 company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("""
#                UPDATE fin_expenses SET status='paga', paid_at=NOW()
#                WHERE id=%s AND company_id=%s AND status='aprovada' RETURNING *
#            """, (expense_id, company_id))
#            expense = cur.fetchone()
#            conn.commit()
#    if not expense:
#        raise HTTPException(status_code=404, detail="Despesa não encontrada ou ainda não aprovada")
#
#    amount = round(float(expense["amount"]), 2)
#    post_journal_entry(
#        token, date.today().isoformat(), f"Pagamento despesa — {expense['description']}",
#        [
#            {"account_code": "5.1", "debit": amount, "credit": 0},
#            {"account_code": "1.1.2", "debit": 0, "credit": amount},
#        ],
#        "expense", expense["id"],
#    )
#    return expense
#
#
#@app.delete("/expenses/{expense_id}", status_code=204)
#def delete_expense(expense_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("DELETE FROM fin_expenses WHERE id=%s AND company_id=%s AND status='pendente'", (expense_id, company_id))
#            conn.commit()
#
#
## -------------------------
## FORNECEDORES
## -------------------------
#
#@app.get("/suppliers")
#def list_suppliers(active: bool | None = None, q: str | None = None,
#                    user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            clauses, params = ["company_id=%s"], [company_id]
#            if active is not None:
#                clauses.append("active=%s")
#                params.append(active)
#            if q:
#                clauses.append("(name ILIKE %s OR nif ILIKE %s)")
#                params.extend([f"%{q}%", f"%{q}%"])
#            where = f"WHERE {' AND '.join(clauses)}"
#            cur.execute(f"SELECT * FROM fin_suppliers {where} ORDER BY name", params)
#            return cur.fetchall()
#
#
#@app.post("/suppliers", status_code=201)
#def create_supplier(body: SupplierCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("""
#                INSERT INTO fin_suppliers (name, nif, email, phone, category, address, notes, company_id)
#                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
#                RETURNING *
#            """, (body.name, body.nif, body.email, body.phone, body.category, body.address, body.notes, company_id))
#            conn.commit()
#            return cur.fetchone()
#
#
#@app.get("/suppliers/{supplier_id}")
#def get_supplier(supplier_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("SELECT * FROM fin_suppliers WHERE id=%s AND company_id=%s", (supplier_id, company_id))
#            supplier = cur.fetchone()
#    if not supplier:
#        raise HTTPException(status_code=404, detail="Fornecedor não encontrado")
#    return supplier
#
#
#@app.put("/suppliers/{supplier_id}")
#def update_supplier(supplier_id: int, body: SupplierUpdate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("""
#                UPDATE fin_suppliers
#                SET name=%s, nif=%s, email=%s, phone=%s, category=%s, address=%s, notes=%s, active=%s
#                WHERE id=%s AND company_id=%s RETURNING *
#            """, (
#                body.name, body.nif, body.email, body.phone, body.category,
#                body.address, body.notes, body.active, supplier_id, company_id,
#            ))
#            supplier = cur.fetchone()
#            conn.commit()
#    if not supplier:
#        raise HTTPException(status_code=404, detail="Fornecedor não encontrado")
#    return supplier
#
#
#@app.delete("/suppliers/{supplier_id}", status_code=204)
#def delete_supplier(supplier_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("SELECT 1 FROM fin_expenses WHERE supplier_id=%s AND company_id=%s LIMIT 1", (supplier_id, company_id))
#            if cur.fetchone():
#                raise HTTPException(
#                    status_code=409,
#                    detail="Não é possível apagar: há despesas associadas a este fornecedor. Desactive-o em vez disso.",
#                )
#            cur.execute("DELETE FROM fin_suppliers WHERE id=%s AND company_id=%s", (supplier_id, company_id))
#            conn.commit()
#
#
#@app.get("/suppliers/{supplier_id}/expenses")
#def list_supplier_expenses(supplier_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute(
#                "SELECT * FROM fin_expenses WHERE supplier_id=%s AND company_id=%s ORDER BY created_at DESC",
#                (supplier_id, company_id),
#            )
#            return cur.fetchall()
#
#
## -------------------------
## FLUXO DE CAIXA E RESUMO
## -------------------------
#
#@app.get("/cashflow")
#def cashflow(date_from: str, date_to: str, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("""
#                SELECT payment_date AS day, SUM(amount) AS total
#                FROM fin_receipts
#                WHERE company_id = %s AND payment_date BETWEEN %s AND %s
#                GROUP BY payment_date
#            """, (company_id, date_from, date_to))
#            income_by_day = {r["day"].isoformat(): float(r["total"]) for r in cur.fetchall()}
#
#            cur.execute("""
#                SELECT paid_at::date AS day, SUM(amount) AS total
#                FROM fin_expenses
#                WHERE company_id = %s AND paid_at IS NOT NULL AND paid_at::date BETWEEN %s AND %s
#                GROUP BY paid_at::date
#            """, (company_id, date_from, date_to))
#            expense_by_day = {r["day"].isoformat(): float(r["total"]) for r in cur.fetchall()}
#
#    days = sorted(set(income_by_day) | set(expense_by_day))
#    running = 0.0
#    result = []
#    for day in days:
#        income = income_by_day.get(day, 0.0)
#        expense = expense_by_day.get(day, 0.0)
#        running += income - expense
#        result.append({
#            "day": day,
#            "income": income,
#            "expense": expense,
#            "balance": income - expense,
#            "running_balance": round(running, 2),
#        })
#    return result
#
#
#@app.get("/summary")
#def summary(user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    today = date.today().isoformat()
#    month_start = date.today().replace(day=1).isoformat()
#
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("""
#                SELECT COALESCE(SUM(amount), 0) AS total FROM fin_receipts
#                WHERE company_id = %s AND payment_date >= %s
#            """, (company_id, month_start))
#            recebido_mes = float(cur.fetchone()["total"])
#
#            cur.execute("""
#                SELECT COALESCE(SUM(total - paid_amount), 0) AS total FROM fin_invoices
#                WHERE company_id = %s AND status IN ('emitida', 'parcial', 'vencida')
#            """, (company_id,))
#            a_receber = float(cur.fetchone()["total"])
#
#            cur.execute("""
#                SELECT COUNT(*) AS n FROM fin_invoices
#                WHERE company_id = %s AND status IN ('emitida', 'parcial') AND due_date IS NOT NULL AND due_date < %s
#            """, (company_id, today))
#            vencidas = int(cur.fetchone()["n"])
#
#            cur.execute("SELECT COUNT(*) AS n FROM fin_expenses WHERE company_id = %s AND status='pendente'", (company_id,))
#            despesas_pendentes = int(cur.fetchone()["n"])
#
#            cur.execute("""
#                SELECT COALESCE(SUM(amount), 0) AS total FROM fin_receipts WHERE company_id = %s AND payment_date >= %s
#            """, (company_id, month_start))
#            entradas_mes = float(cur.fetchone()["total"])
#
#            cur.execute("""
#                SELECT COALESCE(SUM(amount), 0) AS total FROM fin_expenses
#                WHERE company_id = %s AND paid_at IS NOT NULL AND paid_at::date >= %s
#            """, (company_id, month_start))
#            saidas_mes = float(cur.fetchone()["total"])
#
#    return {
#        "recebido_mes": recebido_mes,
#        "a_receber": a_receber,
#        "vencidas": vencidas,
#        "despesas_pendentes": despesas_pendentes,
#        "saldo_caixa_mes": round(entradas_mes - saidas_mes, 2),
#    }
#
#
## -------------------------
## DOCUMENTOS (ANEXOS DE FACTURAS E DESPESAS)
## -------------------------
#
#@app.post("/invoices/{invoice_id}/documents", status_code=201)
#def upload_invoice_document(
#    invoice_id: int,
#    document_type: str,
#    file: UploadFile = File(...),
#    user=Depends(get_current_user),
#    company_id: int = Depends(get_current_company),
#):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            assert_entity_exists(cur, "invoice", invoice_id, company_id)
#            object_name = upload_file(file, "invoice", invoice_id)
#            cur.execute("""
#                INSERT INTO fin_documents
#                    (entity_type, entity_id, document_type, filename, object_name, content_type, uploaded_by, company_id)
#                VALUES ('invoice', %s, %s, %s, %s, %s, %s, %s)
#                RETURNING *
#            """, (invoice_id, document_type, file.filename, object_name, file.content_type, user, company_id))
#            conn.commit()
#            return cur.fetchone()
#
#
#@app.get("/invoices/{invoice_id}/documents")
#def list_invoice_documents(invoice_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("""
#                SELECT * FROM fin_documents
#                WHERE entity_type='invoice' AND entity_id=%s AND company_id=%s
#                ORDER BY created_at DESC
#            """, (invoice_id, company_id))
#            return cur.fetchall()
#
#
#@app.post("/expenses/{expense_id}/documents", status_code=201)
#def upload_expense_document(
#    expense_id: int,
#    document_type: str,
#    file: UploadFile = File(...),
#    user=Depends(get_current_user),
#    company_id: int = Depends(get_current_company),
#):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            assert_entity_exists(cur, "expense", expense_id, company_id)
#            object_name = upload_file(file, "expense", expense_id)
#            cur.execute("""
#                INSERT INTO fin_documents
#                    (entity_type, entity_id, document_type, filename, object_name, content_type, uploaded_by, company_id)
#                VALUES ('expense', %s, %s, %s, %s, %s, %s, %s)
#                RETURNING *
#            """, (expense_id, document_type, file.filename, object_name, file.content_type, user, company_id))
#            conn.commit()
#            return cur.fetchone()
#
#
#@app.get("/expenses/{expense_id}/documents")
#def list_expense_documents(expense_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("""
#                SELECT * FROM fin_documents
#                WHERE entity_type='expense' AND entity_id=%s AND company_id=%s
#                ORDER BY created_at DESC
#            """, (expense_id, company_id))
#            return cur.fetchall()
#
#
#@app.get("/documents")
#def list_documents(
#    entity_type: str | None = None,
#    document_type: str | None = None,
#    user=Depends(get_current_user),
#    company_id: int = Depends(get_current_company),
#):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            clauses, params = ["company_id=%s"], [company_id]
#            if entity_type:
#                clauses.append("entity_type=%s")
#                params.append(entity_type)
#            if document_type:
#                clauses.append("document_type=%s")
#                params.append(document_type)
#            where = f"WHERE {' AND '.join(clauses)}"
#            cur.execute(f"SELECT * FROM fin_documents {where} ORDER BY created_at DESC", params)
#            return cur.fetchall()
#
#
#@app.get("/documents/{document_id}/presigned-url")
#def get_document_url(document_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("SELECT object_name FROM fin_documents WHERE id=%s AND company_id=%s", (document_id, company_id))
#            doc = cur.fetchone()
#    if not doc:
#        raise HTTPException(status_code=404, detail="Documento não encontrado")
#    return {"url": get_file_url(doc["object_name"])}
#
#
#@app.delete("/documents/{document_id}")
#def delete_document(document_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
#    with get_connection() as conn:
#        with conn.cursor(cursor_factory=RealDictCursor) as cur:
#            cur.execute("SELECT object_name FROM fin_documents WHERE id=%s AND company_id=%s", (document_id, company_id))
#            doc = cur.fetchone()
#            if not doc:
#                raise HTTPException(status_code=404, detail="Documento não encontrado")
#
#            try:
#                delete_object(doc["object_name"])
#            except Exception:
#                raise HTTPException(500, "Erro ao apagar ficheiro no storage")
#
#            cur.execute("DELETE FROM fin_documents WHERE id=%s AND company_id=%s", (document_id, company_id))
#            conn.commit()
#
#    return {"message": "Documento removido com sucesso"}
#