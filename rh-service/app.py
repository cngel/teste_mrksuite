from decimal import Decimal
import os
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordBearer
import uuid
from fastapi import UploadFile, File

if __package__:
    from .core.db import get_connection, init_db
    from .core.storage import upload_bytes, ensure_bucket, delete_object
    from .core.url_service import get_presigned_url
    from .core.payroll_calc import calculate_payroll
else:
    from core.db import get_connection, init_db
    from core.storage import upload_bytes, ensure_bucket, delete_object
    from core.url_service import get_presigned_url
    from core.payroll_calc import calculate_payroll

app = FastAPI(title="People Service (HR)", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://localhost:80", "http://127.0.0.1", "http://localhost:5002"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="http://ms_auth:5000/login"
)


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

    # Sem esta verificação, um token revogado por logout continuava válido aqui
    # até expirar (15 min) — os outros serviços já verificam a blocklist.
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
    """Isolamento entre empresas: toda a leitura/escrita de dados de RH tem de ser
    filtrada por company_id — nunca basta um JWT válido, seja lá de que empresa for."""
    company_id = claims.get("company_id")
    if company_id is None:
        raise HTTPException(status_code=403, detail="Utilizador sem empresa associada")
    return company_id

#-------------------------
# UPLOAD DE FICHEIROS
#-------------------------

def upload_file(file, employee_id: int, doc_type: str):
    ext = file.filename.split(".")[-1]
    object_name = f"{employee_id}/{doc_type}/{uuid.uuid4()}.{ext}"

    data = file.file.read()

    upload_bytes(
        data,
        object_name,
        file.content_type or "application/octet-stream"
    )

    return object_name

#-------------------------------
# DOWNLOAD DE DOCUMENTOS
#-------------------------------

def get_file_url(object_name: str, host: str | None = None):
    return get_presigned_url(object_name, host)


def public_host(request: Request) -> str | None:
    """Hostname pelo qual o utilizador acedeu ao dashboard, propagado pelo
    proxy do web-service via X-Forwarded-Host (ver web-service/app.py) —
    usado para que as URLs pré-assinadas do MinIO funcionem também fora de
    "localhost" (IP de rede local, domínio público, etc.)."""
    return request.headers.get("x-forwarded-host") or request.url.hostname


# -------------------------
# MODELS
# -------------------------

class DepartmentCreate(BaseModel):
    name: str
    color: str = "#6B7280"


class EmployeeCreate(BaseModel):
    full_name: str | None = None
    email: str
    phone: str | None = None
    department_id: int | None = None
    role: str | None = None
    status: str = "online"
    reports_to: int | None = None
    birth_date: str | None = None
    contract_type: str | None = None
    # Dados Pessoais
    employee_number: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    gender: str | None = None
    nationality: str | None = None
    marital_status: str | None = None
    mobile: str | None = None
    address: str | None = None
    province: str | None = None
    city: str | None = None
    # Documentos
    bi_number: str | None = None
    bi_expiry: str | None = None
    nif: str | None = None
    niss: str | None = None
    passport_number: str | None = None
    passport_expiry: str | None = None
    # Financeiro (dados bancários; salário vive em Contratos)
    bank_name: str | None = None
    bank_account: str | None = None
    iban: str | None = None


class EmployeeUpdate(EmployeeCreate):
    pass


class EmployeeStatusUpdate(BaseModel):
    status: str


class LeaveCreate(BaseModel):
    start_date: str
    end_date: str
    reason: str | None = None


class LeaveStatusUpdate(BaseModel):
    status: str

class EmployeeDocumentCreate(BaseModel):
    document_type: str
    filename: str
    object_name: str
    content_type: str | None = None


class ContractCreate(BaseModel):
    employee_id: int
    contract_type: str
    start_date: str
    end_date: str | None = None
    trial_period_days: int | None = None
    weekly_hours: float | None = None
    base_salary: float
    meal_allowance: float = 0
    transport_allowance: float = 0
    apply_inss: bool = True
    apply_irt: bool = True
    notes: str | None = None


class ContractUpdate(ContractCreate):
    pass


class TrainingCreate(BaseModel):
    employee_id: int
    title: str
    provider: str | None = None
    status: str = "ongoing"
    start_date: str | None = None
    end_date: str | None = None
    hours: int | None = None
    cert_url: str | None = None


class TrainingUpdate(TrainingCreate):
    pass


class EvaluationCreate(BaseModel):
    employee_id: int
    cycle: str = "trimestral"
    period: str | None = None
    eval_date: str
    rating: int
    comments: str | None = None


class EvaluationUpdate(EvaluationCreate):
    pass


class PayslipCreate(BaseModel):
    employee_id: int
    month: str
    year: int
    base_salary: float = 0
    extras: float = 0
    deductions: float = 0
    irt: float = 0
    social_security: float = 0
    net: float = 0


class OnboardingItemCreate(BaseModel):
    label: str


class OnboardingItemUpdate(BaseModel):
    label: str | None = None
    done: bool | None = None


DEFAULT_ONBOARDING_ITEMS = [
    "Assinar contrato",
    "Criar email corporativo",
    "Acesso aos sistemas",
    "Apresentação à equipa",
]

class SalaryProfileCreate(BaseModel):
    base_salary: float
    food_allowance: float = 0
    transport_allowance: float = 0
    other_allowance: float = 0


