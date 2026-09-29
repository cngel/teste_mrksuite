print("Servidor a iniciar", flush=True)
from pydantic import BaseModel, ConfigDict, Field
from fastapi import FastAPI, BackgroundTasks
from typing import Optional
from datetime import datetime
from core.db import get_connection
from psycopg2.extras import RealDictCursor
import psycopg2
import re

app = FastAPI(title="WhatsApp Service", version="1.0.0")

class WebHook(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

class Contact(WebHook):
    name: Optional[str] = Field(default=None, alias="Name")
    phone_number: Optional[str] = Field(default=None, alias="PhoneNumber")
    id: str = Field(alias="Id")

class LastMessage(WebHook):
    content: Optional[str] = Field(default=None, alias="Content")
    message_type: Optional[str] = Field(default=None, alias="MessageType")
    source: Optional[str] = Field(default=None, alias="Source")
    event_at_utc: Optional[datetime] = Field(default=None, alias="EventAtUTC")
    id: str = Field(alias="Id")

class ChatContent(WebHook):
    id: str = Field(alias="Id")
    contact: Contact = Field(alias="Contact")
    last_message: Optional[LastMessage] = Field(default=None, alias="LastMessage")
    total_unread: Optional[int] = Field(default=None, alias="TotalUnread")
    open: Optional[bool] = Field(default=None, alias="Open")
    waiting: Optional[bool] = Field(default=None, alias="Waiting")
    event_at_utc: Optional[datetime] = Field(default=None, alias="EventAtUTC")

class Payload(WebHook):
    type: Optional[str] = Field(default=None, alias="Type")
    content: ChatContent = Field(alias="Content")

class ChatWebhookEvent(WebHook):
    type: Optional[str] = Field(default=None, alias="Type")
    event_id: Optional[str] = Field(default=None, alias="EventId")
    event_date: Optional[datetime] = Field(default=None, alias="EventDate")
    payload: Payload = Field(alias="Payload")


def router(phone, message):

    try:

        phone = re.sub(r'^\+244', '', phone)

        if message == "crm":
            with get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT company_id FROM usuarios WHERE whatsapp = %s LIMIT 1", (phone,))
                    user = cur.fetchone()

                    if not user:
                        print("company_id não encontrado", flush=True)

                    company_id = user["company_id"]

                    cur.execute("SELECT name, email, phone, company, notes, " \
                    "stage, pipeline_value, channel, owner, service_type FROM " \
                    "contacts WHERE company_id = %s ORDER BY created_at DESC", (company_id,))

                    contacts = cur.fetchall()

                    for contact in contacts:
                        print(contact, flush=True)

        elif re.fullmatch(r"crm/\d+", message.lower()) or re.fullmatch(r"^crm/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}$", message.lower()):
            print("searching...", flush=True)

            index = message[4:]

            with get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:

                    cur.execute("SELECT company_id FROM usuarios WHERE whatsapp = %s LIMIT 1", (phone,))
                    user = cur.fetchone()

                    if not user:
                        print("company_id não encontrado", flush=True)

                    company_id = user["company_id"]

                    print(f"Company ID: {company_id}")

                    cur.execute("SELECT name, email, phone, company, notes, " \
                    "stage, pipeline_value, channel, owner, service_type FROM " \
                    "contacts WHERE company_id = %s AND (phone = %s or email = %s) LIMIT 1", (company_id,index,index))

                    contact = cur.fetchone()

                    print(contact, flush=True)

        elif message.lower().startswith("crm/criar/"):
            print("bora lá", flush=True)

            partes = message[len("crm/criar/"):].split("/")

            if len(partes) % 2 != 0:
                print("Formato inválido", flush=True)
            else:
                dados = dict(zip(partes[::2], partes[1::2]))

            campos_permitidos = {
                "nome",
                "email",
                "telefone",
                "empresa",
                "serviço",
                "valor",
                "data",
                "canal",
                "estado",
                "notas"
            }

            if not set(dados).issubset(campos_permitidos):
                print("Existe um campo inválido")

            with get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:

                    cur.execute("SELECT company_id FROM usuarios WHERE whatsapp = %s LIMIT 1", (phone,))
                    user = cur.fetchone()

                    if not user:
                        print("company_id não encontrado", flush=True)

                    company_id = user["company_id"]

                    print(f"Company ID: {company_id}")

                    MAP_COLUMNS = {
                        "name":"nome",
                        "email":"email",
                        "phone":"telefone",
                        "company":"empresa",
                        "notes":"notas",
                        "stage":"estado",
                        "channel":"canal",
                        "service_type":"serviço",
                        "lead_date":"data"
                    }

                    colunas_sql = []
                    valores = []

                    for chave_dict, coluna_db in MAP_COLUMNS.items():
                        if chave_dict in dados and dados[chave_dict] is not None:

                            colunas_sql.append(psycopg2.sql.Identifier(coluna_db))

                            # Trata os fallbacks diretamente na extração do valor
                            val = dados[chave_dict]
                            if chave_dict in ("canal","serviço") and not val:
                                val = ""
                            valores.append(val)

                    # Adiciona o campo fixo da empresa/organização se estiver fora do dict 'dados'
                    colunas_sql.append(psycopg2.sql.Indentifier("company_id"))
                    valores.append(company_id)

                    # Construção da Query usando sql.SQL

                    querry = psycopg2.sql.SQL(
                        """
                        INSERT INTO {tabela} ({colunas}) 
                        VALUES ({placeholders})
                        """
                    ).format(
                        tabela = psycopg2.sql.Identifier("contacts"),
                        colunas = psycopg2.sql.SQL(", ").join(colunas_sql),
                        placeholders = psycopg2.sql.SQL(", ").join(
                            [psycopg2.sql.SQL("%s")] * len(valores)
                        ), # Gera exatamente o número de %s necessário
                    )

                    cur.execute(querry, valores)
                    conn.commit()

                    print("Cadastrado")
    except Exception as e:
        return e

    return True


@app.post("/marksuite-webhook", status_code=200)
def webhook(body:ChatWebhookEvent, background_tasks: BackgroundTasks):

    background_tasks.add_task(router,body.payload.content.contact.phone_number, body.payload.content.last_message.content)

    return {"status":"proccessing"}