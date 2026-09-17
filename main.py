#!/usr/bin/env python3
"""
Binance Futures 15m Multi-Coin Simulator
Tüm USDT-M perpetual coinleri tarar (dinamik liste).
Aynı sinyal mantığı (BB + StochRSI + KDJ + EMA + MACD + Hacim).
Sanal bakiye: 500 USDT
Her pozisyon: 10 USDT margin + 10x kaldıraç
Gerçek emir YOK.
"""

import json
import os
import time
import logging
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ==================== YAPILANDIRMA ====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://fapi.binance.com/fapi/v1"
EXCHANGE_INFO_URL = f"{BASE_URL}/exchangeInfo"
KLINES_URL = f"{BASE_URL}/klines"

INTERVAL = "15m"
LIMIT = 200                    # 250 yerine 200 → daha hızlı tarama

SL_ATR_MULTIPLIER = 1.2
LEVERAGE = 10.0
MARGIN_PER_TRADE = 10.0        # Her pozisyon için sabit 10 USDT margin
STARTING_BALANCE = 500.0       # Toplam sanal bakiye

REQUEST_TIMEOUT = 12
RETRY_COUNT = 2
RETRY_DELAY = 2
LOOP_SECONDS = 90              # Tam tarama uzun sürdüğü için biraz daha uzun bekle
REQUEST_DELAY = 0.12           # Coinler arası istek gecikmesi (rate limit koruması)

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("metal-bot")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram token/chat_id tanımlı değil, mesaj gönderilmedi.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "MultiCoinSimulator/9.9",
        },
        method="POST",
    )

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return response.status == 200
        except Exception as e:
            log.warning(f"Telegram deneme {attempt}/{RETRY_COUNT} başarısız: {e}")
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
    return False


def http_get_json(url: str):
    last_error = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            req = Request(url, headers={"User-Agent": "MultiCoinSimulator/9.9"})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            last_error = e
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
    raise RuntimeError(f"Binance bağlantısı başarısız: {last_error}")


def get_all_usdt_perpetual_symbols() -> list:
    """Tüm USDT-M perpetual trading sembollerini dinamik olarak çeker."""
    data = http_get_json(EXCHANGE_INFO_URL)
    symbols = []
    for s in data.get("symbols", []):
        if (
            s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
            and s.get("symbol", "").endswith("USDT")
        ):
            symbols.append(s["symbol"])
    symbols.sort()
    log.info(f"Toplam {len(symbols)} USDT perpetual sembol bulundu.")
    return symbols


def get_klines(symbol: str, interval: str = INTERVAL, limit: int = LIMIT):
    url = f"{KLINES_URL}?symbol={symbol}&interval={interval}&limit={limit}"
    return http_get_json(url)


def parse_klines(data):
    closes = [float(row[4]) for row in data]
    highs = [float(row[2]) for row in data]
    lows = [float(row[3]) for row in data]
    volumes = [float(row[5]) for row in data]
    return closes, highs, lows, volumes


def ema_series(values, period: int):
    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    ema_list = [sum(values[:period]) / period]
    for price in values[period:]:
        ema_list.append((price - ema_list[-1]) * multiplier + ema_list[-1])
    return ema_list


def sma(values, period: int):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def bollinger_bands(values, period: int = 20, std_dev: float = 2.0):
    if len(values) < period:
        return None, None, None
    sma_val = sum(values[-period:]) / period
    variance = sum((x - sma_val) ** 2 for x in values[-period:]) / period
    std = variance ** 0.5
    upper = sma_val + (std * std_dev)
    lower = sma_val - (std * std_dev)
    return upper, sma_val, lower


def rsi_series(values, period: int = 14):
    if len(values) < period + 1:
        return []
    gains, losses = [], []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_list = []
    rsi_list.append(100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss)))

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        rsi_list.append(100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss)))

    return rsi_list


def stoch_rsi_series(values, rsi_period=14, stoch_period=14, smooth_k=3, smooth_d=3):
    rsi_vals = rsi_series(values, rsi_period)
    if len(rsi_vals) < stoch_period:
        return [], []

    raw_stoch = []
    for i in range(stoch_period - 1, len(rsi_vals)):
        window = rsi_vals[i - stoch_period + 1:i + 1]
        lo, hi = min(window), max(window)
        raw_stoch.append(0.0 if hi == lo else (rsi_vals[i] - lo) / (hi - lo) * 100)

    if len(raw_stoch) < smooth_k:
        return [], []

    k_vals = [
        sum(raw_stoch[i - smooth_k + 1:i + 1]) / smooth_k
        for i in range(smooth_k - 1, len(raw_stoch))
    ]

    if len(k_vals) < smooth_d:
        return [], []

    d_vals = [
        sum(k_vals[i - smooth_d + 1:i + 1]) / smooth_d
        for i in range(smooth_d - 1, len(k_vals))
    ]

    return k_vals, d_vals


