import io
import os
from datetime import timedelta

from minio import Minio

BUCKET_NAME = "projects-attachments"


def get_minio_client() -> Minio:
    return Minio(
        os.environ.get("MINIO_INTERNAL_ENDPOINT", "minio:9000"),
        access_key=os.environ["MINIO_ROOT_USER"],
        secret_key=os.environ["MINIO_ROOT_PASSWORD"],
        secure=False,
    )


def get_public_minio_client() -> Minio:
    # URLs pré-assinadas são seguidas directamente pelo browser do utilizador,
    # que não resolve o hostname interno "minio" da rede Docker; a região é
    # fixada para evitar que o SDK tente contactar esse endpoint a partir
    # do próprio container só para descobrir a região do bucket.
    #
    # Em produção, MINIO_PUBLIC_ENDPOINT tem de ser um host alcançável pelo
    # browser do utilizador final (ex: "marksuite.ao"), NUNCA "localhost" —
    # "localhost:9000" resolve para a própria máquina do utilizador, não para
    # o servidor.
    secure = os.environ.get("MINIO_PUBLIC_SECURE", "false").lower() == "true"
    return Minio(
        os.environ.get("MINIO_PUBLIC_ENDPOINT", "localhost:9000"),
        access_key=os.environ["MINIO_ROOT_USER"],
        secret_key=os.environ["MINIO_ROOT_PASSWORD"],
        secure=secure,
        region="us-east-1",
    )


def ensure_bucket():
    client = get_minio_client()
    if not client.bucket_exists(BUCKET_NAME):
        client.make_bucket(BUCKET_NAME)


def upload_bytes(data: bytes, object_name: str, content_type: str = "application/octet-stream"):
    client = get_minio_client()
    client.put_object(
        BUCKET_NAME,
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def delete_object(object_name: str):
    get_minio_client().remove_object(BUCKET_NAME, object_name)


def get_presigned_url(object_name: str) -> str:
    client = get_public_minio_client()
    return client.presigned_get_object(BUCKET_NAME, object_name, expires=timedelta(hours=1))
