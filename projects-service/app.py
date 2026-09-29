# import uuid

# from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.security import OAuth2PasswordBearer
# from jose import JWTError, jwt
# from psycopg2.extras import RealDictCursor
# from pydantic import BaseModel
# import os

# from core.db import get_connection, init_db
# from core.tools import ensure_bucket, delete_object, get_presigned_url, upload_bytes

# app = FastAPI(title="Projects Service", version="1.0.0")

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=os.environ.get(
#         "CORS_ORIGINS",
#         "http://localhost,http://localhost:80,http://127.0.0.1,http://localhost:5002",
#     ).split(","),
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# JWT_SECRET    = os.environ["JWT_SECRET"]
# JWT_ALGORITHM = "HS256"
# oauth2_scheme = OAuth2PasswordBearer(tokenUrl="http://localhost:5000/login")


# @app.on_event("startup")
# def on_startup():
#     init_db()
#     ensure_bucket()


# # ---------------------------------------------------------------------------
# # Verificação JWT (partilha blocklist com auth-service via mesma DB)
# # ---------------------------------------------------------------------------

# def get_current_claims(token: str = Depends(oauth2_scheme)) -> dict:
#     try:
#         payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
#     except JWTError:
#         raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

#     if payload.get("type") != "access":
#         raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token de acesso inválido")

#     jti = payload.get("jti")
#     if jti:
#         with get_connection() as conn:
#             with conn.cursor() as cur:
#                 cur.execute(
#                     "SELECT jti FROM jwt_blocklist WHERE jti = %s AND expires_at > NOW()",
#                     (jti,),
#                 )
#                 if cur.fetchone():
#                     raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revogado")

#     return payload


# def get_current_user(claims: dict = Depends(get_current_claims)) -> str:
#     return claims["sub"]


# def get_current_company(claims: dict = Depends(get_current_claims)) -> int:
#     """Isolamento entre empresas: toda a leitura/escrita de projectos e tarefas tem
#     de ser filtrada por company_id — nunca basta um JWT válido, seja lá de que
#     empresa for."""
#     company_id = claims.get("company_id")
#     if company_id is None:
#         raise HTTPException(status_code=403, detail="Utilizador sem empresa associada")
#     return company_id


# # ---------------------------------------------------------------------------
# # MODELS
# # ---------------------------------------------------------------------------

# class ProjectCreate(BaseModel):
#     name: str
#     description: str | None = None
#     color: str = "#3B82F6"
#     status: str = "ativo"
#     due_date: str | None = None


# class ProjectUpdate(ProjectCreate):
#     pass


# class TaskCreate(BaseModel):
#     project_id: int
#     title: str
#     description: str | None = None
#     status: str = "todo"
#     priority: str = "media"
#     assignee_id: int | None = None
#     due_date: str | None = None


# class TaskUpdate(TaskCreate):
#     pass


# class TaskStatusUpdate(BaseModel):
#     status: str
#     position: int | None = None


# class TagCreate(BaseModel):
#     name: str
#     color: str = "#6B7280"


# class TaskTagsUpdate(BaseModel):
#     tag_ids: list[int]


# class CommentCreate(BaseModel):
#     body: str


# # ---------------------------------------------------------------------------
# # Projects
# # ---------------------------------------------------------------------------

# @app.get("/projects")
# def list_projects(user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute("SELECT * FROM projects WHERE company_id = %s ORDER BY created_at DESC", (company_id,))
#             return cur.fetchall()


# @app.post("/projects", status_code=201)
# def create_project(body: ProjectCreate, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute(
#                 """INSERT INTO projects (name, description, color, status, due_date, company_id)
#                    VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
#                 (body.name, body.description, body.color, body.status, body.due_date, company_id),
#             )
#             conn.commit()
#             return cur.fetchone()


# @app.get("/projects/{project_id}")
# def get_project(project_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute("SELECT * FROM projects WHERE id = %s AND company_id = %s", (project_id, company_id))
#             project = cur.fetchone()
#     if not project:
#         raise HTTPException(status_code=404, detail="Projecto não encontrado")
#     return project


# @app.put("/projects/{project_id}")
# def update_project(project_id: int, body: ProjectUpdate, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute(
#                 """UPDATE projects SET name=%s, description=%s, color=%s, status=%s, due_date=%s
#                    WHERE id=%s AND company_id=%s RETURNING *""",
#                 (body.name, body.description, body.color, body.status, body.due_date, project_id, company_id),
#             )
#             conn.commit()
#             project = cur.fetchone()
#     if not project:
#         raise HTTPException(status_code=404, detail="Projecto não encontrado")
#     return project


# @app.delete("/projects/{project_id}", status_code=204)
# def delete_project(project_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor() as cur:
#             cur.execute("DELETE FROM projects WHERE id = %s AND company_id = %s", (project_id, company_id))
#             conn.commit()


# # ---------------------------------------------------------------------------
# # Tasks
# # ---------------------------------------------------------------------------

# @app.get("/tasks")
# def list_tasks(project_id: int | None = None, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             if project_id:
#                 cur.execute(
#                     "SELECT * FROM tasks WHERE project_id = %s AND company_id = %s ORDER BY position, created_at DESC",
#                     (project_id, company_id),
#                 )
#             else:
#                 cur.execute(
#                     "SELECT * FROM tasks WHERE company_id = %s ORDER BY position, created_at DESC",
#                     (company_id,),
#                 )
#             tasks = cur.fetchall()

#             cur.execute("""
#                 SELECT tt.task_id, t.id, t.name, t.color
#                 FROM task_tags tt JOIN tags t ON t.id = tt.tag_id
#                 WHERE t.company_id = %s
#             """, (company_id,))
#             tag_rows = cur.fetchall()

#     tags_by_task = {}
#     for row in tag_rows:
#         tags_by_task.setdefault(row["task_id"], []).append(
#             {"id": row["id"], "name": row["name"], "color": row["color"]}
#         )
#     for t in tasks:
#         t["tags"] = tags_by_task.get(t["id"], [])
#     return tasks


# @app.get("/projects/{project_id}/tasks")
# def list_project_tasks(project_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     return list_tasks(project_id=project_id, user_id=user_id, company_id=company_id)


# def _require_project(cur, project_id: int, company_id: int) -> None:
#     cur.execute("SELECT 1 FROM projects WHERE id = %s AND company_id = %s", (project_id, company_id))
#     if not cur.fetchone():
#         raise HTTPException(status_code=404, detail="Projecto não encontrado")


# @app.post("/tasks", status_code=201)
# def create_task(body: TaskCreate, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             _require_project(cur, body.project_id, company_id)
#             cur.execute(
#                 """INSERT INTO tasks (project_id, title, description, status, priority, assignee_id, due_date, company_id)
#                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
#                 (
#                     body.project_id, body.title, body.description, body.status,
#                     body.priority, body.assignee_id, body.due_date, company_id,
#                 ),
#             )
#             conn.commit()
#             task = cur.fetchone()
#     task["tags"] = []
#     return task


# @app.get("/tasks/{task_id}")
# def get_task(task_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute("SELECT * FROM tasks WHERE id = %s AND company_id = %s", (task_id, company_id))
#             task = cur.fetchone()
#             if not task:
#                 raise HTTPException(status_code=404, detail="Tarefa não encontrada")
#             cur.execute("""
#                 SELECT t.id, t.name, t.color FROM task_tags tt
#                 JOIN tags t ON t.id = tt.tag_id WHERE tt.task_id = %s
#             """, (task_id,))
#             task["tags"] = cur.fetchall()
#     return task


# @app.put("/tasks/{task_id}")
# def update_task(task_id: int, body: TaskUpdate, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     completed_at_expr = "NOW()" if body.status == "done" else "NULL"
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             _require_project(cur, body.project_id, company_id)
#             cur.execute(
#                 f"""UPDATE tasks SET project_id=%s, title=%s, description=%s, status=%s,
#                        priority=%s, assignee_id=%s, due_date=%s, completed_at={completed_at_expr}
#                    WHERE id=%s AND company_id=%s RETURNING *""",
#                 (
#                     body.project_id, body.title, body.description, body.status,
#                     body.priority, body.assignee_id, body.due_date, task_id, company_id,
#                 ),
#             )
#             conn.commit()
#             task = cur.fetchone()
#             if not task:
#                 raise HTTPException(status_code=404, detail="Tarefa não encontrada")
#             cur.execute("""
#                 SELECT t.id, t.name, t.color FROM task_tags tt
#                 JOIN tags t ON t.id = tt.tag_id WHERE tt.task_id = %s
#             """, (task_id,))
#             task["tags"] = cur.fetchall()
#     return task


# @app.patch("/tasks/{task_id}/status")
# def update_task_status(task_id: int, body: TaskStatusUpdate, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     completed_at_expr = "NOW()" if body.status == "done" else "NULL"
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             if body.position is not None:
#                 cur.execute(
#                     f"UPDATE tasks SET status=%s, position=%s, completed_at={completed_at_expr} WHERE id=%s AND company_id=%s RETURNING *",
#                     (body.status, body.position, task_id, company_id),
#                 )
#             else:
#                 cur.execute(
#                     f"UPDATE tasks SET status=%s, completed_at={completed_at_expr} WHERE id=%s AND company_id=%s RETURNING *",
#                     (body.status, task_id, company_id),
#                 )
#             conn.commit()
#             task = cur.fetchone()
#     if not task:
#         raise HTTPException(status_code=404, detail="Tarefa não encontrada")
#     return task


# @app.delete("/tasks/{task_id}", status_code=204)
# def delete_task(task_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor() as cur:
#             cur.execute("DELETE FROM tasks WHERE id = %s AND company_id = %s", (task_id, company_id))
#             conn.commit()


# # ---------------------------------------------------------------------------
# # Tags
# # ---------------------------------------------------------------------------

# @app.get("/projects/{project_id}/tags")
# def list_tags(project_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute(
#                 "SELECT * FROM tags WHERE project_id = %s AND company_id = %s ORDER BY name",
#                 (project_id, company_id),
#             )
#             return cur.fetchall()


# @app.post("/projects/{project_id}/tags", status_code=201)
# def create_tag(project_id: int, body: TagCreate, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             _require_project(cur, project_id, company_id)
#             cur.execute(
#                 "INSERT INTO tags (project_id, name, color, company_id) VALUES (%s, %s, %s, %s) RETURNING *",
#                 (project_id, body.name, body.color, company_id),
#             )
#             conn.commit()
#             return cur.fetchone()


# @app.delete("/tags/{tag_id}", status_code=204)
# def delete_tag(tag_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor() as cur:
#             cur.execute("DELETE FROM tags WHERE id = %s AND company_id = %s", (tag_id, company_id))
#             conn.commit()


# @app.put("/tasks/{task_id}/tags")
# def set_task_tags(task_id: int, body: TaskTagsUpdate, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute("SELECT 1 FROM tasks WHERE id = %s AND company_id = %s", (task_id, company_id))
#             if not cur.fetchone():
#                 raise HTTPException(status_code=404, detail="Tarefa não encontrada")

#             for tag_id in set(body.tag_ids):
#                 cur.execute("SELECT 1 FROM tags WHERE id = %s AND company_id = %s", (tag_id, company_id))
#                 if not cur.fetchone():
#                     raise HTTPException(status_code=404, detail="Uma ou mais etiquetas não foram encontradas")

#             cur.execute("DELETE FROM task_tags WHERE task_id = %s", (task_id,))
#             for tag_id in body.tag_ids:
#                 cur.execute(
#                     "INSERT INTO task_tags (task_id, tag_id) VALUES (%s, %s)",
#                     (task_id, tag_id),
#                 )
#             conn.commit()
#             cur.execute("""
#                 SELECT t.id, t.name, t.color FROM task_tags tt
#                 JOIN tags t ON t.id = tt.tag_id WHERE tt.task_id = %s
#             """, (task_id,))
#             return cur.fetchall()


# # ---------------------------------------------------------------------------
# # Comments
# # ---------------------------------------------------------------------------

# @app.get("/tasks/{task_id}/comments")
# def list_comments(task_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute("SELECT 1 FROM tasks WHERE id = %s AND company_id = %s", (task_id, company_id))
#             if not cur.fetchone():
#                 raise HTTPException(status_code=404, detail="Tarefa não encontrada")
#             cur.execute(
#                 "SELECT * FROM task_comments WHERE task_id = %s ORDER BY created_at ASC",
#                 (task_id,),
#             )
#             return cur.fetchall()


# @app.post("/tasks/{task_id}/comments", status_code=201)
# def create_comment(task_id: int, body: CommentCreate, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute("SELECT 1 FROM tasks WHERE id = %s AND company_id = %s", (task_id, company_id))
#             if not cur.fetchone():
#                 raise HTTPException(status_code=404, detail="Tarefa não encontrada")

#             cur.execute("SELECT nome FROM usuarios WHERE id = %s", (user_id,))
#             user_row = cur.fetchone()
#             author_name = user_row["nome"] if user_row else "Utilizador"

#             cur.execute(
#                 """INSERT INTO task_comments (task_id, author_id, author_name, body, company_id)
#                    VALUES (%s, %s, %s, %s, %s) RETURNING *""",
#                 (task_id, user_id, author_name, body.body, company_id),
#             )
#             conn.commit()
#             return cur.fetchone()


# # ---------------------------------------------------------------------------
# # Attachments
# # ---------------------------------------------------------------------------

# @app.post("/tasks/{task_id}/attachments", status_code=201)
# def upload_attachment(
#     task_id: int,
#     file: UploadFile = File(...),
#     user_id: str = Depends(get_current_user),
#     company_id: int = Depends(get_current_company),
# ):
#     data = file.file.read()
#     object_name = f"{task_id}/{uuid.uuid4()}_{file.filename}"

#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute("SELECT 1 FROM tasks WHERE id = %s AND company_id = %s", (task_id, company_id))
#             if not cur.fetchone():
#                 raise HTTPException(status_code=404, detail="Tarefa não encontrada")
#             upload_bytes(data, object_name, file.content_type or "application/octet-stream")
#             cur.execute(
#                 """INSERT INTO task_attachments (task_id, filename, object_name, content_type, company_id)
#                    VALUES (%s, %s, %s, %s, %s) RETURNING *""",
#                 (task_id, file.filename, object_name, file.content_type, company_id),
#             )
#             conn.commit()
#             return cur.fetchone()


# @app.get("/tasks/{task_id}/attachments")
# def list_attachments(task_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute("SELECT 1 FROM tasks WHERE id = %s AND company_id = %s", (task_id, company_id))
#             if not cur.fetchone():
#                 raise HTTPException(status_code=404, detail="Tarefa não encontrada")
#             cur.execute(
#                 "SELECT * FROM task_attachments WHERE task_id = %s ORDER BY created_at DESC",
#                 (task_id,),
#             )
#             attachments = cur.fetchall()
#     for att in attachments:
#         att["url"] = get_presigned_url(att["object_name"])
#     return attachments


# @app.delete("/attachments/{attachment_id}", status_code=204)
# def delete_attachment(attachment_id: int, user_id: str = Depends(get_current_user), company_id: int = Depends(get_current_company)):
#     with get_connection() as conn:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute(
#                 "SELECT object_name FROM task_attachments WHERE id = %s AND company_id = %s",
#                 (attachment_id, company_id),
#             )
#             att = cur.fetchone()
#             if not att:
#                 raise HTTPException(status_code=404, detail="Anexo não encontrado")
#             try:
#                 delete_object(att["object_name"])
#             except Exception:
#                 pass
#             cur.execute("DELETE FROM task_attachments WHERE id = %s AND company_id = %s", (attachment_id, company_id))
#             conn.commit()
