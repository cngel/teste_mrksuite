import os
from datetime import timedelta
from minio import Minio

BUCKET = "rh-anexos"


def get_public_minio_client(host: str | None = None):
    # URLs pré-assinadas são seguidas directamente pelo browser do utilizador,
    # que não resolve o hostname interno "minio" da rede Docker; a região é
    # fixada para evitar que o SDK tente contactar esse endpoint a partir do
    # próprio container só para descobrir a região do bucket.
    #
    # "host" é o hostname pelo qual o utilizador acedeu ao dashboard (vindo do
    # pedido HTTP em curso) — permite que as URLs funcionem tanto em localhost
    # como por IP de rede local ou domínio público, sem depender de um único
    # valor fixo em MINIO_PUBLIC_ENDPOINT (que só cobre o caso "localhost").
    endpoint = f"{host}:9000" if host else os.getenv("MINIO_PUBLIC_ENDPOINT", "localhost:9000")
    secure = os.getenv("MINIO_PUBLIC_SECURE", "false").lower() == "true"
    return Minio(
        endpoint,
        access_key=os.environ["MINIO_ROOT_USER"],
        secret_key=os.environ["MINIO_ROOT_PASSWORD"],
        secure=secure,
        region="us-east-1",
    )


def get_presigned_url(object_name: str, host: str | None = None):
    return get_public_minio_client(host).presigned_get_object(
        BUCKET,
        object_name,
        expires=timedelta(hours=1),
    )