def kdj_series(closes, highs, lows, n=9, m1=3, m2=3):
    if len(closes) < n:
        return [], [], []

    k_list, d_list, j_list = [], [], []
    k, d = 50.0, 50.0

    for i in range(n - 1, len(closes)):
        low_n = min(lows[i - n + 1:i + 1])
        high_n = max(highs[i - n + 1:i + 1])
        rsv = 50.0 if high_n == low_n else (closes[i] - low_n) / (high_n - low_n) * 100

        k = (m1 - 1) / m1 * k + 1 / m1 * rsv
        d = (m2 - 1) / m2 * d + 1 / m2 * k
        j = 3 * k - 2 * d

        k_list.append(k)
        d_list.append(d)
        j_list.append(j)

    return k_list, d_list, j_list


def macd_series(values, fast=12, slow=26, signal=9):
    fast_ema = ema_series(values, fast)
    slow_ema = ema_series(values, slow)

    diff = len(fast_ema) - len(slow_ema)
    fast_ema = fast_ema[diff:]

    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = ema_series(macd_line, signal)

    diff_sig = len(macd_line) - len(signal_line)
    macd_line = macd_line[diff_sig:]

    return macd_line, signal_line


def atr(data, period=14):
    if len(data) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(data)):
        h = float(data[i][2])
        l = float(data[i][3])
        pc = float(data[i - 1][4])
        true_ranges.append(max(h - l, abs(h - pc), abs(l - pc)))

    if len(true_ranges) < period:
        return None

    result = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        result = ((result * (period - 1)) + tr) / period

    return result


