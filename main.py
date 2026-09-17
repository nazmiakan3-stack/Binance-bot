#!/usr/bin/env python3
"""
Binance Futures 15m Multi-Coin Simulator v12.0
- Risk bazlı pozisyon büyüklüğü (% risk)
- Akıllı Trailing (önce BE, sonra ATR trail)
- CSV işlem kaydı
- Detaylı istatistik + portföy takibi
- Gerçek emir YOK
"""

import json
import os
import time
import logging
import csv
from datetime import datetime
from urllib.request import Request, urlopen

# ==================== YAPILANDIRMA ====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://fapi.binance.com/fapi/v1"
EXCHANGE_INFO_URL = f"{BASE_URL}/exchangeInfo"
TICKER_24H_URL = f"{BASE_URL}/ticker/24hr"
KLINES_URL = f"{BASE_URL}/klines"

INTERVAL = "15m"
LIMIT = 200

# --- Risk & Filtre ---
SL_ATR_MULTIPLIER = 1.3
TRAIL_ATR_MULTIPLIER = 1.6
BREAKEVEN_R = 0.8                # +0.8R olunca SL'yi girişe çek
LEVERAGE = 10.0
RISK_PERCENT = 1.5               # Her işlemde bakiyenin %1.5'ini riske at
STARTING_BALANCE = 500.0
MAX_OPEN_POSITIONS = 5
MIN_QUOTE_VOLUME = 50_000_000
MIN_SIGNAL_SCORE = 5

REQUEST_TIMEOUT = 12
RETRY_COUNT = 2
RETRY_DELAY = 2
LOOP_SECONDS = 80
REQUEST_DELAY = 0.07

CSV_FILE = "trades_v12.csv"

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot_v12.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("multi-v12")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": "MultiCoinSimulator/12.0",
    }, method="POST")
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
            req = Request(url, headers={"User-Agent": "MultiCoinSimulator/12.0"})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            last_error = e
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
    raise RuntimeError(f"Binance bağlantısı başarısız: {last_error}")


def init_csv():
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "id", "symbol", "side", "entry", "exit", "pnl", "pnl_pct",
                "reason", "opened_at", "closed_at", "risk_usdt", "size"
            ])


def save_trade_to_csv(trade: dict):
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            trade["id"], trade["symbol"], trade["side"],
            f"{trade['entry']:.6f}", f"{trade['exit']:.6f}",
            f"{trade['pnl']:.4f}", f"{trade['pnl_pct']:.3f}",
            trade["reason"], trade["opened_at"], trade["closed_at"],
            f"{trade.get('risk_usdt', 0):.2f}", f"{trade.get('size', 0):.2f}"
        ])


def get_high_volume_symbols() -> list:
    exchange = http_get_json(EXCHANGE_INFO_URL)
    valid = set()
    for s in exchange.get("symbols", []):
        if (s.get("quoteAsset") == "USDT" and
            s.get("contractType") == "PERPETUAL" and
            s.get("status") == "TRADING" and
            s.get("symbol", "").endswith("USDT")):
            valid.add(s["symbol"])

    tickers = http_get_json(TICKER_24H_URL)
    symbols = []
    for t in tickers:
        sym = t.get("symbol")
        if sym not in valid:
            continue
        try:
            if float(t.get("quoteVolume", 0)) >= MIN_QUOTE_VOLUME:
                symbols.append(sym)
        except (TypeError, ValueError):
            continue
    symbols.sort()
    log.info(f"Yüksek hacimli sembol: {len(symbols)} (min {MIN_QUOTE_VOLUME/1e6:.0f}M USDT)")
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
    return sma_val + std * std_dev, sma_val, sma_val - std * std_dev


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
    rsi_list = [100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))]
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
    k_vals = [sum(raw_stoch[i - smooth_k + 1:i + 1]) / smooth_k for i in range(smooth_k - 1, len(raw_stoch))]
    if len(k_vals) < smooth_d:
        return [], []
    d_vals = [sum(k_vals[i - smooth_d + 1:i + 1]) / smooth_d for i in range(smooth_d - 1, len(k_vals))]
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
        h, l, pc = float(data[i][2]), float(data[i][3]), float(data[i-1][4])
        true_ranges.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(true_ranges) < period:
        return None
    result = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        result = ((result * (period - 1)) + tr) / period
    return result


