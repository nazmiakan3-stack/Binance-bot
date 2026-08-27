#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
import pandas as pd
import numpy as np
import ccxt
import requests
from datetime import datetime, timezone, timedelta

# Log yapılandırması
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- AYARLAR ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")

# Rapor gönderme aralığı (900 saniye = 15 dakika)
CHECK_INTERVAL = 900  

# Taranacak semboller (Binance Spot standart çiftleri)
SYMBOLS = [
    "PAXG/USDT",  # XAU (Ons Altın endeksli token)
    "XAG/USDT",   # Gümüş
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", 
    "XRP/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "DOGE/USDT"
]

# Binance Borsasına Bağlantı
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

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