def analyze_15m(symbol: str):
    """Aynı orijinal sinyal mantığı – hiç bozulmadı."""
    data = get_klines(symbol, INTERVAL)
    if len(data) < 150:
        return None

    closed_data = data[:-1]
    closes, highs, lows, volumes = parse_klines(closed_data)

    price = closes[-1]
    high = highs[-1]
    low = lows[-1]

    stoch_k, stoch_d = stoch_rsi_series(closes)
    kdj_k, kdj_d, _ = kdj_series(closes, highs, lows)
    ema5_series = ema_series(closes, 5)
    ema20_series = ema_series(closes, 20)
    macd_line, macd_sig = macd_series(closes)
    vol_ma20 = sma(volumes, 20)
    atr14 = atr(closed_data, 14)
    bb_upper, bb_mid, bb_lower = bollinger_bands(closes, 20, 2)

    if not (stoch_k and kdj_k and ema5_series and macd_line and atr14 and bb_upper):
        return None

    sk_curr, sk_prev = stoch_k[-1], stoch_k[-2]
    sd_curr, sd_prev = stoch_d[-1], stoch_d[-2]

    kk_curr, kk_prev = kdj_k[-1], kdj_k[-2]
    kd_curr, kd_prev = kdj_d[-1], kdj_d[-2]

    e5_curr, e5_prev = ema5_series[-1], ema5_series[-2]
    e20_curr = ema20_series[-1]

    m_curr, m_prev = macd_line[-1], macd_line[-2]
    ms_curr, ms_prev = macd_sig[-1], macd_sig[-2]

    curr_vol = volumes[-1]

    # --- LONG koşulları (orijinal mantık) ---
    c_bb_long = low <= bb_lower or price <= bb_lower * 1.002
    c_stoch_long = sk_curr > sd_curr or (sk_prev < 30 and sk_curr > sk_prev)
    c_kdj_long = kk_curr > kd_curr or (kk_prev < 30 and kk_curr > kk_prev)
    c_ema_long = e5_curr > e5_prev or price > e20_curr
    c_macd_long = m_curr > ms_curr or m_curr > m_prev
    c_vol_long = vol_ma20 is not None and (curr_vol > vol_ma20 * 0.8)

    long_conditions = [c_bb_long, c_stoch_long, c_kdj_long, c_ema_long, c_macd_long, c_vol_long]
    long_score = sum(long_conditions)

    # --- SHORT koşulları (orijinal mantık) ---
    c_bb_short = high >= bb_upper or price >= bb_upper * 0.998
    c_stoch_short = sk_curr < sd_curr or (sk_prev > 70 and sk_curr < sk_prev)
    c_kdj_short = kk_curr < kd_curr or (kk_prev > 70 and kk_curr < kk_prev)
    c_ema_short = e5_curr < e5_prev or price < e20_curr
    c_macd_short = m_curr < ms_curr or m_curr < m_prev
    c_vol_short = vol_ma20 is not None and (curr_vol > vol_ma20 * 0.8)

    short_conditions = [c_bb_short, c_stoch_short, c_kdj_short, c_ema_short, c_macd_short, c_vol_short]
    short_score = sum(short_conditions)

    # --- Çıkış sinyalleri (orijinal mantık) ---
    long_exit_signal = (sk_curr < sd_curr and sk_curr > 75) or (e5_curr < e5_prev and price < e5_curr)
    short_exit_signal = (sk_curr > sd_curr and sk_curr < 25) or (e5_curr > e5_prev and price > e5_curr)

    signal = "BEKLE"
    score = 0
    reasons = []

    if long_score >= 2 and long_score > short_score:
        signal = "LONG"
        score = long_score
        if c_bb_long:
            reasons.append("BB Alt Bant Temas")
        if c_stoch_long:
            reasons.append("StochRSI Dönüşü")
        if c_kdj_long:
            reasons.append("KDJ Dip Kesişimi")
        if c_ema_long:
            reasons.append("EMA5/EMA20 Trend")
        if c_macd_long:
            reasons.append("MACD Momentum")
        if c_vol_long:
            reasons.append("Hacim Desteği")

    elif short_score >= 2 and short_score > long_score:
        signal = "SHORT"
        score = -short_score
        if c_bb_short:
            reasons.append("BB Üst Bant Temas")
        if c_stoch_short:
            reasons.append("StochRSI Tepe Dönüşü")
        if c_kdj_short:
            reasons.append("KDJ Tepe Kesişimi")
        if c_ema_short:
            reasons.append("EMA5/EMA20 Trend")
        if c_macd_short:
            reasons.append("MACD Momentum")
        if c_vol_short:
            reasons.append("Hacim Desteği")

    long_sl = short_sl = None
    if atr14:
        long_sl = price - atr14 * SL_ATR_MULTIPLIER
        short_sl = price + atr14 * SL_ATR_MULTIPLIER

    return {
        "signal": signal,
        "price": price,
        "high": high,
        "low": low,
        "long_sl": long_sl,
        "short_sl": short_sl,
        "long_exit_signal": long_exit_signal,
        "short_exit_signal": short_exit_signal,
        "score": score,
        "long_score": long_score,
        "short_score": short_score,
        "reasons": ", ".join(reasons) if reasons else "-",
    }


