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
# TELEGRAM (Render Environment Variable veya Varsayılan)
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8680932537:AAEWtIILsVYRsCsJdIFwBzv87QzeSmPJvkI")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1734551753")


# ============================================================
# BINANCE API
# ============================================================

BASE_URLS = [
    "https://fapi.binance.com/fapi/v1/klines",
    "https://fapi1.binance.com/fapi/v1/klines",
    "https://fapi2.binance.com/fapi/v1/klines"
]


# ============================================================
# COINLER
# ============================================================

SYMBOLS = {
    "XAUUSDT": "XAU",
    "XAGUSDT": "XAG",
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
    "BNBUSDT": "BNB",
    "XRPUSDT": "XRP",
    "ADAUSDT": "ADA",
    "AVAXUSDT": "AVAX",
    "LINKUSDT": "LINK",
    "DOGEUSDT": "DOGE",
}


# ============================================================
# ANALİZ AYARLARI
# ============================================================

TIMEFRAME = "15m"
LIMIT = 100
LOOP_SECONDS = 60


# ============================================================
# SANAL İŞLEM AYARLARI
# ============================================================

STARTING_BALANCE_PER_COIN = 30.0
MARGIN_PER_TRADE = 25.0
LEVERAGE = 10.0
POSITION_SIZE = MARGIN_PER_TRADE * LEVERAGE
TAKE_PROFIT_PCT = 0.02
STOP_LOSS_PCT = 0.05
COMMISSION_RATE = 0.0004


# ============================================================
# SİSTEM
# ============================================================

STATE_FILE = "bot_state.json"
REQUEST_TIMEOUT = 10
RETRY_COUNT = 3
TELEGRAM_NOTIFY_INTERVAL = 15 * 60
TURKEY_TZ = timezone(timedelta(hours=3))


def acquire_wake_lock():
    try:
        subprocess.run(["termux-wake-lock"], check=False)
    except Exception:
        pass

def release_wake_lock():
    try:
        subprocess.run(["termux-wake-unlock"], check=False)
    except Exception:
        pass

acquire_wake_lock()
atexit.register(release_wake_lock)


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def now_date_text():
    return datetime.now(TURKEY_TZ).strftime("%d.%m.%Y %H:%M:%S")


