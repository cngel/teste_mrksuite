FROM python:3.12-slim

WORKDIR /app

# dependências Python
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# copiar código
COPY . .

# porta do serviço (ajusta se estiver diferente no app.py)
EXPOSE 5003

# execução do FastAPI (mesmo padrão do CRM)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5003"]