def run_simulation():
    log.info("=" * 70)
    log.info("   BINANCE FUTURES MULTI-COIN 15m SİMÜLATÖR v9.9")
    log.info("   TÜM USDT PERPETUAL COİNLER TARANIR")
    log.info("   GERÇEK EMİR YOK / API ANAHTARI YOK")
    log.info(f"   Sanal Bakiye: {STARTING_BALANCE} USDT | Margin/Pozisyon: {MARGIN_PER_TRADE} USDT")
    log.info("=" * 70)

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID tanımlı değil!")
        log.warning("Bildirimler çalışmayacak. .env dosyasını kontrol et.")

    # Tüm sembolleri bir kez çek
    try:
        all_symbols = get_all_usdt_perpetual_symbols()
    except Exception as e:
        log.error(f"Sembol listesi alınamadı: {e}")
        return

    if not all_symbols:
        log.error("Hiç sembol bulunamadı, çıkılıyor.")
        return

    balance = STARTING_BALANCE
    used_margin = 0.0
    positions = {}          # symbol → position dict
    trade_number = 0

    while True:
        try:
            cycle_start = time.time()
            log.info("-" * 70)
            log.info(f"Döngü başladı: {now_text()} | Açık pozisyon: {len(positions)} | Serbest bakiye: {balance - used_margin:.2f} USDT")

            scanned = 0
            signals_found = 0
            errors = 0

            for symbol in all_symbols:
                try:
                    # Rate limit koruması
                    time.sleep(REQUEST_DELAY)

                    data = analyze_15m(symbol)
                    scanned += 1

                    if data is None:
                        continue

                    signal = data["signal"]
                    price = data["price"]
                    high = data["high"]
                    low = data["low"]

                    # --- Açık pozisyon kontrolü ---
                    if symbol in positions:
                        pos = positions[symbol]

                        if pos.get("just_opened", False):
                            pos["just_opened"] = False
                            continue

                        entry = pos["entry"]
                        side = pos["side"]
                        sl = pos["sl"]
                        margin = pos["margin"]
                        size = pos["size"]

                        closed = False
                        result_pct = 0.0
                        reason = ""
                        exit_price = None

                        if side == "LONG":
                            if low <= sl:
                                result_pct = (sl - entry) / entry * 100
                                reason = "SL (Zarar Durdur)"
                                exit_price = sl
                                closed = True
                            elif data["long_exit_signal"]:
                                result_pct = (price - entry) / entry * 100
                                reason = "TP (Gösterge Dönüşü)"
                                exit_price = price
                                closed = True

                        elif side == "SHORT":
                            if high >= sl:
                                result_pct = (entry - sl) / entry * 100
                                reason = "SL (Zarar Durdur)"
                                exit_price = sl
                                closed = True
                            elif data["short_exit_signal"]:
                                result_pct = (entry - price) / entry * 100
                                reason = "TP (Gösterge Dönüşü)"
                                exit_price = price
                                closed = True

                        if closed:
                            pnl = size * result_pct / 100
                            balance += pnl
                            used_margin -= margin

                            log.info(f">>> KAPANDI #{pos['id']} {symbol} | {reason} | K/Z: {pnl:+.2f} USDT")

                            send_telegram(
                                f"📉 POZİSYON KAPANDI #{pos['id']}\n"
                                f"{symbol}\n"
                                f"Sonuç: {reason}\n"
                                f"Giriş: {entry:.6f} | Çıkış: {exit_price:.6f}\n"
                                f"K/Z: {pnl:+.2f} USDT\n"
                                f"Bakiye: {balance:.2f} USDT"
                            )
                            del positions[symbol]

                        continue  # Pozisyon varken yeni sinyal arama

                    # --- Yeni pozisyon açma ---
                    if signal in ("LONG", "SHORT"):
                        free_balance = balance - used_margin
                        if free_balance < MARGIN_PER_TRADE:
                            continue  # Yeterli serbest bakiye yok

                        sl = data["long_sl"] if signal == "LONG" else data["short_sl"]
                        if sl is None:
                            continue

                        trade_number += 1
                        margin = MARGIN_PER_TRADE
                        size = margin * LEVERAGE          # 10 * 10 = 100 USDT notional

                        positions[symbol] = {
                            "id": trade_number,
                            "symbol": symbol,
                            "side": signal,
                            "entry": price,
                            "sl": sl,
                            "size": size,
                            "margin": margin,
                            "leverage": LEVERAGE,
                            "opened_at": now_text(),
                            "just_opened": True,
                        }
                        used_margin += margin
                        signals_found += 1

                        log.info(
                            f">>> AÇILDI #{trade_number} {symbol} | {signal} | "
                            f"Giriş: {price:.6f} | SL: {sl:.6f} | Margin: {margin} USDT"
                        )

                        send_telegram(
                            f"🚀 YENİ SANAL POZİSYON #{trade_number}\n"
                            f"{symbol}\n"
                            f"Yön: {signal}\n"
                            f"Giriş: {price:.6f}\n"
                            f"SL: {sl:.6f}\n"
                            f"Margin: {margin} USDT | Notional: {size} USDT\n"
                            f"Skor: {data['score']} | {data['reasons']}"
                        )

                except Exception as e:
                    errors += 1
                    # Çok fazla log basmamak için sadece kritik hataları yaz
                    if errors <= 5 or errors % 50 == 0:
                        log.warning(f"{symbol} hata: {e}")

            elapsed = time.time() - cycle_start
            free = balance - used_margin
            log.info(
                f"Tarama bitti | Taranan: {scanned}/{len(all_symbols)} | "
                f"Yeni sinyal: {signals_found} | Hata: {errors} | "
                f"Süre: {elapsed:.1f}s"
            )
            log.info(
                f"📊 PORTFÖY → Bakiye: {balance:.2f} | Kullanılan Margin: {used_margin:.2f} | "
                f"Serbest: {free:.2f} | Açık Pozisyon: {len(positions)}"
            )

            # Bir sonraki döngüye kadar bekle
            sleep_time = max(10, LOOP_SECONDS - elapsed)
            log.info(f"Sonraki tarama için {sleep_time:.0f} saniye bekleniyor...")
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            log.info("Test kullanıcı tarafından durduruldu.")
            break
        except Exception as e:
            log.exception(f"GENEL HATA: {e}")
            time.sleep(10)


if __name__ == "__main__":
    run_simulation()
