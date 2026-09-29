import os

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PASSWORD_PEPPER", "test-pepper")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("MINIO_ROOT_USER", "test")
os.environ.setdefault("MINIO_ROOT_PASSWORD", "test")