def analyze_15m(symbol: str):
    data = get_klines(symbol, INTERVAL)
    if len(data) < 180:
        return None

    closed_data = data[:-1]
    closes, highs, lows, volumes = parse_klines(closed_data)

    price = closes[-1]
    high = highs[-1]
    low = lows[-1]

    stoch_k, stoch_d = stoch_rsi_series(closes)
    kdj_k, kdj_d, _ = kdj_series(closes, highs, lows)
    ema5 = ema_series(closes, 5)
    ema20 = ema_series(closes, 20)
    ema50 = ema_series(closes, 50)
    ema200 = ema_series(closes, 200)
    macd_line, macd_sig = macd_series(closes)
    vol_ma20 = sma(volumes, 20)
    atr14 = atr(closed_data, 14)
    bb_upper, bb_mid, bb_lower = bollinger_bands(closes, 20, 2)

    if not all([stoch_k, kdj_k, ema5, ema20, ema50, ema200, macd_line, atr14, bb_upper]):
        return None

    sk_c, sk_p = stoch_k[-1], stoch_k[-2]
    sd_c = stoch_d[-1]
    kk_c, kk_p = kdj_k[-1], kdj_k[-2]
    kd_c = kdj_d[-1]
    e5_c, e5_p = ema5[-1], ema5[-2]
    e20_c = ema20[-1]
    e50_c = ema50[-1]
    e200_c = ema200[-1]
    m_c, m_p = macd_line[-1], macd_line[-2]
    ms_c = macd_sig[-1]
    curr_vol = volumes[-1]

    # LONG
    c_bb_long     = price <= bb_lower * 1.003 or low <= bb_lower
    c_stoch_long  = sk_c > sd_c and sk_c < 45
    c_kdj_long    = kk_c > kd_c and kk_c < 50
    c_ema_long    = e5_c > e5_p and price > e20_c
    c_macd_long   = m_c > ms_c and m_c > m_p
    c_vol_long    = vol_ma20 and curr_vol > vol_ma20 * 1.1
    c_trend_long  = e50_c > e200_c and price > e50_c

    long_conds = [c_bb_long, c_stoch_long, c_kdj_long, c_ema_long, c_macd_long, c_vol_long]
    long_score = sum(long_conds)

    # SHORT
    c_bb_short    = price >= bb_upper * 0.997 or high >= bb_upper
    c_stoch_short = sk_c < sd_c and sk_c > 55
    c_kdj_short   = kk_c < kd_c and kk_c > 50
    c_ema_short   = e5_c < e5_p and price < e20_c
    c_macd_short  = m_c < ms_c and m_c < m_p
    c_vol_short   = vol_ma20 and curr_vol > vol_ma20 * 1.1
    c_trend_short = e50_c < e200_c and price < e50_c

    short_conds = [c_bb_short, c_stoch_short, c_kdj_short, c_ema_short, c_macd_short, c_vol_short]
    short_score = sum(short_conds)

    long_exit  = (sk_c < sd_c and sk_c > 70) or (e5_c < e5_p and price < e5_c)
    short_exit = (sk_c > sd_c and sk_c < 30) or (e5_c > e5_p and price > e5_c)

    signal = "BEKLE"
    score = 0
    reasons = []

    if long_score >= MIN_SIGNAL_SCORE and long_score > short_score and c_trend_long:
        signal = "LONG"
        score = long_score
        for name, cond in [("BB", c_bb_long), ("Stoch", c_stoch_long), ("KDJ", c_kdj_long),
                           ("EMA", c_ema_long), ("MACD", c_macd_long), ("Vol", c_vol_long)]:
            if cond: reasons.append(name)
        reasons.append("Trend↑")

    elif short_score >= MIN_SIGNAL_SCORE and short_score > long_score and c_trend_short:
        signal = "SHORT"
        score = -short_score
        for name, cond in [("BB", c_bb_short), ("Stoch", c_stoch_short), ("KDJ", c_kdj_short),
                           ("EMA", c_ema_short), ("MACD", c_macd_short), ("Vol", c_vol_short)]:
            if cond: reasons.append(name)
        reasons.append("Trend↓")

    long_sl = price - atr14 * SL_ATR_MULTIPLIER if atr14 else None
    short_sl = price + atr14 * SL_ATR_MULTIPLIER if atr14 else None

    return {
        "signal": signal,
        "price": price,
        "high": high,
        "low": low,
        "long_sl": long_sl,
        "short_sl": short_sl,
        "atr": atr14,
        "long_exit_signal": long_exit,
        "short_exit_signal": short_exit,
        "score": score,
        "reasons": ", ".join(reasons) if reasons else "-",
    }


