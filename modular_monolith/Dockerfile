FROM python:3.12-slim
WORKDIR /app
COPY modular_monolith/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY modular_monolith ./modular_monolith
COPY auth-service ./auth-service
COPY crm-service ./crm-service
COPY rh-service ./rh-service
COPY documents-service ./documents-service
COPY accounting-service ./accounting-service
COPY stock-service ./stock-service
COPY web-service ./web-service
EXPOSE 10000
CMD ["sh", "-c", "uvicorn modular_monolith.app:app --host 0.0.0.0 --port ${PORT:-5002}"]