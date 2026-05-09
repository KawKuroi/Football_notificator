FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    TZ=America/Bogota

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY football_API.py .

# One-shot: arranca, corre el pipeline, sale.
# Diseñado para Cloud Run Jobs disparado por Cloud Scheduler (o cron equivalente).
CMD ["python", "football_API.py"]