def calculate_position_size(balance: float, entry: float, sl: float, side: str) -> tuple:
    """Risk bazlı pozisyon büyüklüğü hesaplar. (size, margin, risk_usdt)"""
    risk_usdt = balance * (RISK_PERCENT / 100)
    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        return 0, 0, 0

    # Kayıp = size * (sl_distance / entry)
    # risk_usdt = size * (sl_distance / entry)
    size = risk_usdt / (sl_distance / entry)
    margin = size / LEVERAGE
    return size, margin, risk_usdt


def print_stats(balance, peak_balance, closed_trades, positions, used_margin):
    total = len(closed_trades)
    if total == 0:
        winrate = total_pnl = avg_pnl = best = worst = 0.0
    else:
        wins = sum(1 for t in closed_trades if t["pnl"] > 0)
        winrate = wins / total * 100
        total_pnl = sum(t["pnl"] for t in closed_trades)
        avg_pnl = total_pnl / total
        best = max(t["pnl"] for t in closed_trades)
        worst = min(t["pnl"] for t in closed_trades)

    drawdown = (peak_balance - balance) / peak_balance * 100 if peak_balance > 0 else 0
    free = balance - used_margin

    log.info("=" * 70)
    log.info(f"📊 PORTFÖY ÖZETİ | {now_text()}")
    log.info(f"   Bakiye      : {balance:.2f} USDT")
    log.info(f"   Kullanılan  : {used_margin:.2f} USDT")
    log.info(f"   Serbest     : {free:.2f} USDT")
    log.info(f"   Peak        : {peak_balance:.2f} USDT")
    log.info(f"   Drawdown    : {drawdown:.2f}%")
    log.info(f"   Açık Poz.   : {len(positions)}/{MAX_OPEN_POSITIONS}")
    log.info("-" * 70)
    log.info(f"📈 KAPALI İŞLEMLER")
    log.info(f"   Toplam      : {total}")
    log.info(f"   Winrate     : {winrate:.1f}%")
    log.info(f"   Toplam K/Z  : {total_pnl:+.2f} USDT")
    log.info(f"   Ortalama    : {avg_pnl:+.2f} USDT")
    log.info(f"   En İyi      : {best:+.2f} | En Kötü: {worst:+.2f}")
    log.info("=" * 70)

    if positions:
        log.info("📌 AÇIK POZİSYONLAR:")
        for sym, pos in positions.items():
            log.info(f"   #{pos['id']} {sym} {pos['side']} | Giriş: {pos['entry']:.6f} | SL: {pos['sl']:.6f} | Risk: {pos.get('risk_usdt',0):.2f}")