class SalaryProfileUpdate(SalaryProfileCreate):
    pass

class PayrollGenerate(BaseModel):
    month: int
    year: int

class DeductionRuleCreate(BaseModel):
    name: str
    description: str | None = None

    calculation_type: str

    calculation_base: str = "gross_salary"

    value: Decimal = 0

    country_code: str = "AO"

class DeductionRuleUpdate(BaseModel):
    name: str
    description: str | None = None
    calculation_type: str
    calculation_base: str
    value: float
    active: bool

class DeductionBracketCreate(BaseModel):

    minimum_amount: Decimal

    maximum_amount: Decimal | None = None

    percentage: Decimal = 0

    fixed_amount: Decimal = 0


# -------------------------
# DEPARTMENTS
# -------------------------

@app.get("/departments")
def list_departments(user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM departments WHERE company_id = %s ORDER BY name", (company_id,))
            return cur.fetchall()


@app.post("/departments", status_code=201)
def create_department(body: DepartmentCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("""
                    INSERT INTO departments (name, color, company_id)
                    VALUES (%s, %s, %s)
                    RETURNING *
                """, (body.name, body.color, company_id))
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Já existe um departamento com este nome")
            conn.commit()
            return cur.fetchone()


@app.put("/departments/{dept_id}")
def update_department(dept_id: int, body: DepartmentCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("""
                    UPDATE departments SET name = %s, color = %s
                    WHERE id = %s AND company_id = %s RETURNING *
                """, (body.name, body.color, dept_id, company_id))
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Já existe um departamento com este nome")
            conn.commit()
            dept = cur.fetchone()
    if not dept:
        raise HTTPException(status_code=404, detail="Departamento não encontrado")
    return dept


@app.delete("/departments/{dept_id}", status_code=204)
def delete_department(dept_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT 1 FROM employees WHERE department_id = %s AND company_id = %s", (dept_id, company_id))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Há funcionários atribuídos a este departamento")
            cur.execute("DELETE FROM departments WHERE id = %s AND company_id = %s", (dept_id, company_id))
            conn.commit()


# -------------------------
# EMPLOYEES (CORE HR)
# -------------------------

def with_photo_url(emp, host: str | None = None):
    if emp and emp.get("photo_object_name"):
        emp["photo_url"] = get_file_url(emp["photo_object_name"], host)
    else:
        emp["photo_url"] = None
    return emp


@app.get("/employees")
def list_employees(request: Request, department_id: int | None = None, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    host = public_host(request)
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            if department_id:
                cur.execute("""
                    SELECT * FROM employees
                    WHERE department_id = %s AND company_id = %s
                    ORDER BY created_at DESC
                """, (department_id, company_id))
            else:
                cur.execute("""
                    SELECT * FROM employees
                    WHERE company_id = %s
                    ORDER BY created_at DESC
                """, (company_id,))

            return [with_photo_url(e, host) for e in cur.fetchall()]


def resolve_full_name(body) -> str:
    if body.first_name or body.last_name:
        return f"{body.first_name or ''} {body.last_name or ''}".strip()
    return (body.full_name or "").strip()


@app.post("/employees", status_code=201)
def create_employee(request: Request, body: EmployeeCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    full_name = resolve_full_name(body)
    if not full_name:
        raise HTTPException(status_code=422, detail="Nome do funcionário é obrigatório")
    if not (body.email or "").strip():
        raise HTTPException(status_code=422, detail="Email é obrigatório")

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                INSERT INTO employees
                    (full_name, email, phone, department_id, role, status, reports_to, birth_date, contract_type,
                     employee_number, first_name, last_name, gender, nationality, marital_status, mobile,
                     address, province, city,
                     bi_number, bi_expiry, nif, niss, passport_number, passport_expiry,
                     bank_name, bank_account, iban, company_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                full_name,
                body.email,
                body.phone,
                body.department_id,
                body.role,
                body.status,
                body.reports_to,
                body.birth_date,
                body.contract_type,
                body.employee_number,
                body.first_name,
                body.last_name,
                body.gender,
                body.nationality,
                body.marital_status,
                body.mobile,
                body.address,
                body.province,
                body.city,
                body.bi_number,
                body.bi_expiry,
                body.nif,
                body.niss,
                body.passport_number,
                body.passport_expiry,
                body.bank_name,
                body.bank_account,
                body.iban,
                company_id,
            ))

            conn.commit()
            return with_photo_url(cur.fetchone(), public_host(request))


@app.get("/employees/{emp_id}")
def get_employee(request: Request, emp_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT * FROM employees WHERE id = %s AND company_id = %s
            """, (emp_id, company_id))

            emp = cur.fetchone()

    if not emp:
        raise HTTPException(status_code=404, detail="Funcionário não encontrado")

    return with_photo_url(emp, public_host(request))


@app.put("/employees/{emp_id}")
def update_employee(request: Request, emp_id: int, body: EmployeeUpdate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    full_name = resolve_full_name(body)
    if not full_name:
        raise HTTPException(status_code=422, detail="Nome do funcionário é obrigatório")
    if not (body.email or "").strip():
        raise HTTPException(status_code=422, detail="Email é obrigatório")

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE employees
                SET full_name=%s, email=%s, phone=%s, department_id=%s, role=%s,
                    status=%s, reports_to=%s, birth_date=%s, contract_type=%s,
                    employee_number=%s, first_name=%s, last_name=%s, gender=%s, nationality=%s,
                    marital_status=%s, mobile=%s, address=%s, province=%s, city=%s,
                    bi_number=%s, bi_expiry=%s, nif=%s, niss=%s, passport_number=%s, passport_expiry=%s,
                    bank_name=%s, bank_account=%s, iban=%s
                WHERE id=%s AND company_id=%s RETURNING *
            """, (
                full_name, body.email, body.phone, body.department_id, body.role,
                body.status, body.reports_to, body.birth_date, body.contract_type,
                body.employee_number, body.first_name, body.last_name, body.gender, body.nationality,
                body.marital_status, body.mobile, body.address, body.province, body.city,
                body.bi_number, body.bi_expiry, body.nif, body.niss, body.passport_number, body.passport_expiry,
                body.bank_name, body.bank_account, body.iban,
                emp_id, company_id,
            ))
            conn.commit()
            emp = cur.fetchone()
    if not emp:
        raise HTTPException(status_code=404, detail="Funcionário não encontrado")
    return with_photo_url(emp, public_host(request))


@app.patch("/employees/{emp_id}/status")
def update_employee_status(emp_id: int, body: EmployeeStatusUpdate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE employees SET status=%s WHERE id=%s AND company_id=%s RETURNING *
            """, (body.status, emp_id, company_id))
            conn.commit()
            emp = cur.fetchone()
    if not emp:
        raise HTTPException(status_code=404, detail="Funcionário não encontrado")
    return emp


@app.delete("/employees/{emp_id}", status_code=204)
def delete_employee(emp_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM employees WHERE id = %s AND company_id = %s", (emp_id, company_id))
            conn.commit()


def _require_employee(cur, emp_id: int, company_id: int) -> None:
    cur.execute("SELECT 1 FROM employees WHERE id = %s AND company_id = %s", (emp_id, company_id))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Funcionário não encontrado")


# FOTO DE PERFIL (separada do arquivo de documentos, um único ficheiro por colaborador)

@app.post("/employees/{emp_id}/photo")
def upload_employee_photo(
    request: Request,
    emp_id: int,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT photo_object_name FROM employees WHERE id=%s AND company_id=%s", (emp_id, company_id))
            existing = cur.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Funcionário não encontrado")

            old_object_name = existing["photo_object_name"]
            object_name = upload_file(file, emp_id, "foto")

            cur.execute("""
                UPDATE employees SET photo_object_name=%s WHERE id=%s AND company_id=%s RETURNING *
            """, (object_name, emp_id, company_id))
            conn.commit()
            emp = cur.fetchone()

    if old_object_name:
        try:
            delete_object(old_object_name)
        except Exception:
            pass

    return with_photo_url(emp, public_host(request))


# -------------------------
# ATTENDANCE (RH OPERATIONAL)
# -------------------------

@app.post("/employees/{emp_id}/checkin")
def check_in(emp_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, emp_id, company_id)

            cur.execute("""
                SELECT id FROM leave_requests
                WHERE employee_id = %s AND company_id = %s AND status != 'rejected'
                  AND start_date <= CURRENT_DATE AND end_date >= CURRENT_DATE
                LIMIT 1
            """, (emp_id, company_id))
            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="Funcionário tem uma falta/ausência registada para hoje — não é possível marcar presença"
                )

            cur.execute("""
                INSERT INTO attendance (employee_id, check_in, company_id)
                VALUES (%s, NOW(), %s)
            """, (emp_id, company_id))

            conn.commit()

    return {"message": "Check-in registado pelo RH"}


@app.post("/employees/{emp_id}/checkout")
def check_out(emp_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, emp_id, company_id)
            cur.execute("""
                UPDATE attendance
                SET check_out = NOW()
                WHERE employee_id = %s AND company_id = %s AND check_out IS NULL
            """, (emp_id, company_id))

            conn.commit()

    return {"message": "Check-out registado pelo RH"}


# -------------------------
# LEAVE MANAGEMENT (RH DECIDE)
# -------------------------

@app.post("/employees/{emp_id}/leave", status_code=201)
def create_leave(emp_id: int, body: LeaveCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, emp_id, company_id)

            cur.execute("SELECT department_id FROM employees WHERE id = %s AND company_id = %s", (emp_id, company_id))
            department_id = cur.fetchone()["department_id"]

            if department_id is not None:
                cur.execute("""
                    SELECT e.full_name
                    FROM leave_requests lr
                    JOIN employees e ON e.id = lr.employee_id
                    WHERE lr.company_id = %s
                      AND e.department_id = %s
                      AND lr.employee_id != %s
                      AND lr.status != 'rejected'
                      AND lr.start_date <= %s AND lr.end_date >= %s
                    LIMIT 1
                """, (company_id, department_id, emp_id, body.end_date, body.start_date))
                conflict = cur.fetchone()
                if conflict:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Já existe um pedido de férias de {conflict['full_name']} neste departamento para esse período"
                    )

            cur.execute("""
                INSERT INTO leave_requests
                (employee_id, start_date, end_date, reason, status, company_id)
                VALUES (%s, %s, %s, %s, 'pending', %s)
                RETURNING *
            """, (
                emp_id,
                body.start_date,
                body.end_date,
                body.reason,
                company_id,
            ))

            conn.commit()
            return cur.fetchone()


@app.patch("/employees/{emp_id}/leave/{leave_id}")
def update_leave_status(emp_id: int, leave_id: int, body: LeaveStatusUpdate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    if body.status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=422, detail="Estado inválido")

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, emp_id, company_id)
            cur.execute("""
                UPDATE leave_requests
                SET status = %s
                WHERE id = %s AND employee_id = %s AND company_id = %s
                RETURNING *
            """, (body.status, leave_id, emp_id, company_id))

            updated = cur.fetchone()
            if not updated:
                raise HTTPException(status_code=404, detail="Pedido de férias não encontrado")

            conn.commit()
            return updated


@app.get("/employees/{emp_id}/leave")
def list_leaves(emp_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, emp_id, company_id)
            cur.execute("""
                SELECT * FROM leave_requests
                WHERE employee_id = %s
                ORDER BY created_at DESC
            """, (emp_id,))

            return cur.fetchall()

# ADICIONAR DOCUMENTOS

@app.post("/employees/{emp_id}/documents", status_code=201)
def upload_document(
    emp_id: int,
    document_type: str,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, emp_id, company_id)

            object_name = upload_file(file, emp_id, document_type)

            cur.execute("""
                INSERT INTO employee_documents
                (employee_id, document_type, filename, object_name, content_type, company_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                emp_id,
                document_type,
                file.filename,
                object_name,
                file.content_type,
                company_id,
            ))

            conn.commit()
            return cur.fetchone()

# LISTAR DOCUMENTOS

@app.get("/employees/{emp_id}/documents")
def list_documents(
    emp_id: int,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, emp_id, company_id)
            cur.execute("""
                SELECT *
                FROM employee_documents
                WHERE employee_id=%s AND document_type <> 'foto'
                ORDER BY created_at DESC
            """, (emp_id,))

            return cur.fetchall()


# OBTER UM DOCUMENTO ESPECÍFICO

@app.get("/documents/{document_id}/presigned-url")
def get_document_url(request: Request, document_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT object_name
                FROM employee_documents
                WHERE id=%s AND company_id=%s
            """, (document_id, company_id))

            doc = cur.fetchone()

    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    return {
        "url": get_file_url(doc["object_name"], public_host(request))
    }


# REMOVER DOCUMENTOS

@app.delete("/documents/{document_id}")
def delete_document(document_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT object_name
                FROM employee_documents
                WHERE id=%s AND company_id=%s
            """, (document_id, company_id))

            doc = cur.fetchone()

            if not doc:
                raise HTTPException(status_code=404, detail="Documento não encontrado")

            # apagar no MinIO
            try:
                delete_object(doc["object_name"])
            except Exception:
                raise HTTPException(500, "Erro ao apagar ficheiro no storage")

            # apagar no Postgres
            cur.execute("""
                DELETE FROM employee_documents
                WHERE id=%s AND company_id=%s
            """, (document_id, company_id))

            conn.commit()

    return {"message": "Documento removido com sucesso"}


# -------------------------
# CONTRATOS
# -------------------------

@app.get("/contracts")
def list_contracts(employee_id: int | None = None, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if employee_id:
                cur.execute("""
                    SELECT c.*, e.full_name AS employee_name
                    FROM contracts c JOIN employees e ON e.id = c.employee_id
                    WHERE c.employee_id = %s AND c.company_id = %s
                    ORDER BY c.start_date DESC
                """, (employee_id, company_id))
            else:
                cur.execute("""
                    SELECT c.*, e.full_name AS employee_name
                    FROM contracts c JOIN employees e ON e.id = c.employee_id
                    WHERE c.company_id = %s
                    ORDER BY c.start_date DESC
                """, (company_id,))
            return cur.fetchall()


@app.post("/contracts", status_code=201)
def create_contract(body: ContractCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, body.employee_id, company_id)

            cur.execute("""
                UPDATE contracts SET status='terminado'
                WHERE employee_id=%s AND company_id=%s AND status='ativo'
            """, (body.employee_id, company_id))

            cur.execute("""
                INSERT INTO contracts
                    (employee_id, contract_type, start_date, end_date, trial_period_days, weekly_hours,
                     base_salary, meal_allowance, transport_allowance, apply_inss, apply_irt, notes, company_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                body.employee_id, body.contract_type, body.start_date, body.end_date,
                body.trial_period_days, body.weekly_hours, body.base_salary,
                body.meal_allowance, body.transport_allowance, body.apply_inss, body.apply_irt, body.notes,
                company_id,
            ))
            conn.commit()
            return cur.fetchone()


@app.get("/employees/{emp_id}/contracts")
def list_employee_contracts(emp_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM contracts WHERE employee_id=%s AND company_id=%s ORDER BY start_date DESC
            """, (emp_id, company_id))
            return cur.fetchall()


@app.get("/employees/{emp_id}/contracts/active")
def get_active_contract(emp_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM contracts WHERE employee_id=%s AND company_id=%s AND status='ativo'
                ORDER BY start_date DESC LIMIT 1
            """, (emp_id, company_id))
            contract = cur.fetchone()
    if not contract:
        raise HTTPException(status_code=404, detail="Sem contrato activo")
    return contract


@app.put("/contracts/{contract_id}")
def update_contract(contract_id: int, body: ContractUpdate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE contracts
                SET contract_type=%s, start_date=%s, end_date=%s, trial_period_days=%s, weekly_hours=%s,
                    base_salary=%s, meal_allowance=%s, transport_allowance=%s, apply_inss=%s, apply_irt=%s, notes=%s
                WHERE id=%s AND company_id=%s RETURNING *
            """, (
                body.contract_type, body.start_date, body.end_date, body.trial_period_days, body.weekly_hours,
                body.base_salary, body.meal_allowance, body.transport_allowance, body.apply_inss, body.apply_irt,
                body.notes, contract_id, company_id,
            ))
            conn.commit()
            contract = cur.fetchone()
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    return contract


@app.delete("/contracts/{contract_id}", status_code=204)
def delete_contract(contract_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM contracts WHERE id = %s AND company_id = %s", (contract_id, company_id))
            conn.commit()


# -------------------------
# FORMAÇÕES
# -------------------------

@app.get("/trainings")
def list_trainings(employee_id: int | None = None, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if employee_id:
                cur.execute(
                    "SELECT * FROM trainings WHERE employee_id=%s AND company_id=%s ORDER BY created_at DESC",
                    (employee_id, company_id),
                )
            else:
                cur.execute("SELECT * FROM trainings WHERE company_id=%s ORDER BY created_at DESC", (company_id,))
            return cur.fetchall()


@app.post("/trainings", status_code=201)
def create_training(body: TrainingCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, body.employee_id, company_id)
            cur.execute("""
                INSERT INTO trainings
                    (employee_id, title, provider, status, start_date, end_date, hours, cert_url, company_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                body.employee_id, body.title, body.provider, body.status,
                body.start_date, body.end_date, body.hours, body.cert_url, company_id,
            ))
            conn.commit()
            return cur.fetchone()


@app.get("/employees/{emp_id}/trainings")
def list_employee_trainings(emp_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM trainings WHERE employee_id=%s AND company_id=%s ORDER BY created_at DESC",
                (emp_id, company_id),
            )
            return cur.fetchall()


@app.put("/trainings/{training_id}")
def update_training(training_id: int, body: TrainingUpdate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, body.employee_id, company_id)
            cur.execute("""
                UPDATE trainings
                SET employee_id=%s, title=%s, provider=%s, status=%s, start_date=%s, end_date=%s, hours=%s, cert_url=%s
                WHERE id=%s AND company_id=%s RETURNING *
            """, (
                body.employee_id, body.title, body.provider, body.status,
                body.start_date, body.end_date, body.hours, body.cert_url, training_id, company_id,
            ))
            conn.commit()
            training = cur.fetchone()
    if not training:
        raise HTTPException(status_code=404, detail="Formação não encontrada")
    return training


@app.delete("/trainings/{training_id}", status_code=204)
def delete_training(training_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM trainings WHERE id = %s AND company_id = %s", (training_id, company_id))
            conn.commit()


# -------------------------
# AVALIAÇÕES
# -------------------------

@app.get("/evaluations")
def list_evaluations(employee_id: int | None = None, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if employee_id:
                cur.execute(
                    "SELECT * FROM evaluations WHERE employee_id=%s AND company_id=%s ORDER BY eval_date DESC",
                    (employee_id, company_id),
                )
            else:
                cur.execute("SELECT * FROM evaluations WHERE company_id=%s ORDER BY eval_date DESC", (company_id,))
            return cur.fetchall()


@app.post("/evaluations", status_code=201)
def create_evaluation(body: EvaluationCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, body.employee_id, company_id)
            cur.execute("""
                INSERT INTO evaluations
                    (employee_id, cycle, period, eval_date, rating, comments, company_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                body.employee_id, body.cycle, body.period, body.eval_date, body.rating, body.comments, company_id,
            ))
            conn.commit()
            return cur.fetchone()


@app.get("/employees/{emp_id}/evaluations")
def list_employee_evaluations(emp_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM evaluations WHERE employee_id=%s AND company_id=%s ORDER BY eval_date DESC",
                (emp_id, company_id),
            )
            return cur.fetchall()


@app.put("/evaluations/{evaluation_id}")
def update_evaluation(evaluation_id: int, body: EvaluationUpdate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, body.employee_id, company_id)
            cur.execute("""
                UPDATE evaluations
                SET employee_id=%s, cycle=%s, period=%s, eval_date=%s, rating=%s, comments=%s
                WHERE id=%s AND company_id=%s RETURNING *
            """, (
                body.employee_id, body.cycle, body.period, body.eval_date, body.rating, body.comments,
                evaluation_id, company_id,
            ))
            conn.commit()
            evaluation = cur.fetchone()
    if not evaluation:
        raise HTTPException(status_code=404, detail="Avaliação não encontrada")
    return evaluation


@app.delete("/evaluations/{evaluation_id}", status_code=204)
def delete_evaluation(evaluation_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM evaluations WHERE id = %s AND company_id = %s", (evaluation_id, company_id))
            conn.commit()


# -------------------------
# RECIBOS DE VENCIMENTO
# -------------------------

@app.get("/payslips")
def list_payslips(employee_id: int | None = None, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if employee_id:
                cur.execute("""
                    SELECT * FROM payslips WHERE employee_id=%s AND company_id=%s
                    ORDER BY year DESC, month DESC
                """, (employee_id, company_id))
            else:
                cur.execute("SELECT * FROM payslips WHERE company_id=%s ORDER BY year DESC, month DESC", (company_id,))
            return cur.fetchall()


@app.post("/payslips", status_code=201)
def create_payslip(body: PayslipCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, body.employee_id, company_id)
            cur.execute("""
                INSERT INTO payslips
                    (employee_id, month, year, base_salary, extras, deductions, irt, social_security, net, company_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                body.employee_id, body.month, body.year, body.base_salary,
                body.extras, body.deductions, body.irt, body.social_security, body.net, company_id,
            ))
            conn.commit()
            return cur.fetchone()


@app.get("/employees/{emp_id}/payslips")
def list_employee_payslips(emp_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM payslips WHERE employee_id=%s AND company_id=%s
                ORDER BY year DESC, month DESC
            """, (emp_id, company_id))
            return cur.fetchall()


@app.delete("/payslips/{payslip_id}", status_code=204)
def delete_payslip(payslip_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM payslips WHERE id = %s AND company_id = %s", (payslip_id, company_id))
            conn.commit()


# -------------------------
# ONBOARDING
# -------------------------

@app.get("/employees/{emp_id}/onboarding")
def get_onboarding(emp_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, emp_id, company_id)
            cur.execute("SELECT * FROM onboarding_checklists WHERE employee_id=%s AND company_id=%s", (emp_id, company_id))
            checklist = cur.fetchone()

            if not checklist:
                cur.execute("""
                    INSERT INTO onboarding_checklists (employee_id, company_id) VALUES (%s, %s) RETURNING *
                """, (emp_id, company_id))
                checklist = cur.fetchone()
                for i, label in enumerate(DEFAULT_ONBOARDING_ITEMS):
                    cur.execute("""
                        INSERT INTO onboarding_items (checklist_id, label, position, company_id)
                        VALUES (%s, %s, %s, %s)
                    """, (checklist["id"], label, i, company_id))
                conn.commit()

            cur.execute("""
                SELECT * FROM onboarding_items WHERE checklist_id=%s ORDER BY position, id
            """, (checklist["id"],))
            items = cur.fetchall()

    return {"checklist_id": checklist["id"], "employee_id": emp_id, "items": items}


@app.post("/employees/{emp_id}/onboarding/items", status_code=201)
def add_onboarding_item(emp_id: int, body: OnboardingItemCreate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_employee(cur, emp_id, company_id)
            cur.execute("SELECT * FROM onboarding_checklists WHERE employee_id=%s AND company_id=%s", (emp_id, company_id))
            checklist = cur.fetchone()
            if not checklist:
                cur.execute("""
                    INSERT INTO onboarding_checklists (employee_id, company_id) VALUES (%s, %s) RETURNING *
                """, (emp_id, company_id))
                checklist = cur.fetchone()

            cur.execute("""
                SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM onboarding_items WHERE checklist_id=%s
            """, (checklist["id"],))
            next_pos = cur.fetchone()["next_pos"]

            cur.execute("""
                INSERT INTO onboarding_items (checklist_id, label, position, company_id)
                VALUES (%s, %s, %s, %s)
                RETURNING *
            """, (checklist["id"], body.label, next_pos, company_id))
            conn.commit()
            return cur.fetchone()


@app.patch("/onboarding/items/{item_id}")
def update_onboarding_item(item_id: int, body: OnboardingItemUpdate, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM onboarding_items WHERE id=%s AND company_id=%s", (item_id, company_id))
            item = cur.fetchone()
            if not item:
                raise HTTPException(status_code=404, detail="Item não encontrado")

            label = body.label if body.label is not None else item["label"]
            done = body.done if body.done is not None else item["done"]

            cur.execute("""
                UPDATE onboarding_items SET label=%s, done=%s WHERE id=%s AND company_id=%s RETURNING *
            """, (label, done, item_id, company_id))
            conn.commit()
            return cur.fetchone()


@app.delete("/onboarding/items/{item_id}", status_code=204)
def delete_onboarding_item(item_id: int, user=Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM onboarding_items WHERE id = %s AND company_id = %s", (item_id, company_id))
            conn.commit()

# ADICIONAR PERFIL SALARIAL

@app.post("/employees/{emp_id}/salary-profile", status_code=201)
def create_salary_profile(
    emp_id: int,
    body: SalaryProfileCreate,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # Verificar se o funcionário existe
            cur.execute("""
                SELECT id
                FROM employees
                WHERE id = %s AND company_id = %s
            """, (emp_id, company_id))

            if not cur.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail="Funcionário não encontrado"
                )

            # Verificar se já existe perfil salarial
            cur.execute("""
                SELECT id
                FROM salary_profiles
                WHERE employee_id = %s
            """, (emp_id,))

            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="O funcionário já possui um perfil salarial."
                )

            # Criar perfil salarial
            cur.execute("""
                INSERT INTO salary_profiles (
                    employee_id,
                    base_salary,
                    food_allowance,
                    transport_allowance,
                    other_allowance,
                    company_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                emp_id,
                body.base_salary,
                body.food_allowance,
                body.transport_allowance,
                body.other_allowance,
                company_id,
            ))

            salary_profile = cur.fetchone()

            conn.commit()

            return salary_profile

# BUSCAR PERFIL SALARIAL

@app.get("/employees/{emp_id}/salary-profile")
def get_salary_profile(
    emp_id: int,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # Verificar se o funcionário existe
            cur.execute("""
                SELECT id
                FROM employees
                WHERE id = %s AND company_id = %s
            """, (emp_id, company_id))

            if not cur.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail="Funcionário não encontrado"
                )

            # Obter perfil salarial
            cur.execute("""
                SELECT
                    id,
                    employee_id,
                    base_salary,
                    food_allowance,
                    transport_allowance,
                    other_allowance,
                    created_at
                FROM salary_profiles
                WHERE employee_id = %s
            """, (emp_id,))

            salary_profile = cur.fetchone()

            if not salary_profile:
                raise HTTPException(
                    status_code=404,
                    detail="O funcionário não possui perfil salarial."
                )

            return salary_profile


# ATUALIZAR PERFIL SALARIAL

@app.put("/employees/{emp_id}/salary-profile")
def update_salary_profile(
    emp_id: int,
    body: SalaryProfileUpdate,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # Verificar se o funcionário existe
            cur.execute("""
                SELECT id
                FROM employees
                WHERE id = %s AND company_id = %s
            """, (emp_id, company_id))

            if not cur.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail="Funcionário não encontrado"
                )

            # Verificar se o perfil salarial existe
            cur.execute("""
                SELECT id
                FROM salary_profiles
                WHERE employee_id = %s
            """, (emp_id,))

            if not cur.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail="O funcionário não possui perfil salarial."
                )

            # Atualizar perfil salarial
            cur.execute("""
                UPDATE salary_profiles
                SET
                    base_salary = %s,
                    food_allowance = %s,
                    transport_allowance = %s,
                    other_allowance = %s
                WHERE employee_id = %s AND company_id = %s
                RETURNING *
            """, (
                body.base_salary,
                body.food_allowance,
                body.transport_allowance,
                body.other_allowance,
                emp_id,
                company_id,
            ))

            salary_profile = cur.fetchone()

            conn.commit()

            return salary_profile


# CRIAR REGRAS DE DEDUÇÃO

@app.post("/deduction-rules", status_code=201)
def create_deduction_rule(
    body: DeductionRuleCreate,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):

    allowed_types = [
        "fixed",
        "percentage",
        "bracket"
    ]

    if body.calculation_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Tipo de cálculo inválido."
        )

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # Verificar se já existe uma regra com o mesmo nome para o país (dentro da mesma empresa)
            cur.execute("""
                SELECT id
                FROM deduction_rules
                WHERE company_id = %s
                  AND country_code = %s
                  AND LOWER(name) = LOWER(%s)
            """,
            (
                company_id,
                body.country_code,
                body.name
            ))

            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="Já existe uma regra com este nome para este país."
                )

            # Criar regra
            cur.execute("""
                INSERT INTO deduction_rules(
                    name,
                    description,
                    calculation_type,
                    calculation_base,
                    value,
                    country_code,
                    company_id
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """,
            (
                body.name,
                body.description,
                body.calculation_type,
                body.calculation_base,
                body.value,
                body.country_code,
                company_id,
            ))

            rule = cur.fetchone()

            conn.commit()

            return rule

# LISTAR REGRAS DE DEDUÇÃO
@app.get("/deduction-rules")
def list_deduction_rules(
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM deduction_rules
                WHERE company_id = %s
                ORDER BY id DESC
            """, (company_id,))

            return cur.fetchall()

# BUSCAR UMA REGRA DE DEDUÇÃO
@app.get("/deduction-rules/{rule_id}")
def get_deduction_rule(
    rule_id:int,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM deduction_rules
                WHERE id=%s AND company_id=%s
            """,
            (rule_id, company_id))

            rule = cur.fetchone()

            if not rule:
                raise HTTPException(
                    status_code=404,
                    detail="Regra não encontrada."
                )

            return rule

# ATUALIZAR UMA REGRA DE DEDUÇÃO

@app.put("/deduction-rules/{rule_id}")
def update_deduction_rule(
    rule_id: int,
    body: DeductionRuleUpdate,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):

    allowed_types = [
        "fixed",
        "percentage",
        "bracket"
    ]

    if body.calculation_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Tipo de cálculo inválido."
        )

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # Verificar se a regra existe
            cur.execute("""
                SELECT *
                FROM deduction_rules
                WHERE id = %s AND company_id = %s
            """, (rule_id, company_id))

            existing_rule = cur.fetchone()

            if not existing_rule:
                raise HTTPException(
                    status_code=404,
                    detail="Regra não encontrada."
                )

            # Verificar duplicidade de nome no mesmo país (dentro da mesma empresa)
            cur.execute("""
                SELECT id
                FROM deduction_rules
                WHERE company_id = %s
                  AND country_code = %s
                  AND LOWER(name) = LOWER(%s)
                  AND id <> %s
            """,
            (
                company_id,
                existing_rule["country_code"],
                body.name,
                rule_id
            ))

            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="Já existe uma regra com este nome para este país."
                )

            # Atualizar regra
            cur.execute("""
                UPDATE deduction_rules
                SET
                    name = %s,
                    description = %s,
                    calculation_type = %s,
                    calculation_base = %s,
                    value = %s,
                    active = %s,
                    updated_at = NOW()
                WHERE id = %s AND company_id = %s
                RETURNING *
            """,
            (
                body.name,
                body.description,
                body.calculation_type,
                body.calculation_base,
                body.value,
                body.active,
                rule_id,
                company_id,
            ))

            updated_rule = cur.fetchone()

            conn.commit()

            return updated_rule

# GERAR FOLHA SALARIAL

@app.post("/employees/{emp_id}/payrolls/generate", status_code=201)
def generate_payroll(
    emp_id: int,
    body: PayrollGenerate,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:


            # 1. Verificar funcionário

            cur.execute("""
                SELECT id
                FROM employees
                WHERE id = %s AND company_id = %s
            """, (emp_id, company_id))

            employee = cur.fetchone()

            if not employee:
                raise HTTPException(
                    status_code=404,
                    detail="Funcionário não encontrado."
                )


            # 2. Buscar perfil salarial

            cur.execute("""
                SELECT *
                FROM salary_profiles
                WHERE employee_id = %s
            """, (emp_id,))

            profile = cur.fetchone()

            if not profile:
                raise HTTPException(
                    status_code=404,
                    detail="Funcionário sem perfil salarial."
                )


            # 3. Verificar duplicação

            cur.execute("""
                SELECT id
                FROM payrolls
                WHERE employee_id = %s
                AND month = %s
                AND year = %s
            """,
            (
                emp_id,
                body.month,
                body.year
            ))

            existing = cur.fetchone()

            if existing:
                raise HTTPException(
                    status_code=409,
                    detail="Já existe uma folha para este período."
                )


            # 4. Buscar regras de dedução

            cur.execute("""
                SELECT *
                FROM deduction_rules
                WHERE active = true
                AND country_code = %s
                AND company_id = %s
            """,
            ("AO", company_id))


            deduction_rules = cur.fetchall()


            # 5. Calcular folha

            calculation = calculate_payroll(
                profile,
                deduction_rules
            )


            gross_salary = calculation["gross_salary"]

            total_earnings = calculation["total_earnings"]

            total_deductions = calculation["total_deductions"]

            net_salary = calculation["net_salary"]

            payroll_items = calculation["items"]



            # 6. Criar folha

            cur.execute("""
                INSERT INTO payrolls(
                    employee_id,
                    month,
                    year,
                    total_earnings,
                    total_deductions,
                    net_salary,
                    gross_salary,
                    status,
                    company_id
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """,
            (
                emp_id,
                body.month,
                body.year,
                total_earnings,
                total_deductions,
                net_salary,
                gross_salary,
                "draft",
                company_id,
            ))


            payroll = cur.fetchone()



            # 7. Criar itens

            for item in payroll_items:

                cur.execute("""
                    INSERT INTO payroll_items(
                        payroll_id,
                        item_type,
                        description,
                        amount,
                        created_value,
                        company_id
                    )
                    VALUES(%s,%s,%s,%s,%s,%s)
                """,
                (
                    payroll["id"],
                    item["item_type"],
                    item["description"],
                    item["amount"],
                    item["amount"],
                    company_id,
                ))



            conn.commit()



            return {
                "payroll": payroll,
                "items": payroll_items
            }

# LISTAR FOLHAS SALARIAIS DO FUNCIONÁRIO

@app.get("/employees/{emp_id}/payrolls")
def list_employee_payrolls(
    emp_id: int,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:


            # verificar funcionário

            cur.execute("""
                SELECT id
                FROM employees
                WHERE id = %s AND company_id = %s
            """, (emp_id, company_id))


            employee = cur.fetchone()


            if not employee:
                raise HTTPException(
                    status_code=404,
                    detail="Funcionário não encontrado."
                )


            cur.execute("""
                SELECT *
                FROM payrolls
                WHERE employee_id = %s
                ORDER BY year DESC, month DESC
            """,
            (emp_id,))


            payrolls = cur.fetchall()


            return payrolls

# DETALHE DA FOLHA SALARIAL

@app.get("/payrolls/{payroll_id}")
def get_payroll(
    payroll_id: int,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:


            cur.execute("""
                SELECT *
                FROM payrolls
                WHERE id = %s AND company_id = %s
            """,
            (payroll_id, company_id))


            payroll = cur.fetchone()


            if not payroll:
                raise HTTPException(
                    status_code=404,
                    detail="Folha não encontrada."
                )


            cur.execute("""
                SELECT
                    id,
                    item_type,
                    description,
                    amount
                FROM payroll_items
                WHERE payroll_id = %s
                ORDER BY id
            """,
            (payroll_id,))


            items = cur.fetchall()


            return {
                "payroll": payroll,
                "items": items
            }

# APROVAR FOLHA SALARIAL

@app.post("/payrolls/{payroll_id}/approve")
def approve_payroll(
    payroll_id: int,
    user=Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:


            # Verificar se existe

            cur.execute("""
                SELECT *
                FROM payrolls
                WHERE id = %s AND company_id = %s
            """,
            (payroll_id, company_id))


            payroll = cur.fetchone()


            if not payroll:
                raise HTTPException(
                    status_code=404,
                    detail="Folha não encontrada."
                )


            # Verificar estado atual

            if payroll["status"] == "approved":

                raise HTTPException(
                    status_code=409,
                    detail="Folha já está aprovada."
                )


            # Aprovar

            cur.execute("""
                UPDATE payrolls
                SET status = 'approved'
                WHERE id = %s AND company_id = %s
                RETURNING *
            """,
            (payroll_id, company_id))


            updated_payroll = cur.fetchone()


            conn.commit()


            return {
                "message": "Folha aprovada com sucesso.",
                "payroll": updated_payroll
            }