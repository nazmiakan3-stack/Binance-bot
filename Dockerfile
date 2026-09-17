# Metal 15m Simulator - Binance Futures (XAUUSDT + XAGUSDT)
# Sanal işlem botu - Gerçek emir YOK

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TELEGRAM_BOT_TOKEN="" \
    TELEGRAM_CHAT_ID=""

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY main.py .
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

VOLUME ["/app/logs"]

CMD ["python", "main.py"]
