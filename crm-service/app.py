import os
import uuid
from datetime import datetime

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

if __package__:
    from .core.db import get_connection, init_db
    from .core.tools import ensure_bucket, get_presigned_url, upload_bytes
else:
    from core.db import get_connection, init_db
    from core.tools import ensure_bucket, get_presigned_url, upload_bytes

app = FastAPI(title="CRM Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS",
        "*",
    ).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JWT_SECRET    = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="http://localhost:5000/login")


@app.on_event("startup")
def on_startup():
    init_db()
    ensure_bucket()


# ---------------------------------------------------------------------------
# Verificação JWT (partilha blocklist com auth-service via mesma DB)
# ---------------------------------------------------------------------------

def get_current_claims(token: str = Depends(oauth2_scheme)) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token de acesso inválido")

    jti = payload.get("jti")
    if jti:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT jti FROM jwt_blocklist WHERE jti = %s AND expires_at > NOW()",
                    (jti,),
                )
                if cur.fetchone():
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revogado")

    return payload


def get_current_user(claims: dict = Depends(get_current_claims)) -> str:
    return claims["sub"]


def get_current_company(claims: dict = Depends(get_current_claims)) -> int:
    """Isolamento entre empresas: toda a leitura/escrita de contactos tem de ser
    filtrada por company_id — nunca basta um JWT válido, seja lá de que empresa for."""
    company_id = claims.get("company_id")
    if company_id is None:
        raise HTTPException(status_code=403, detail="Utilizador sem empresa associada")
    return company_id


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

class ContactCreate(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    notes: str | None = None
    stage: str = "novo"
    pipeline_value: float = 0
    channel: str = "outros"
    owner: str | None = None
    service_type: str | None = None
    lead_date: str | None = None


class ContactUpdate(ContactCreate):
    pass


@app.get("/contacts")
def list_contacts(user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM contacts WHERE company_id = %s ORDER BY created_at DESC", (company_id,))
            return cur.fetchall()


@app.post("/contacts", status_code=201)
def create_contact(body: ContactCreate, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO contacts
                       (name, email, phone, company, notes,
                        stage, pipeline_value, channel, owner, service_type, lead_date, company_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    body.name, body.email, body.phone, body.company, body.notes,
                    body.stage, body.pipeline_value, body.channel,
                    body.owner or "", body.service_type or "",
                    body.lead_date, company_id,
                ),
            )
            conn.commit()
            return cur.fetchone()


@app.get("/contacts/{contact_id}")
def get_contact(contact_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM contacts WHERE id = %s AND company_id = %s", (contact_id, company_id))
            contact = cur.fetchone()
    if not contact:
        raise HTTPException(status_code=404, detail="Contacto não encontrado")
    return contact


@app.put("/contacts/{contact_id}")
def update_contact(contact_id: int, body: ContactUpdate, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """UPDATE contacts
                   SET name=%s, email=%s, phone=%s, company=%s, notes=%s,
                       stage=%s, pipeline_value=%s, channel=%s,
                       owner=%s, service_type=%s, lead_date=%s
                   WHERE id=%s AND company_id=%s RETURNING *""",
                (
                    body.name, body.email, body.phone, body.company, body.notes,
                    body.stage, body.pipeline_value, body.channel,
                    body.owner or "", body.service_type or "",
                    body.lead_date, contact_id, company_id,
                ),
            )
            conn.commit()
            contact = cur.fetchone()
    if not contact:
        raise HTTPException(status_code=404, detail="Contacto não encontrado")
    return contact


@app.patch("/contacts/{contact_id}/stage")
def update_stage(contact_id: int, payload: dict, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
    stage = payload.get("stage")
    if not stage:
        raise HTTPException(status_code=400, detail="Campo 'stage' obrigatório")
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "UPDATE contacts SET stage=%s WHERE id=%s AND company_id=%s RETURNING *",
                (stage, contact_id, company_id),
            )
            conn.commit()
            contact = cur.fetchone()
    if not contact:
        raise HTTPException(status_code=404, detail="Contacto não encontrado")
    return contact


@app.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM contacts WHERE id = %s AND company_id = %s", (contact_id, company_id))
            conn.commit()


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

def _require_contact(cur, contact_id: int, company_id: int) -> None:
    cur.execute("SELECT 1 FROM contacts WHERE id = %s AND company_id = %s", (contact_id, company_id))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Contacto não encontrado")


@app.post("/contacts/{contact_id}/attachments", status_code=201)
def upload_attachment(
    contact_id: int,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
    company_id: int = Depends(get_current_company),
):
    data = file.file.read()
    object_name = f"{contact_id}/{uuid.uuid4()}_{file.filename}"

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_contact(cur, contact_id, company_id)
            upload_bytes(data, object_name, file.content_type or "application/octet-stream")
            cur.execute(
                """INSERT INTO attachments (contact_id, filename, object_name, content_type, company_id)
                   VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                (contact_id, file.filename, object_name, file.content_type, company_id),
            )
            conn.commit()
            return cur.fetchone()


@app.get("/contacts/{contact_id}/attachments")
def list_attachments(contact_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_contact(cur, contact_id, company_id)
            cur.execute(
                "SELECT * FROM attachments WHERE contact_id = %s AND company_id = %s ORDER BY created_at DESC",
                (contact_id, company_id),
            )
            attachments = cur.fetchall()
    for att in attachments:
        att["url"] = get_presigned_url(att["object_name"])
    return attachments