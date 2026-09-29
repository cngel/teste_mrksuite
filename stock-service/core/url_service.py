import os
from datetime import timedelta
from minio import Minio

BUCKET = "stock-anexos"


def get_public_minio_client():
    # URLs pré-assinadas são seguidas directamente pelo browser do utilizador,
    # que não resolve o hostname interno "minio" da rede Docker; a região é
    # fixada para evitar que o SDK tente contactar esse endpoint a partir do
    # próprio container só para descobrir a região do bucket.
    secure = os.getenv("MINIO_PUBLIC_SECURE", "false").lower() == "true"
    return Minio(
        os.getenv("MINIO_PUBLIC_ENDPOINT", "localhost:9000"),
        access_key=os.environ["MINIO_ROOT_USER"],
        secret_key=os.environ["MINIO_ROOT_PASSWORD"],
        secure=secure,
        region="us-east-1",
    )


def get_presigned_url(object_name: str):
    return get_public_minio_client().presigned_get_object(
        BUCKET,
        object_name,
        expires=timedelta(hours=1),
    )
