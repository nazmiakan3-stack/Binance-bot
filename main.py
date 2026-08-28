#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
import os
import subprocess
import atexit
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# ============================================================
# TELEGRAM BİLGİLERİ (Render Environment Variables)
# ============================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8680932537:AAFwFy1EjZvKnrxYei8tmb4FQQbXlQ8Fuo8")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1734551753")

BASE_URLS = [
    "https://fapi.binance.com/fapi/v1/klines",
    "https://fapi1.binance.com/fapi/v1/klines",
    "https://fapi2.binance.com/fapi/v1/klines"
]

SYMBOLS = {
    "XAUUSDT": "XAU", "XAGUSDT": "XAG", "BTCUSDT": "BTC",
    "ETHUSDT": "ETH", "SOLUSDT": "SOL", "BNBUSDT": "BNB",
    "XRPUSDT": "XRP", "ADAUSDT": "ADA", "AVAXUSDT": "AVAX",
    "LINKUSDT": "LINK", "DOGEUSDT": "DOGE"
}

TIMEFRAME = "15m"
LIMIT = 100
LOOP_SECONDS = 60

STARTING_BALANCE_PER_COIN = 30.0
MARGIN_PER_TRADE = 25.0
LEVERAGE = 10.0
POSITION_SIZE = MARGIN_PER_TRADE * LEVERAGE
TAKE_PROFIT_PCT = 0.02
STOP_LOSS_PCT = 0.05
COMMISSION_RATE = 0.0004

STATE_FILE = "bot_state.json"
REQUEST_TIMEOUT = 10
RETRY_COUNT = 3
TELEGRAM_NOTIFY_INTERVAL = 15 * 60
TURKEY_TZ = timezone(timedelta(hours=3))

def send_telegram_msg(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[HATA] Telegram Token veya Chat ID bulunamadı!")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"<pre>{message}</pre>",
        "parse_mode": "HTML"
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    req = Request(url, data=payload, headers=headers, method="POST")

    for attempt in range(RETRY_COUNT):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    return True
        except Exception as e:
            print(f"[Telegram Hatası] Deneme {attempt+1}: {e}")
            time.sleep(1)

    return False

# (Geri kalan analiz ve Web Server kodlarınız...)