def run_simulation():
    log.info("=" * 70)
    log.info("   BINANCE FUTURES MULTI-COIN 15m SİMÜLATÖR v12.0")
    log.info("   RISK BAZLI + AKILLI TRAILING + CSV KAYIT")
    log.info("   GERÇEK EMİR YOK")
    log.info(f"   Başlangıç: {STARTING_BALANCE} | Risk: %{RISK_PERCENT} | Max Poz: {MAX_OPEN_POSITIONS}")
    log.info(f"   Min Skor: {MIN_SIGNAL_SCORE}/6 | Min Hacim: {MIN_QUOTE_VOLUME/1e6:.0f}M")
    log.info("=" * 70)

    init_csv()

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram tanımlı değil → bildirimler kapalı.")

    try:
        all_symbols = get_high_volume_symbols()
    except Exception as e:
        log.error(f"Sembol listesi alınamadı: {e}")
        return

    if not all_symbols:
        log.error("Uygun sembol bulunamadı.")
        return

    balance = STARTING_BALANCE
    peak_balance = STARTING_BALANCE
    used_margin = 0.0
    positions = {}
    closed_trades = []
    trade_number = 0

    last_symbol_refresh = time.time()
    last_stats_print = 0
    STATS_INTERVAL = 300

    while True:
        try:
            if time.time() - last_symbol_refresh > 6 * 3600:
                try:
                    all_symbols = get_high_volume_symbols()
                    last_symbol_refresh = time.time()
                except Exception as e:
                    log.warning(f"Sembol yenileme hatası: {e}")

            cycle_start = time.time()
            log.info("-" * 70)
            log.info(f"Döngü | Açık: {len(positions)}/{MAX_OPEN_POSITIONS} | Serbest: {balance - used_margin:.2f}")

            scanned = signals_found = errors = 0

            for symbol in all_symbols:
                try:
                    time.sleep(REQUEST_DELAY)
                    data = analyze_15m(symbol)
                    scanned += 1
                    if data is None:
                        continue

                    price = data["price"]
                    high = data["high"]
                    low = data["low"]
                    atr_val = data["atr"]

                    # ===== Açık pozisyon yönetimi =====
                    if symbol in positions:
                        pos = positions[symbol]
                        if pos.get("just_opened"):
                            pos["just_opened"] = False
                            continue

                        entry = pos["entry"]
                        side = pos["side"]
                        sl = pos["sl"]
                        size = pos["size"]
                        margin = pos["margin"]
                        closed = False
                        result_pct = 0.0
                        reason = ""
                        exit_price = None

                        # --- Akıllı Trailing ---
                        r_distance = abs(entry - pos["initial_sl"])
                        if side == "LONG":
                            current_r = (price - entry) / r_distance if r_distance > 0 else 0
                            # Breakeven
                            if current_r >= BREAKEVEN_R and sl < entry:
                                pos["sl"] = entry
                                sl = entry
                                log.info(f"   #{pos['id']} {symbol} → Breakeven'e çekildi")
                            # Trailing
                            if atr_val:
                                new_trail = price - atr_val * TRAIL_ATR_MULTIPLIER
                                if new_trail > sl:
                                    pos["sl"] = new_trail
                                    sl = new_trail
                        else:
                            current_r = (entry - price) / r_distance if r_distance > 0 else 0
                            if current_r >= BREAKEVEN_R and sl > entry:
                                pos["sl"] = entry
                                sl = entry
                                log.info(f"   #{pos['id']} {symbol} → Breakeven'e çekildi")
                            if atr_val:
                                new_trail = price + atr_val * TRAIL_ATR_MULTIPLIER
                                if new_trail < sl:
                                    pos["sl"] = new_trail
                                    sl = new_trail

                        # Çıkış kontrolü
                        if side == "LONG":
                            if low <= sl:
                                result_pct = (sl - entry) / entry * 100
                                reason = "SL/Trailing"
                                exit_price = sl
                                closed = True
                            elif data["long_exit_signal"]:
                                result_pct = (price - entry) / entry * 100
                                reason = "Gösterge"
                                exit_price = price
                                closed = True
                        else:
                            if high >= sl:
                                result_pct = (entry - sl) / entry * 100
                                reason = "SL/Trailing"
                                exit_price = sl
                                closed = True
                            elif data["short_exit_signal"]:
                                result_pct = (entry - price) / entry * 100
                                reason = "Gösterge"
                                exit_price = price
                                closed = True

                        if closed:
                            pnl = size * result_pct / 100
                            balance += pnl
                            used_margin -= margin
                            peak_balance = max(peak_balance, balance)

                            trade_record = {
                                "id": pos["id"],
                                "symbol": symbol,
                                "side": side,
                                "entry": entry,
                                "exit": exit_price,
                                "pnl": pnl,
                                "pnl_pct": result_pct,
                                "reason": reason,
                                "opened_at": pos["opened_at"],
                                "closed_at": now_text(),
                                "risk_usdt": pos.get("risk_usdt", 0),
                                "size": size,
                            }
                            closed_trades.append(trade_record)
                            save_trade_to_csv(trade_record)

                            log.info(f">>> KAPANDI #{pos['id']} {symbol} {side} | {reason} | K/Z: {pnl:+.2f} ({result_pct:+.2f}%) | Bakiye: {balance:.2f}")

                            send_telegram(
                                f"📉 KAPANDI #{pos['id']}\n{symbol} {side}\n"
                                f"Sebep: {reason}\n"
                                f"Giriş: {entry:.6f} → {exit_price:.6f}\n"
                                f"K/Z: {pnl:+.2f} USDT ({result_pct:+.2f}%)\n"
                                f"Bakiye: {balance:.2f}"
                            )
                            del positions[symbol]
                        continue

                    # ===== Yeni pozisyon =====
                    signal = data["signal"]
                    if signal in ("LONG", "SHORT"):
                        if len(positions) >= MAX_OPEN_POSITIONS:
                            continue

                        sl = data["long_sl"] if signal == "LONG" else data["short_sl"]
                        if sl is None:
                            continue

                        size, margin, risk_usdt = calculate_position_size(balance, price, sl, signal)
                        if margin <= 0 or size <= 0:
                            continue
                        if (balance - used_margin) < margin:
                            continue

                        trade_number += 1
                        positions[symbol] = {
                            "id": trade_number,
                            "symbol": symbol,
                            "side": signal,
                            "entry": price,
                            "sl": sl,
                            "initial_sl": sl,
                            "size": size,
                            "margin": margin,
                            "risk_usdt": risk_usdt,
                            "opened_at": now_text(),
                            "just_opened": True,
                        }
                        used_margin += margin
                        signals_found += 1

                        log.info(
                            f">>> AÇILDI #{trade_number} {symbol} {signal} | "
                            f"Giriş: {price:.6f} | SL: {sl:.6f} | "
                            f"Risk: {risk_usdt:.2f} USDT | Size: {size:.1f} | "
                            f"Skor: {abs(data['score'])}/6 | {data['reasons']}"
                        )

                        send_telegram(
                            f"🚀 YENİ #{trade_number}\n{symbol} {signal}\n"
                            f"Giriş: {price:.6f}\nSL: {sl:.6f}\n"
                            f"Risk: {risk_usdt:.2f} USDT\n"
                            f"Skor: {abs(data['score'])}/6 | {data['reasons']}"
                        )

                except Exception as e:
                    errors += 1
                    if errors <= 3 or errors % 20 == 0:
                        log.warning(f"{symbol} hata: {e}")

            elapsed = time.time() - cycle_start
            log.info(f"Tarama bitti | Taranan: {scanned} | Yeni: {signals_found} | Hata: {errors} | Süre: {elapsed:.1f}s")

            if time.time() - last_stats_print > STATS_INTERVAL:
                print_stats(balance, peak_balance, closed_trades, positions, used_margin)
                last_stats_print = time.time()

            sleep_time = max(20, LOOP_SECONDS - elapsed)
            log.info(f"Sonraki tarama: {sleep_time:.0f} sn")
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            log.info("Durduruldu.")
            print_stats(balance, peak_balance, closed_trades, positions, used_margin)
            break
        except Exception as e:
            log.exception(f"GENEL HATA: {e}")
            time.sleep(15)


if __name__ == "__main__":
    run_simulation()