def save_state(positions, wallet_balances, realized_pnl, trade_number):
    state = {
        "positions": positions,
        "wallet_balances": wallet_balances,
        "realized_pnl": realized_pnl,
        "trade_number": trade_number,
        "last_save": now_date_text()
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Durum kaydedilemedi: {e}")

def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================
# TELEGRAM BİLDİRİMİ
# ============================================================

def send_telegram_msg(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram Token/Chat ID tanımlı değil.")
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

    for _ in range(RETRY_COUNT):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return response.status == 200
        except Exception:
            time.sleep(1)

    return False


def http_get_json(url):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

def get_klines(symbol):
    for base_url in BASE_URLS:
        url = f"{base_url}?symbol={symbol}&interval={TIMEFRAME}&limit={LIMIT}"
        data = http_get_json(url)
        if data and isinstance(data, list) and len(data) > 0:
            return data
    return None

def calc_ema(data, period):
    if len(data) < period:
        return []
    sma = sum(data[:period]) / period
    ema = [sma]
    k = 2 / (period + 1)
    for price in data[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def calc_atr(highs, lows, closes, period=20):
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    if len(trs) < period:
        return 0.0
    return sum(trs[-period:]) / period

def calc_rsi(closes, period=14):
    if len(closes) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def analyze(symbol):
    data = get_klines(symbol)
    if not data or len(data) < 50:
        return (symbol, None, None, None)

    closed = data[:-1]
    closes = [float(row[4]) for row in closed]
    highs = [float(row[2]) for row in closed]
    lows = [float(row[3]) for row in closed]

    price = closes[-1]
    ema = calc_ema(closes, 20)
    atr = calc_atr(highs, lows, closes, 20)

    if not ema or atr == 0:
        return (symbol, None, None, None)

    kc_lower = ema[-1] - atr * 2
    kc_upper = ema[-1] + atr * 2
    rsi = calc_rsi(closes, 14)

    signal = None
    if price < kc_lower and rsi <= 25:
        signal = "LONG"
    elif price > kc_upper and rsi >= 75:
        signal = "SHORT"

    return (symbol, signal, price, rsi)


# ============================================================
# HEALTH CHECK (Render'ın Aktif Kalması İçin)
# ============================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Active")

    def log_message(self, format, *args):
        return

def run_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


# ============================================================
# MAIN LOOP
# ============================================================

def main():
    threading.Thread(target=run_health_check_server, daemon=True).start()

    state = load_state()
    if state:
        positions = state.get("positions", {s: None for s in SYMBOLS})
        wallet_balances = state.get("wallet_balances", {s: STARTING_BALANCE_PER_COIN for s in SYMBOLS})
        realized_pnl = state.get("realized_pnl", {s: 0.0 for s in SYMBOLS})
        trade_number = state.get("trade_number", 0)
    else:
        positions = {s: None for s in SYMBOLS}
        wallet_balances = {s: STARTING_BALANCE_PER_COIN for s in SYMBOLS}
        realized_pnl = {s: 0.0 for s in SYMBOLS}
        trade_number = 0

    print("\n========================================\n       10X SANAL KELTNER BOT\n========================================")
    
    send_telegram_msg(f"🚀 BOT BAŞLATILDI!\nTarih: {now_date_text()}\nSistem Render üzerinde aktifleştirildi.")

    last_telegram_time = 0

    while True:
        try:
            lines = []
            trade_events = []
            total_unrealized_pnl = 0.0

            lines.append("╔══════════════════════════════════════╗")
            lines.append("       10X SANAL KELTNER RAPORU")
            lines.append(f" Tarih: {now_date_text()}")
            lines.append(f" Kaldıraç: {LEVERAGE:.0f}x | Teminat: {MARGIN_PER_TRADE:.2f} USDT")
            lines.append("╚══════════════════════════════════════╝\n")
            lines.append("COIN | FİYAT     | DURUM | CÜZDAN | K/Z")
            lines.append("─────────────────────────────────────────")

            with ThreadPoolExecutor(max_workers=5) as executor:
                results = list(executor.map(analyze, SYMBOLS.keys()))

            analysis_dict = {r[0]: r[1:] for r in results}

            for symbol, name in SYMBOLS.items():
                signal, current_price, rsi = analysis_dict.get(symbol, (None, None, None))
                wallet = wallet_balances.get(symbol, STARTING_BALANCE_PER_COIN)

                if current_price is None:
                    lines.append(f"{name:<4} | {'N/A':<9} | BOŞ   | {wallet:6.2f} |  0.00")
                    continue

                pos = positions.get(symbol)
                unrealized_pnl = 0.0
                status_code = "BOŞ"

                # POZİSYON AÇ
                if pos is None and signal in ("LONG", "SHORT") and wallet >= MARGIN_PER_TRADE:
                    trade_number += 1
                    if signal == "LONG":
                        tp = current_price * (1 + TAKE_PROFIT_PCT)
                        sl = current_price * (1 - STOP_LOSS_PCT)
                    else:
                        tp = current_price * (1 - TAKE_PROFIT_PCT)
                        sl = current_price * (1 + STOP_LOSS_PCT)

                    wallet_balances[symbol] = wallet - MARGIN_PER_TRADE
                    positions[symbol] = {
                        "id": trade_number, "side": signal, "entry": current_price,
                        "tp": tp, "sl": sl, "margin": MARGIN_PER_TRADE,
                        "leverage": LEVERAGE, "position_size": POSITION_SIZE
                    }
                    pos = positions[symbol]

                    trade_events.append(
                        f"🚨 <b>YENİ POZİSYON</b>\n"
                        f"Coin: {name} | Yön: {signal}\n"
                        f"Giriş: {current_price:.6f}\n"
                        f"TP: {tp:.6f} | SL: {sl:.6f}"
                    )

                # AÇIK POZİSYON KONTROL
                if pos is not None:
                    side, entry = pos["side"], float(pos["entry"])
                    pct = (current_price - entry) / entry if side == "LONG" else (entry - current_price) / entry
                    gross_pnl = POSITION_SIZE * pct
                    commission = POSITION_SIZE * COMMISSION_RATE
                    unrealized_pnl = gross_pnl - commission
                    total_unrealized_pnl += unrealized_pnl

                    hit_tp = (side == "LONG" and current_price >= pos["tp"]) or (side == "SHORT" and current_price <= pos["tp"])
                    hit_sl = (side == "LONG" and current_price <= pos["sl"]) or (side == "SHORT" and current_price >= pos["sl"])

                    if hit_tp or hit_sl:
                        wallet_balances[symbol] += MARGIN_PER_TRADE + unrealized_pnl
                        realized_pnl[symbol] = realized_pnl.get(symbol, 0.0) + unrealized_pnl
                        positions[symbol] = None
                        status_code = "KAP"
                        res_text = "TAKE PROFIT" if hit_tp else "STOP LOSS"

                        trade_events.append(
                            f"✅ <b>POZİSYON KAPANDI</b>\n"
                            f"Coin: {name} | Sonuç: {res_text}\n"
                            f"P/L: {unrealized_pnl:+.2f} USDT\n"
                            f"Yeni Cüzdan: {wallet_balances[symbol]:.2f} USDT"
                        )
                    else:
                        status_code = "LNG" if side == "LONG" else "SHR"

                display_wallet = wallet_balances[symbol] + (MARGIN_PER_TRADE + unrealized_pnl if positions.get(symbol) else 0)
                price_str = f"{current_price:9.1f}" if current_price >= 1000 else (f"{current_price:9.2f}" if current_price >= 1 else f"{current_price:9.6f}")

                lines.append(f"{name:<4} | {price_str} | {status_code:<5} | {display_wallet:6.2f} | {unrealized_pnl:+6.2f}")

            total_cash = sum(wallet_balances.values())
            total_realized = sum(realized_pnl.values())
            total_equity = total_cash + sum(float(p["margin"]) for p in positions.values() if p) + total_unrealized_pnl

            lines.append("─────────────────────────────────────────")
            lines.append(f"AÇIK K/Z       : {total_unrealized_pnl:+8.2f} USDT")
            lines.append(f"REALİZE K/Z    : {total_realized:+8.2f} USDT")
            lines.append(f"TOPLAM VARLIK  : {total_equity:8.2f} USDT")

            output_text = "\n".join(lines)
            clear_screen()
            print(output_text)

            for event in trade_events:
                send_telegram_msg(event)

            now_ts = time.time()
            if now_ts - last_telegram_time >= TELEGRAM_NOTIFY_INTERVAL:
                send_telegram_msg(output_text)
                last_telegram_time = now_ts

            save_state(positions, wallet_balances, realized_pnl, trade_number)
            time.sleep(LOOP_SECONDS)

        except KeyboardInterrupt:
            print("\nBot kapatılıyor...")
            save_state(positions, wallet_balances, realized_pnl, trade_number)
            break
        except Exception as e:
            print(f"Hata oluştu: {e}")
            time.sleep(15)

if __name__ == "__main__":
    main()
# ============================================================
# SANAL İŞLEM AYARLARI
# ============================================================

STARTING_BALANCE_PER_COIN = 30.0
MARGIN_PER_TRADE = 25.0
LEVERAGE = 10.0
POSITION_SIZE = MARGIN_PER_TRADE * LEVERAGE
TAKE_PROFIT_PCT = 0.02
STOP_LOSS_PCT = 0.05
COMMISSION_RATE = 0.0004


# ============================================================
# SİSTEM
# ============================================================

STATE_FILE = "bot_state.json"
REQUEST_TIMEOUT = 10
RETRY_COUNT = 3
TELEGRAM_NOTIFY_INTERVAL = 15 * 60
TURKEY_TZ = timezone(timedelta(hours=3))


def acquire_wake_lock():
    try:
        subprocess.run(["termux-wake-lock"], check=False)
    except Exception:
        pass

def release_wake_lock():
    try:
        subprocess.run(["termux-wake-unlock"], check=False)
    except Exception:
        pass

acquire_wake_lock()
atexit.register(release_wake_lock)


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def now_date_text():
    return datetime.now(TURKEY_TZ).strftime("%d.%m.%Y %H:%M:%S")


def save_state(positions, wallet_balances, realized_pnl, trade_number):
    state = {
        "positions": positions,
        "wallet_balances": wallet_balances,
        "realized_pnl": realized_pnl,
        "trade_number": trade_number,
        "last_save": now_date_text()
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Durum kaydedilemedi: {e}")

def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================
# TELEGRAM BİLDİRİMİ
# ============================================================

def send_telegram_msg(message):
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "BURAYA_BOT_TOKENINI_YAZABILIRSIN":
        print("Telegram Token tanımlı değil, bildirim gönderilmedi.")
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

    for _ in range(RETRY_COUNT):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return response.status == 200
        except Exception:
            time.sleep(1)

    return False


def http_get_json(url):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

def get_klines(symbol):
    for base_url in BASE_URLS:
        url = f"{base_url}?symbol={symbol}&interval={TIMEFRAME}&limit={LIMIT}"
        data = http_get_json(url)
        if data and isinstance(data, list) and len(data) > 0:
            return data
    return None

def calc_ema(data, period):
    if len(data) < period:
        return []
    sma = sum(data[:period]) / period
    ema = [sma]
    k = 2 / (period + 1)
    for price in data[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def calc_atr(highs, lows, closes, period=20):
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    if len(trs) < period:
        return 0.0
    return sum(trs[-period:]) / period

def calc_rsi(closes, period=14):
    if len(closes) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def analyze(symbol):
    data = get_klines(symbol)
    if not data or len(data) < 50:
        return (symbol, None, None, None)

    closed = data[:-1]
    closes = [float(row[4]) for row in closed]
    highs = [float(row[2]) for row in closed]
    lows = [float(row[3]) for row in closed]

    price = closes[-1]
    ema = calc_ema(closes, 20)
    atr = calc_atr(highs, lows, closes, 20)

    if not ema or atr == 0:
        return (symbol, None, None, None)

    kc_lower = ema[-1] - atr * 2
    kc_upper = ema[-1] + atr * 2
    rsi = calc_rsi(closes, 14)

    signal = None
    if price < kc_lower and rsi <= 25:
        signal = "LONG"
    elif price > kc_upper and rsi >= 75:
        signal = "SHORT"

    return (symbol, signal, price, rsi)


# ============================================================
# HEALTH CHECK (Render'ın Aktif Kalması İçin)
# ============================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Active")

    def log_message(self, format, *args):
        return

def run_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


# ============================================================
# MAIN LOOP
# ============================================================

def main():
    threading.Thread(target=run_health_check_server, daemon=True).start()

    state = load_state()
    if state:
        positions = state.get("positions", {s: None for s in SYMBOLS})
        wallet_balances = state.get("wallet_balances", {s: STARTING_BALANCE_PER_COIN for s in SYMBOLS})
        realized_pnl = state.get("realized_pnl", {s: 0.0 for s in SYMBOLS})
        trade_number = state.get("trade_number", 0)
    else:
        positions = {s: None for s in SYMBOLS}
        wallet_balances = {s: STARTING_BALANCE_PER_COIN for s in SYMBOLS}
        realized_pnl = {s: 0.0 for s in SYMBOLS}
        trade_number = 0

    print("\n========================================\n       10X SANAL KELTNER BOT\n========================================")
    
    # Başlangıçta Telegram'a Botun Çalıştığına Dair Açılış Mesajı At
    send_telegram_msg(f"🚀 BOT BAŞATILDI!\nTarih: {now_date_text()}\nSistem Render üzerinde aktifleştirildi.")

    last_telegram_time = 0

    while True:
        try:
            lines = []
            trade_events = []
            total_unrealized_pnl = 0.0

            lines.append("╔══════════════════════════════════════╗")
            lines.append("       10X SANAL KELTNER RAPORU")
            lines.append(f" Tarih: {now_date_text()}")
            lines.append(f" Kaldıraç: {LEVERAGE:.0f}x | Teminat: {MARGIN_PER_TRADE:.2f} USDT")
            lines.append("╚══════════════════════════════════════╝\n")
            lines.append("COIN | FİYAT     | DURUM | CÜZDAN | K/Z")
            lines.append("─────────────────────────────────────────")

            with ThreadPoolExecutor(max_workers=5) as executor:
                results = list(executor.map(analyze, SYMBOLS.keys()))

            analysis_dict = {r[0]: r[1:] for r in results}

            for symbol, name in SYMBOLS.items():
                signal, current_price, rsi = analysis_dict.get(symbol, (None, None, None))
                wallet = wallet_balances.get(symbol, STARTING_BALANCE_PER_COIN)

                if current_price is None:
                    lines.append(f"{name:<4} | {'N/A':<9} | BOŞ   | {wallet:6.2f} |  0.00")
                    continue

                pos = positions.get(symbol)
                unrealized_pnl = 0.0
                status_code = "BOŞ"

                # POZİSYON AÇ
                if pos is None and signal in ("LONG", "SHORT") and wallet >= MARGIN_PER_TRADE:
                    trade_number += 1
                    if signal == "LONG":
                        tp = current_price * (1 + TAKE_PROFIT_PCT)
                        sl = current_price * (1 - STOP_LOSS_PCT)
                    else:
                        tp = current_price * (1 - TAKE_PROFIT_PCT)
                        sl = current_price * (1 + STOP_LOSS_PCT)

                    wallet_balances[symbol] = wallet - MARGIN_PER_TRADE
                    positions[symbol] = {
                        "id": trade_number, "side": signal, "entry": current_price,
                        "tp": tp, "sl": sl, "margin": MARGIN_PER_TRADE,
                        "leverage": LEVERAGE, "position_size": POSITION_SIZE
                    }
                    pos = positions[symbol]

                    trade_events.append(
                        f"🚨 <b>YENİ POZİSYON</b>\n"
                        f"Coin: {name} | Yön: {signal}\n"
                        f"Giriş: {current_price:.6f}\n"
                        f"TP: {tp:.6f} | SL: {sl:.6f}"
                    )

                # AÇIK POZİSYON KONTROL
                if pos is not None:
                    side, entry = pos["side"], float(pos["entry"])
                    pct = (current_price - entry) / entry if side == "LONG" else (entry - current_price) / entry
                    gross_pnl = POSITION_SIZE * pct
                    commission = POSITION_SIZE * COMMISSION_RATE
                    unrealized_pnl = gross_pnl - commission
                    total_unrealized_pnl += unrealized_pnl

                    hit_tp = (side == "LONG" and current_price >= pos["tp"]) or (side == "SHORT" and current_price <= pos["tp"])
                    hit_sl = (side == "LONG" and current_price <= pos["sl"]) or (side == "SHORT" and current_price >= pos["sl"])

                    if hit_tp or hit_sl:
                        wallet_balances[symbol] += MARGIN_PER_TRADE + unrealized_pnl
                        realized_pnl[symbol] = realized_pnl.get(symbol, 0.0) + unrealized_pnl
                        positions[symbol] = None
                        status_code = "KAP"
                        res_text = "TAKE PROFIT" if hit_tp else "STOP LOSS"

                        trade_events.append(
                            f"✅ <b>POZİSYON KAPANDI</b>\n"
                            f"Coin: {name} | Sonuç: {res_text}\n"
                            f"P/L: {unrealized_pnl:+.2f} USDT\n"
                            f"Yeni Cüzdan: {wallet_balances[symbol]:.2f} USDT"
                        )
                    else:
                        status_code = "LNG" if side == "LONG" else "SHR"

                display_wallet = wallet_balances[symbol] + (MARGIN_PER_TRADE + unrealized_pnl if positions.get(symbol) else 0)
                price_str = f"{current_price:9.1f}" if current_price >= 1000 else (f"{current_price:9.2f}" if current_price >= 1 else f"{current_price:9.6f}")

                lines.append(f"{name:<4} | {price_str} | {status_code:<5} | {display_wallet:6.2f} | {unrealized_pnl:+6.2f}")

            total_cash = sum(wallet_balances.values())
            total_realized = sum(realized_pnl.values())
            total_equity = total_cash + sum(float(p["margin"]) for p in positions.values() if p) + total_unrealized_pnl

            lines.append("─────────────────────────────────────────")
            lines.append(f"AÇIK K/Z       : {total_unrealized_pnl:+8.2f} USDT")
            lines.append(f"REALİZE K/Z    : {total_realized:+8.2f} USDT")
            lines.append(f"TOPLAM VARLIK  : {total_equity:8.2f} USDT")

            output_text = "\n".join(lines)
            clear_screen()
            print(output_text)

            # Telegram İşlem Bildirimleri
            for event in trade_events:
                send_telegram_msg(event)

            # 15 Dakikalık Rapor Bildirimi
            now_ts = time.time()
            if now_ts - last_telegram_time >= TELEGRAM_NOTIFY_INTERVAL:
                send_telegram_msg(output_text)
                last_telegram_time = now_ts

            save_state(positions, wallet_balances, realized_pnl, trade_number)
            time.sleep(LOOP_SECONDS)

        except KeyboardInterrupt:
            print("\nBot kapatılıyor...")
            save_state(positions, wallet_balances, realized_pnl, trade_number)
            break
        except Exception as e:
            print(f"Hata oluştu: {e}")
            time.sleep(15)

if __name__ == "__main__":
    main()

# ============================================================
# COINLER
# ============================================================

SYMBOLS = {
    "XAUUSDT": "XAU",
    "XAGUSDT": "XAG",
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
    "BNBUSDT": "BNB",
    "XRPUSDT": "XRP",
    "ADAUSDT": "ADA",
    "AVAXUSDT": "AVAX",
    "LINKUSDT": "LINK",
    "DOGEUSDT": "DOGE",
}


# ============================================================
# ANALİZ AYARLARI
# ============================================================

TIMEFRAME = "15m"
LIMIT = 100
LOOP_SECONDS = 60


# ============================================================
# SANAL İŞLEM AYARLARI
# ============================================================

# Her coin için başlangıç cüzdanı
STARTING_BALANCE_PER_COIN = 30.0

# İşlem başına teminat
MARGIN_PER_TRADE = 25.0

# Sanal kaldıraç
LEVERAGE = 10.0

# 25 x 10 = 250 USDT pozisyon
POSITION_SIZE = MARGIN_PER_TRADE * LEVERAGE

# TP %2
TAKE_PROFIT_PCT = 0.02

# SL %5
STOP_LOSS_PCT = 0.05

# Komisyon
COMMISSION_RATE = 0.0004


# ============================================================
# SİSTEM
# ============================================================

STATE_FILE = "bot_state.json"

REQUEST_TIMEOUT = 10
RETRY_COUNT = 3

TELEGRAM_NOTIFY_INTERVAL = 15 * 60

TURKEY_TZ = timezone(timedelta(hours=3))


# ============================================================
# TERMUX WAKE LOCK
# ============================================================

def acquire_wake_lock():
    try:
        subprocess.run(
            ["termux-wake-lock"],
            check=False
        )
    except Exception:
        pass


def release_wake_lock():
    try:
        subprocess.run(
            ["termux-wake-unlock"],
            check=False
        )
    except Exception:
        pass


acquire_wake_lock()
atexit.register(release_wake_lock)


# ============================================================
# YARDIMCI
# ============================================================

def clear_screen():
    os.system(
        "cls" if os.name == "nt" else "clear"
    )


def now_date_text():
    return datetime.now(
        TURKEY_TZ
    ).strftime(
        "%d.%m.%Y %H:%M:%S"
    )


# ============================================================
# STATE KAYDET
# ============================================================

def save_state(
    positions,
    wallet_balances,
    realized_pnl,
    trade_number
):

    state = {
        "positions": positions,
        "wallet_balances": wallet_balances,
        "realized_pnl": realized_pnl,
        "trade_number": trade_number,
        "last_save": now_date_text()
    }

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:

        print(
            f"Durum kaydedilemedi: {e}"
        )


# ============================================================
# STATE YÜKLE
# ============================================================

def load_state():

    if not os.path.exists(
        STATE_FILE
    ):
        return None

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_msg(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
        or TELEGRAM_BOT_TOKEN == "YENI_BOT_TOKENINI_BURAYA_YAZ"
    ):
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"<pre>{message}</pre>",
        "parse_mode": "HTML"
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    req = Request(
        url,
        data=payload,
        headers=headers,
        method="POST"
    )

    for _ in range(
        RETRY_COUNT
    ):

        try:

            with urlopen(
                req,
                timeout=REQUEST_TIMEOUT
            ) as response:

                return response.status == 200

        except Exception:

            time.sleep(1)

    return False


# ============================================================
# HTTP
# ============================================================

def http_get_json(url):

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    }

    try:

        req = Request(
            url,
            headers=headers
        )

        with urlopen(
            req,
            timeout=REQUEST_TIMEOUT
        ) as response:

            return json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

    except Exception:

        return None


# ============================================================
# KLINE
# ============================================================

def get_klines(symbol):

    for base_url in BASE_URLS:

        url = (
            f"{base_url}"
            f"?symbol={symbol}"
            f"&interval={TIMEFRAME}"
            f"&limit={LIMIT}"
        )

        data = http_get_json(url)

        if (
            data
            and isinstance(data, list)
            and len(data) > 0
        ):

            return data

    return None


# ============================================================
# EMA
# ============================================================

def calc_ema(
    data,
    period
):

    if len(data) < period:
        return []

    sma = (
        sum(data[:period])
        / period
    )

    ema = [sma]

    k = 2 / (
        period + 1
    )

    for price in data[period:]:

        ema.append(
            price * k
            +
            ema[-1] * (1 - k)
        )

    return ema


# ============================================================
# ATR
# ============================================================

def calc_atr(
    highs,
    lows,
    closes,
    period=20
):

    trs = []

    for i in range(
        1,
        len(closes)
    ):

        tr = max(
            highs[i] - lows[i],
            abs(
                highs[i]
                -
                closes[i - 1]
            ),
            abs(
                lows[i]
                -
                closes[i - 1]
            )
        )

        trs.append(tr)

    if len(trs) < period:
        return 0.0

    return (
        sum(trs[-period:])
        / period
    )


# ============================================================
# RSI
# ============================================================

def calc_rsi(
    closes,
    period=14
):

    if len(closes) <= period:
        return 50.0

    gains = []
    losses = []

    for i in range(
        1,
        len(closes)
    ):

        change = (
            closes[i]
            -
            closes[i - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            abs(min(change, 0))
        )

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain *
                (period - 1)
            )
            +
            gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss *
                (period - 1)
            )
            +
            losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = (
        avg_gain
        /
        avg_loss
    )

    return 100 - (
        100 /
        (1 + rs)
    )


# ============================================================
# ANALİZ
# ============================================================

def analyze(symbol):

    data = get_klines(
        symbol
    )

    if (
        not data
        or len(data) < 50
    ):
        return (
            symbol,
            None,
            None,
            None
        )

    # Açık mum çıkarılır
    closed = data[:-1]

    closes = [
        float(row[4])
        for row in closed
    ]

    highs = [
        float(row[2])
        for row in closed
    ]

    lows = [
        float(row[3])
        for row in closed
    ]

    price = closes[-1]

    ema = calc_ema(
        closes,
        20
    )

    atr = calc_atr(
        highs,
        lows,
        closes,
        20
    )

    if (
        not ema
        or atr == 0
    ):
        return (
            symbol,
            None,
            None,
            None
        )

    kc_lower = (
        ema[-1]
        -
        atr * 2
    )

    kc_upper = (
        ema[-1]
        +
        atr * 2
    )

    rsi = calc_rsi(
        closes,
        14
    )

    signal = None

    if (
        price < kc_lower
        and rsi <= 25
    ):

        signal = "LONG"

    elif (
        price > kc_upper
        and rsi >= 75
    ):

        signal = "SHORT"

    return (
        symbol,
        signal,
        price,
        rsi
    )


# ============================================================
# HEALTH CHECK
# ============================================================

class HealthCheckHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(
            200
        )

        self.end_headers()

        self.wfile.write(
            b"Bot Active"
        )

    def log_message(
        self,
        format,
        *args
    ):

        return


def run_health_check_server():

    port = int(
        os.getenv(
            "PORT",
            8080
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthCheckHandler
    )

    server.serve_forever()


# ============================================================
# ANA PROGRAM
# ============================================================

def main():

    threading.Thread(
        target=run_health_check_server,
        daemon=True
    ).start()


    state = load_state()


    if state:

        positions = state.get(
            "positions",
            {
                s: None
                for s in SYMBOLS
            }
        )

        wallet_balances = state.get(
            "wallet_balances",
            {
                s:
                STARTING_BALANCE_PER_COIN
                for s in SYMBOLS
            }
        )

        realized_pnl = state.get(
            "realized_pnl",
            {
                s: 0.0
                for s in SYMBOLS
            }
        )

        trade_number = state.get(
            "trade_number",
            0
        )

    else:

        positions = {
            s: None
            for s in SYMBOLS
        }

        wallet_balances = {
            s:
            STARTING_BALANCE_PER_COIN
            for s in SYMBOLS
        }

        realized_pnl = {
            s: 0.0
            for s in SYMBOLS
        }

        trade_number = 0


    print(
        "\n"
        "========================================\n"
        "       10X SANAL KELTNER BOT\n"
        "========================================"
    )

    print(
        f"Başlangıç Cüzdanı : "
        f"{STARTING_BALANCE_PER_COIN:.2f} USDT"
    )

    print(
        f"İşlem Teminatı    : "
        f"{MARGIN_PER_TRADE:.2f} USDT"
    )

    print(
        f"Kaldıraç           : "
        f"{LEVERAGE:.0f}x"
    )

    print(
        f"Pozisyon Büyüklüğü : "
        f"{POSITION_SIZE:.2f} USDT"
    )

    print(
        f"TP                 : "
        f"%{TAKE_PROFIT_PCT * 100:.1f}"
    )

    print(
        f"SL                 : "
        f"%{STOP_LOSS_PCT * 100:.1f}"
    )

    print(
        "========================================\n"
    )


    last_telegram_time = 0


    while True:

        try:

            lines = []

            trade_events = []

            total_unrealized_pnl = 0.0


            # ==================================================
            # RAPOR BAŞI
            # ==================================================

            lines.append(
                "╔══════════════════════════════════════╗"
            )

            lines.append(
                "       10X SANAL KELTNER RAPORU"
            )

            lines.append(
                f" Tarih: {now_date_text()}"
            )

            lines.append(
                f" Kaldıraç: {LEVERAGE:.0f}x"
            )

            lines.append(
                f" Teminat: {MARGIN_PER_TRADE:.2f} USDT"
            )

            lines.append(
                f" Pozisyon: {POSITION_SIZE:.2f} USDT"
            )

            lines.append(
                f" TP: %{TAKE_PROFIT_PCT * 100:.1f}"
                f" | SL: %{STOP_LOSS_PCT * 100:.1f}"
            )

            lines.append(
                "╚══════════════════════════════════════╝"
            )

            lines.append("")

            lines.append(
                "COIN | FİYAT     | DURUM | CÜZDAN | K/Z"
            )

            lines.append(
                "─────────────────────────────────────────"
            )


            # ==================================================
            # COIN ANALİZLERİ
            # ==================================================

            with ThreadPoolExecutor(
                max_workers=5
            ) as executor:

                results = list(
                    executor.map(
                        analyze,
                        SYMBOLS.keys()
                    )
                )


            analysis_dict = {
                r[0]: r[1:]
                for r in results
            }


            # ==================================================
            # COINLERİ İŞLE
            # ==================================================

            for symbol, name in SYMBOLS.items():

                signal, current_price, rsi = (
                    analysis_dict.get(
                        symbol,
                        (
                            None,
                            None,
                            None
                        )
                    )
                )


                wallet = wallet_balances.get(
                    symbol,
                    STARTING_BALANCE_PER_COIN
                )


                # ----------------------------------------------
                # VERİ YOK
                # ----------------------------------------------

                if current_price is None:

                    lines.append(
                        f"{name:<4} | "
                        f"{'N/A':<9} | "
                        f"BOŞ   | "
                        f"{wallet:6.2f} | "
                        f" 0.00"
                    )

                    continue


                pos = positions.get(
                    symbol
                )

                unrealized_pnl = 0.0

                status_code = "BOŞ"


                # ==================================================
                # POZİSYON AÇ
                # ==================================================

                if (
                    pos is None
                    and signal in (
                        "LONG",
                        "SHORT"
                    )
                    and wallet >= MARGIN_PER_TRADE
                ):

                    trade_number += 1


                    if signal == "LONG":

                        tp = (
                            current_price
                            *
                            (
                                1 +
                                TAKE_PROFIT_PCT
                            )
                        )

                        sl = (
                            current_price
                            *
                            (
                                1 -
                                STOP_LOSS_PCT
                            )
                        )

                    else:

                        tp = (
                            current_price
                            *
                            (
                                1 -
                                TAKE_PROFIT_PCT
                            )
                        )

                        sl = (
                            current_price
                            *
                            (
                                1 +
                                STOP_LOSS_PCT
                            )
                        )


                    # ------------------------------------------
                    # TEMİNATI BLOKE ET
                    # ------------------------------------------

                    wallet_balances[symbol] = (
                        wallet
                        -
                        MARGIN_PER_TRADE
                    )


                    # ------------------------------------------
                    # POZİSYON KAYDI
                    # ------------------------------------------

                    positions[symbol] = {

                        "id":
                        trade_number,

                        "side":
                        signal,

                        "entry":
                        current_price,

                        "tp":
                        tp,

                        "sl":
                        sl,

                        "margin":
                        MARGIN_PER_TRADE,

                        "leverage":
                        LEVERAGE,

                        "position_size":
                        POSITION_SIZE
                    }


                    pos = positions[symbol]


                    trade_events.append(
                        f"🚨 <b>YENİ POZİSYON</b>\n"
                        f"Coin: {name}\n"
                        f"Yön: {signal}\n"
                        f"Giriş: {current_price:.6f}\n"
                        f"Teminat: {MARGIN_PER_TRADE:.2f} USDT\n"
                        f"Kaldıraç: {LEVERAGE:.0f}x\n"
                        f"Pozisyon: {POSITION_SIZE:.2f} USDT\n"
                        f"TP: {tp:.6f} (%{TAKE_PROFIT_PCT * 100:.1f})\n"
                        f"SL: {sl:.6f} (%{STOP_LOSS_PCT * 100:.1f})"
                    )


                # ==================================================
                # AÇIK POZİSYON
                # ==================================================

                if pos is not None:

                    side = pos["side"]

                    entry = float(
                        pos["entry"]
                    )

                    margin = float(
                        pos.get(
                            "margin",
                            MARGIN_PER_TRADE
                        )
                    )

                    leverage = float(
                        pos.get(
                            "leverage",
                            LEVERAGE
                        )
                    )

                    position_size = float(
                        pos.get(
                            "position_size",
                            margin * leverage
                        )
                    )


                    # ------------------------------------------
                    # FİYAT DEĞİŞİMİ
                    # ------------------------------------------

                    if side == "LONG":

                        pct = (
                            current_price
                            -
                            entry
                        ) / entry

                    else:

                        pct = (
                            entry
                            -
                            current_price
                        ) / entry


                    # ------------------------------------------
                    # KALDIRAÇLI BRÜT K/Z
                    # ------------------------------------------

                    gross_pnl = (
                        position_size
                        *
                        pct
                    )


                    # ------------------------------------------
                    # KOMİSYON
                    # ------------------------------------------

                    commission = (
                        position_size
                        *
                        COMMISSION_RATE
                    )


                    unrealized_pnl = (
                        gross_pnl
                        -
                        commission
                    )


                    total_unrealized_pnl += (
                        unrealized_pnl
                    )


                    # ==================================================
                    # TP KONTROL
                    # ==================================================

                    hit_tp = (

                        (
                            side == "LONG"
                            and current_price >= pos["tp"]
                        )

                        or

                        (
                            side == "SHORT"
                            and current_price <= pos["tp"]
                        )
                    )


                    # ==================================================
                    # SL KONTROL
                    # ==================================================

                    hit_sl = (

                        (
                            side == "LONG"
                            and current_price <= pos["sl"]
                        )

                        or

                        (
                            side == "SHORT"
                            and current_price >= pos["sl"]
                        )
                    )


                    # ==================================================
                    # POZİSYON KAPAT
                    # ==================================================

                    if hit_tp or hit_sl:

                        final_pnl = unrealized_pnl


                        # ------------------------------------------
                        # TEMİNAT + K/Z CÜZDANA GERİ DÖNER
                        # ------------------------------------------

                        wallet_balances[symbol] = (
                            wallet_balances[symbol]
                            +
                            margin
                            +
                            final_pnl
                        )


                        realized_pnl[symbol] = (
                            realized_pnl.get(
                                symbol,
                                0.0
                            )
                            +
                            final_pnl
                        )


                        positions[symbol] = None


                        status_code = "KAP"


                        if hit_tp:

                            result_text = (
                                "TAKE PROFIT"
                            )

                        else:

                            result_text = (
                                "STOP LOSS"
                            )


                        trade_events.append(
                            f"✅ <b>POZİSYON KAPANDI</b>\n"
                            f"Coin: {name}\n"
                            f"Sonuç: {result_text}\n"
                            f"Yön: {side}\n"
                            f"Kaldıraç: {leverage:.0f}x\n"
                            f"Pozisyon: {position_size:.2f} USDT\n"
                            f"P/L: {final_pnl:+.2f} USDT\n"
                            f"Yeni Cüzdan: "
                            f"{wallet_balances[symbol]:.2f} USDT"
                        )


                    else:

                        status_code = (
                            "LNG"
                            if side == "LONG"
                            else "SHR"
                        )


                # ==================================================
                # EKRANDAKİ TOPLAM COİN VARLIĞI
                # ==================================================

                if positions.get(symbol) is not None:

                    display_wallet = (
                        wallet_balances[symbol]
                        +
                        MARGIN_PER_TRADE
                        +
                        unrealized_pnl
                    )

                else:

                    display_wallet = (
                        wallet_balances[symbol]
                    )


                # ----------------------------------------------
                # FİYAT FORMAT
                # ----------------------------------------------

                if current_price >= 1000:

                    price_str = (
                        f"{current_price:9.1f}"
                    )

                elif current_price >= 1:

                    price_str = (
                        f"{current_price:9.2f}"
                    )

                else:

                    price_str = (
                        f"{current_price:9.6f}"
                    )


                lines.append(
                    f"{name:<4} | "
                    f"{price_str} | "
                    f"{status_code:<5} | "
                    f"{display_wallet:6.2f} | "
                    f"{unrealized_pnl:+6.2f}"
                )


            # ==================================================
            # TOPLAM HESAPLAR
            # ==================================================

            total_cash = sum(
                wallet_balances.values()
            )


            total_open_margin = sum(
                float(
                    p.get(
                        "margin",
                        MARGIN_PER_TRADE
                    )
                )

                for p in positions.values()

                if p is not None
            )


            total_equity = (
                total_cash
                +
                total_open_margin
                +
                total_unrealized_pnl
            )


            total_realized = sum(
                realized_pnl.values()
            )


            lines.append(
                "─────────────────────────────────────────"
            )

            lines.append(
                f"AÇIK K/Z       : "
                f"{total_unrealized_pnl:+8.2f} USDT"
            )

            lines.append(
                f"REALİZE K/Z    : "
                f"{total_realized:+8.2f} USDT"
            )

            lines.append(
                f"NAKİT CÜZDAN   : "
                f"{total_cash:8.2f} USDT"
            )

            lines.append(
                f"TOPLAM VARLIK  : "
                f"{total_equity:8.2f} USDT"
            )


            # ==================================================
            # EKRANA YAZ
            # ==================================================

            output_text = "\n".join(
                lines
            )

            clear_screen()

            print(
                output_text
            )


            # ==================================================
            # TELEGRAM İŞLEM BİLDİRİMLERİ
            # ==================================================

            for event in trade_events:

                send_telegram_msg(
                    event
                )


            # ==================================================
            # 15 DAKİKALIK RAPOR
            # ==================================================

            now_ts = time.time()

            if (
                now_ts -
                last_telegram_time
                >=
                TELEGRAM_NOTIFY_INTERVAL
            ):

                send_telegram_msg(
                    output_text
                )

                last_telegram_time = (
                    now_ts
                )


            # ==================================================
            # STATE KAYDET
            # ==================================================

            save_state(
                positions,
                wallet_balances,
                realized_pnl,
                trade_number
            )


            time.sleep(
                LOOP_SECONDS
            )


        # ======================================================
        # CTRL+C
        # ======================================================

        except KeyboardInterrupt:

            print(
                "\nBot kapatılıyor..."
            )

            save_state(
                positions,
                wallet_balances,
                realized_pnl,
                trade_number
            )

            break


        # ======================================================
        # HATA
        # ======================================================

        except Exception as e:

            print(
                f"Hata oluştu: {e}"
            )

            time.sleep(
                15
            )


# ============================================================
# BAŞLAT
# ============================================================

if __name__ == "__main__":
    main()
def get_turkey_time():
    """Sunucu saatinden bağımsız Türkiye saatini (UTC+3) hesaplar."""
    return datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M")

def calculate_rsi(series, period=14):
    """RSI hesaplama fonksiyonu."""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def fetch_market_data():
    """Her döngüde Binance'den taze canlı mum verisi çeker."""
    results = []
    for symbol in SYMBOLS:
        try:
            # Son 50 mumu çek (15 dakikalık periyot)
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=50)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # Anlık güncel fiyat ve RSI
            current_price = df['close'].iloc[-1]
            rsi_series = calculate_rsi(df['close'], period=14)
            current_rsi = rsi_series.iloc[-1]
            
            # Sembol ismini düzenle (Örn: PAXG -> XAU)
            coin_name = symbol.split('/')[0]
            if coin_name == "PAXG":
                coin_name = "XAU"

            results.append({
                'coin': coin_name,
                'price': current_price,
                'rsi': current_rsi if not np.isnan(current_rsi) else 50.0,
                'pos': 'BOS',
                'cuzdan': 30.0,
                'kz': 0.00
            })
        except Exception as e:
            logging.error(f"{symbol} verisi çekilirken hata oluştu: {e}")
            coin_name = symbol.split('/')[0]
            if coin_name == "PAXG": coin_name = "XAU"
            results.append({'coin': coin_name, 'price': 0.0, 'rsi': 0.0, 'pos': 'HATA', 'cuzdan': 30.0, 'kz': 0.00})
    
    return results

def build_report_message(data):
    """Telegram için hizalanmış tablo formatı oluşturur."""
    tr_time = get_turkey_time()
    
    msg = f"╔═══════════════════════════════════════╗\n"
    msg += f"   BINANCE KELTNER & RSI BANT RAPORU    \n"
    msg += f"   Tarih/Saat: {tr_time}\n"
    msg += f"╚═══════════════════════════════════════╝\n\n"
    msg += f"COIN | FIYAT     | RSI | POS | CUZDAN|  K/Z \n"
    msg += f"─────────────────────────────────────────\n"
    
    for row in data:
        coin = row['coin'].ljust(4)
        price = f"{row['price']:8.2f}" if row['price'] >= 1 else f"{row['price']:8.4f}"
        rsi = f"{row['rsi']:4.1f}"
        pos = row['pos'].center(3)
        cuzdan = f"{row['cuzdan']:5.1f}"
        kz = f"{row['kz']:+5.2f}"
        
        msg += f"{coin} | {price} | {rsi} | {pos} | {cuzdan} | {kz}\n"
        
    msg += f"─────────────────────────────────────────\n"
    msg += f"ACIK PNL  :   +0.00 USDT\n"
    msg += f"REALIZE   :   +0.00 USDT\n"
    msg += f"TOPLAM BAKİYE:  330.00 USDT"
    
    return f"```\n{msg}\n```"

def send_telegram_message(message):
    """Telegram API üzerinden mesaj gönderir ve ağ hatalarını yakalar."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            logging.error(f"Telegram gönderme hatası: {response.text}")
    except Exception as e:
        logging.error(f"Telegram bağlantı hatası: {e}")

def main():
    logging.info("Binance Tarama Botu Başlatıldı...")
    while True:
        try:
            data = fetch_market_data()
            report = build_report_message(data)
            send_telegram_message(report)
            logging.info("Rapor Telegram'a başarıyla gönderildi.")
        except Exception as e:
            logging.error(f"Ana döngü hatası: {e}")
        
        # 15 dakika (900 sn) bekleme
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()


# ========== CÜZDAN & RİSK AYARLARI ==========
STARTING_BALANCE_PER_COIN = 30.0
POSITION_SIZE = 25.0              # Notional pozisyon büyüklüğü
TAKE_PROFIT_PCT = 0.02            # %2 TP
STOP_LOSS_PCT = 0.05              # %5 SL
# ============================================

STATE_FILE = "bot_state.json"
REQUEST_TIMEOUT = 12
RETRY_COUNT = 3
RETRY_DELAY = 2

# Render için Web Server (Port Dinleyici)
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Binance Bot Aktif ve Calisiyor!")

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"✓ Web Sunucusu Port {port} uzerinde baslatildi.")
    server.serve_forever()

def now_date_text():
    return datetime.now().strftime("%d.%m.%Y %H:%M")

def save_state(positions, wallet_balances, realized_pnl, trade_number):
    state = {
        "positions": positions,
        "wallet_balances": wallet_balances,
        "realized_pnl": realized_pnl,
        "trade_number": trade_number,
        "last_save": now_date_text()
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Durum kaydedilemedi: {e}")

def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def send_telegram_msg(message):
    if not ENABLE_TELEGRAM:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"<pre>{message}</pre>",
        "parse_mode": "HTML"
    }).encode("utf-8")

    req = Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": "Render-KeltnerBot/5.0"
    }, method="POST")

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return response.status == 200
        except Exception:
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
    return False

def http_get_json(url):
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            req = Request(url, headers={"User-Agent": "Render-KeltnerBot/5.0"})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
    return None

def get_klines(symbol):
    url = f"{BASE_URL}?symbol={symbol}&interval={TIMEFRAME}&limit={LIMIT}"
    return http_get_json(url)

def calc_ema(data, period):
    k = 2 / (period + 1)
    ema = [data[0]]
    for p in data[1:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema

def calc_atr(highs, lows, closes, period=20):
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period if trs else 0.0

def calc_rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    if len(gains) < period:
        return 50.0

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def analyze(symbol):
    data = get_klines(symbol)
    if not data or len(data) < 50:
        return None, None, None, None, None

    closed = data[:-1]
    closes = [float(row[4]) for row in closed]
    highs = [float(row[2]) for row in closed]
    lows = [float(row[3]) for row in closed]
    price = closes[-1]

    ema = calc_ema(closes, 20)
    atr = calc_atr(highs, lows, closes, 20)
    kc_upper = ema[-1] + (atr * 2)
    kc_lower = ema[-1] - (atr * 2)

    rsi = calc_rsi(closes, 14)

    signal = None
    if price < kc_lower and rsi <= 25:
        signal = "LONG"
    elif price > kc_upper and rsi >= 75:
        signal = "SHORT"

    return signal, price, rsi, kc_lower, kc_upper

def main():
    # Web sunucusunu arka planda baslat
    server_thread = threading.Thread(target=start_web_server, daemon=True)
    server_thread.start()

    state = load_state()

    if state:
        positions = state.get("positions", {s: None for s in SYMBOLS})
        wallet_balances = state.get("wallet_balances", {s: STARTING_BALANCE_PER_COIN for s in SYMBOLS})
        realized_pnl = state.get("realized_pnl", {s: 0.0 for s in SYMBOLS})
        trade_number = state.get("trade_number", 0)
        print(f"✓ Kayitli durum yuklendi (Son kayit: {state.get('last_save', '?')})")
    else:
        positions = {symbol: None for symbol in SYMBOLS}
        wallet_balances = {symbol: STARTING_BALANCE_PER_COIN for symbol in SYMBOLS}
        realized_pnl = {symbol: 0.0 for symbol in SYMBOLS}
        trade_number = 0
        print("✓ Yeni oturum baslatildi")

    print("🚀 Binance Bulut Analiz Botu Baslatildi\n")

    while True:
        try:
            lines = []
            total_unrealized_pnl = 0.0

            lines.append("╔═══════════════════════════════════════╗")
            lines.append("   BINANCE KELTNER & RSI BANT RAPORU    ")
            lines.append(f"   Tarih/Saat: {now_date_text()}")
            lines.append("╚═══════════════════════════════════════╝")
            lines.append("")
            lines.append("COIN | FIYAT     | RSI | POS | CUZDAN|  K/Z ")
            lines.append("─────────────────────────────────────────")

            for symbol, name in SYMBOLS.items():
                signal, current_price, rsi, kc_lower, kc_upper = analyze(symbol)

                if current_price is None:
                    lines.append(f"{name:<4} | {'N/A':<9} | N/A | BOS | {wallet_balances.get(symbol, 30.0):5.1f} |  0.00")
                    continue

                pos = positions.get(symbol)

                if pos is None and signal in ("LONG", "SHORT"):
                    trade_number += 1
                    if signal == "LONG":
                        tp = current_price * (1 + TAKE_PROFIT_PCT)
                        sl = current_price * (1 - STOP_LOSS_PCT)
                    else:
                        tp = current_price * (1 - TAKE_PROFIT_PCT)
                        sl = current_price * (1 + STOP_LOSS_PCT)

                    positions[symbol] = {
                        "id": trade_number,
                        "side": signal,
                        "entry": current_price,
                        "tp": tp,
                        "sl": sl
                    }
                    pos = positions[symbol]
                    print(f"→ {name} {signal} acildi @ {current_price:.4f}")

                unrealized_pnl = 0.0
                status_code = "BOS"

                if pos is not None:
                    side = pos["side"]
                    entry = pos["entry"]
                    pct = ((current_price - entry) / entry) if side == "LONG" else ((entry - current_price) / entry)
                    unrealized_pnl = POSITION_SIZE * pct
                    total_unrealized_pnl += unrealized_pnl

                    hit_tp = (side == "LONG" and current_price >= pos["tp"]) or \
                             (side == "SHORT" and current_price <= pos["tp"])
                    hit_sl = (side == "LONG" and current_price <= pos["sl"]) or \
                             (side == "SHORT" and current_price >= pos["sl"])

                    if hit_tp or hit_sl:
                        realized_pnl[symbol] = realized_pnl.get(symbol, 0.0) + unrealized_pnl
                        wallet_balances[symbol] = wallet_balances.get(symbol, STARTING_BALANCE_PER_COIN) + unrealized_pnl
                        result = "TP" if hit_tp else "SL"
                        print(f"✓ {name} {side} kapandi ({result}) P/L: {unrealized_pnl:+.2f}")
                        positions[symbol] = None
                        status_code = "KAP"
                    else:
                        status_code = "LNG" if side == "LONG" else "SHR"

                if current_price >= 1000:
                    price_str = f"{current_price:9.1f}"
                elif current_price >= 1:
                    price_str = f"{current_price:9.2f}"
                else:
                    price_str = f"{current_price:9.4f}"

                current_coin_wallet = wallet_balances.get(symbol, STARTING_BALANCE_PER_COIN) + unrealized_pnl
                rsi_str = f"{rsi:4.1f}"
                
                line = f"{name:<4} | {price_str} | {rsi_str} | {status_code:<3} | {current_coin_wallet:5.1f} | {unrealized_pnl:+5.2f}"
                lines.append(line)

            total_wallet = sum(wallet_balances.values()) + total_unrealized_pnl
            total_realized = sum(realized_pnl.values())

            lines.append("─────────────────────────────────────────")
            lines.append(f"ACIK PNL  : {total_unrealized_pnl:+7.2f} USDT")
            lines.append(f"REALIZE   : {total_realized:+7.2f} USDT")
            lines.append(f"TOPLAM BAKİYE: {total_wallet:7.2f} USDT")

            table_text = "\n".join(lines)

            print(table_text)
            save_state(positions, wallet_balances, realized_pnl, trade_number)
            send_telegram_msg(table_text)

            time.sleep(LOOP_SECONDS)

        except Exception as e:
            print(f"Hata olustu: {e}")
            time.sleep(15)

if __name__ == "__main__":
    main()
