FROM python:3.12-slim

WORKDIR /app

# dependências Python
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# copiar código
COPY . .

# porta do serviço
EXPOSE 5004

# execução do FastAPI (mesmo padrão dos outros microserviços)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5004"]
