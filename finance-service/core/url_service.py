import os
from datetime import timedelta
from minio import Minio

BUCKET = "finance-anexos"


def get_public_minio_client():
    # URLs pré-assinadas são seguidas directamente pelo browser do utilizador,
    # que não resolve o hostname interno "minio" da rede Docker; a região é
    # fixada para evitar que o SDK tente contactar esse endpoint a partir do
    # próprio container só para descobrir a região do bucket.
    #
    # Em produção, MINIO_PUBLIC_ENDPOINT tem de ser um host alcançável pelo
    # browser do utilizador final (ex: "marksuite.ao"), NUNCA "localhost" —
    # "localhost:9000" resolve para a própria máquina do utilizador, não para
    # o servidor.
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
