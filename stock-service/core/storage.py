import os
import io
from minio import Minio

BUCKET = "stock-anexos"


def get_minio_client():
    return Minio(
        os.getenv("MINIO_INTERNAL_ENDPOINT", "minio:9000"),
        access_key=os.environ["MINIO_ROOT_USER"],
        secret_key=os.environ["MINIO_ROOT_PASSWORD"],
        secure=os.environ.get("MINIO_INTERNAL_SECURE", "false").lower() == "true",
    )


minio_client = get_minio_client()


def ensure_bucket():
    if not minio_client.bucket_exists(BUCKET):
        minio_client.make_bucket(BUCKET)


def upload_bytes(data: bytes, object_name: str, content_type="application/octet-stream"):
    minio_client.put_object(
        BUCKET,
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def delete_object(object_name: str):
    minio_client.remove_object(BUCKET, object_name)
