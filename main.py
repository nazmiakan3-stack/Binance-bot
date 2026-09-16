#!/usr/bin/env python3
import json
import os
import time
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# Telegram Bilgileri (GitHub güvenliği için sistem ortam değişkenlerinden alınır)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
BASE_URL = "https://fapi.binance.com/fapi/v1/klines"

INTERVAL = "15m"
LIMIT = 250

# Strateji ve Parametreler
MIN_SCORE_THRESHOLD = 4
SL_ATR_MULTIPLIER = 1.8
TP_ATR_MULTIPLIER = 3.6

LEVERAGE = 10.0            
MARGIN_PERCENT = 0.05      
STARTING_BALANCE = 500.0   
COMMISSION_RATE = 0.0004

REQUEST_TIMEOUT = 20
RETRY_COUNT = 5
RETRY_DELAY_BASE = 2
LOOP_SECONDS = 60
VERSION = "10.4-SecureScanner"

OS_LEVEL = 30
OB_LEVEL = 70


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode("utf-8")
    req = Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": f"Termux-PerpetualScanner/{VERSION}"},
        method="POST"
    )
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return response.status == 200
        except Exception:
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY_BASE * attempt)
    return False


def http_get_json(url):
    last_error = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            req = Request(url, headers={"User-Agent": f"PerpetualScanner/{VERSION}"})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            last_error = e
            if e.code == 451:
                raise RuntimeError("Bölge kısıtlaması (HTTP 451). VPN kullanın.")
            if e.code in (429, 418):
                time.sleep(RETRY_DELAY_BASE * (2 ** attempt))
                continue
        except (URLError, TimeoutError, OSError, ValueError) as e:
            last_error = e
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY_BASE * attempt)
                continue
    raise RuntimeError(f"Binance bağlantısı başarısız: {last_error}")


def get_active_usdt_symbols():
    try:
        data = http_get_json(EXCHANGE_INFO_URL)
        symbols = []
        for s in data.get("symbols", []):
            if (
                s.get("status") == "TRADING" 
                and s.get("quoteAsset") == "USDT" 
                and s.get("contractType") == "PERPETUAL"
            ):
                symbols.append(s["symbol"])
        return symbols
    except Exception as e:
        print(f"Sembol listesi alınamadı: {e}")
        return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XAUUSDT", "XAGUSDT"]


def get_klines(symbol, interval=INTERVAL, limit=LIMIT):
    url = f"{BASE_URL}?symbol={symbol}&interval={interval}&limit={limit}"
    return http_get_json(url)


def parse_klines(data):
    closes = [float(row[4]) for row in data]
    highs = [float(row[2]) for row in data]
    lows = [float(row[3]) for row in data]
    volumes = [float(row[5]) for row in data]
    return closes, highs, lows, volumes


def ema_series(values, period):
    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    ema_list = [sum(values[:period]) / period]
    for price in values[period:]:
        ema_list.append((price - ema_list[-1]) * multiplier + ema_list[-1])
    return ema_list


def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def bollinger_bands(values, period=20, std_dev=2):
    if len(values) < period:
        return None, None, None
    sma_val = sum(values[-period:]) / period
    variance = sum((x - sma_val) ** 2 for x in values[-period:]) / period
    std = variance ** 0.5
    return sma_val + (std * std_dev), sma_val, sma_val - (std * std_dev)


def rsi_series(values, period=14):
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
    if not fast_ema or not slow_ema:
        return [], []
    diff = len(fast_ema) - len(slow_ema)
    fast_ema = fast_ema[diff:]
    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = ema_series(macd_line, signal)
    if not signal_line:
        return macd_line, []
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


def analyze_symbol(symbol):
    data = get_klines(symbol, INTERVAL)
    if len(data) < 200:
        return None

    closed_data = data[:-1]
    closes, highs, lows, volumes = parse_klines(closed_data)

    price = closes[-1]
    high = highs[-1]
    low = lows[-1]

    stoch_k, stoch_d = stoch_rsi_series(closes)
    kdj_k, kdj_d, _ = kdj_series(closes, highs, lows)
    ema5_series = ema_series(closes, 5)
    ema200_series = ema_series(closes, 200)
    macd_line, macd_sig = macd_series(closes)
    vol_ma20 = sma(volumes, 20)
    atr14 = atr(closed_data, 14)
    bb_upper, bb_mid, bb_lower = bollinger_bands(closes, 20, 2)

    if not (stoch_k and kdj_k and ema5_series and ema200_series and macd_line and atr14 and bb_upper):
        return None

    sk_curr, sk_prev = stoch_k[-1], stoch_k[-2]
    sd_curr, sd_prev = stoch_d[-1], stoch_d[-2]
    kk_curr, kk_prev = kdj_k[-1], kdj_k[-2]
    kd_curr, kd_prev = kdj_d[-1], kdj_d[-2]
    e5_curr, e5_prev = ema5_series[-1], ema5_series[-2]
    m_curr = macd_line[-1]
    ms_curr = macd_sig[-1] if macd_sig else 0
    curr_vol = volumes[-1]

    c_bb_long     = low <= bb_lower or price <= bb_lower * 1.002
    c_stoch_long  = (sk_prev < OS_LEVEL or sd_prev < OS_LEVEL) and (sk_curr > sd_curr)
    c_kdj_long    = (kk_prev < OS_LEVEL or kd_prev < OS_LEVEL) and (kk_curr > kd_curr)
    c_ema_long    = e5_curr > e5_prev
    c_macd_long   = m_curr > ms_curr
    c_vol_long    = vol_ma20 and (curr_vol > vol_ma20)
    
    long_conditions = [c_bb_long, c_stoch_long, c_kdj_long, c_ema_long, c_macd_long, c_vol_long]
    long_score = sum(long_conditions)

    c_bb_short    = high >= bb_upper or price >= bb_upper * 0.998
    c_stoch_short = (sk_prev > OB_LEVEL or sd_prev > OB_LEVEL) and (sk_curr < sd_curr)
    c_kdj_short   = (kk_prev > OB_LEVEL or kd_prev > OB_LEVEL) and (kk_curr < kd_curr)
    c_ema_short   = e5_curr < e5_prev
    c_macd_short  = m_curr < ms_curr
    c_vol_short   = vol_ma20 and (curr_vol > vol_ma20)
    
    short_conditions = [c_bb_short, c_stoch_short, c_kdj_short, c_ema_short, c_macd_short, c_vol_short]
    short_score = sum(short_conditions)

    signal = "BEKLE"
    score = 0

    if long_score >= MIN_SCORE_THRESHOLD and long_score > short_score:
        signal = "LONG"
        score = long_score
    elif short_score >= MIN_SCORE_THRESHOLD and short_score > long_score:
        signal = "SHORT"
        score = short_score

    long_sl = long_tp = short_sl = short_tp = None
    if atr14:
        long_sl  = price - atr14 * SL_ATR_MULTIPLIER
        long_tp  = price + atr14 * TP_ATR_MULTIPLIER
        short_sl = price + atr14 * SL_ATR_MULTIPLIER
        short_tp = price - atr14 * TP_ATR_MULTIPLIER

    return {
        "signal": signal,
        "price": price,
        "high": high,
        "low": low,
        "long_sl": long_sl,
        "long_tp": long_tp,
        "short_sl": short_sl,
        "short_tp": short_tp,
        "score": score,
        "atr": atr14
    }


def print_portfolio_status(global_balance, history, positions):
    print("\n" + "=" * 65)
    print("📊 GENEL CÜZDAN VE PORTFÖY RAPORU (ISOLATED - %5 MARJİN - 10x - TRAILING)")
    print("=" * 65)
    print(f"💰 Toplam Cüzdan Bakiyesi: {global_balance:10.2f} USDT")
    
    total_realized_pnl = sum(t["pnl"] for t in history)
    wins = len([t for t in history if t["pnl"] > 0])
    total_trades = len(history)
    wr = (wins / total_trades * 100) if total_trades > 0 else 0
    
    print(f"📈 Toplam Gerçekleşen K/Z : {total_realized_pnl:+10.2f} USDT | İşlem: {total_trades} (Başarı: %{wr:.0f})")
    print("-" * 65)
    print("📌 AÇIK İŞLEMLER (Dinamik Takipli):")
    if not positions:
        print("   (Şu an açık pozisyon yok)")
    else:
        for sym, pos in positions.items():
            print(f"   • {sym} | Yön: {pos['side']} | Giriş: {pos['entry']:.4f} | SL: {pos['sl']:.4f} | "
                  f"Marjin: {pos['margin']:.2f} USDT | Kaldıraç: {pos['leverage']}x")
    print("=" * 65)


def run_bot():
    print("=" * 65)
    print(f"  BİNANCE PERPETUAL VADELİ COİNLER TARAYICI v{VERSION}")
    print(f"  Mod: İzole (Trailing Stop) | Marjin: %{MARGIN_PERCENT*100:.0f} | Kaldıraç: {LEVERAGE}x | Bakiye: {STARTING_BALANCE} USDT")
    print("=" * 65)

    send_telegram(f"🟢 PERPETUAL TARAYICI (TRAILING) BAŞLADI (v{VERSION})\nMod: İzole | %5 Marjin | {LEVERAGE}x | Bakiye: {STARTING_BALANCE}")

    global_balance = STARTING_BALANCE
    positions = {}
    history = []
    trade_id = 0

    while True:
        try:
            print(f"\n⏱ {now_text()} - Aktif Perpetual Vadeli Piyasalar Taranıyor...")
            symbols = get_active_usdt_symbols()
            print(f"Taranan Perpetual USDT parite sayısı: {len(symbols)}")

            found_signals = []

            for symbol in symbols:
                try:
                    res = analyze_symbol(symbol)
                    if not res:
                        continue
                    
                    if symbol not in positions and res["signal"] in ("LONG", "SHORT"):
                        found_signals.append((symbol, res))
                    
                    elif symbol in positions:
                        pos = positions[symbol]
                        price = res["price"]
                        high = res["high"]
                        low = res["low"]
                        curr_atr = res["atr"] if res["atr"] else (pos["tp"] - pos["entry"]) / TP_ATR_MULTIPLIER
                        
                        closed = False
                        result_pct = 0.0
                        reason = ""
                        exit_price = None

                        if pos["side"] == "LONG":
                            potential_new_sl = high - (curr_atr * SL_ATR_MULTIPLIER)
                            if potential_new_sl > pos["sl"]:
                                pos["sl"] = potential_new_sl

                            if high >= pos["tp"]:
                                result_pct = (pos["tp"] - pos["entry"]) / pos["entry"] * 100
                                reason, exit_price, closed = "TP", pos["tp"], True
                            elif low <= pos["sl"]:
                                result_pct = (pos["sl"] - pos["entry"]) / pos["entry"] * 100
                                reason, exit_price, closed = "TRAILING-SL", pos["sl"], True
                        else:
                            potential_new_sl = low + (curr_atr * SL_ATR_MULTIPLIER)
                            if potential_new_sl < pos["sl"]:
                                pos["sl"] = potential_new_sl

                            if low <= pos["tp"]:
                                result_pct = (pos["entry"] - pos["tp"]) / pos["entry"] * 100
                                reason, exit_price, closed = "TP", pos["tp"], True
                            elif high >= pos["sl"]:
                                result_pct = (pos["entry"] - pos["sl"]) / pos["entry"] * 100
                                reason, exit_price, closed = "TRAILING-SL", pos["sl"], True

                        if closed:
                            gross_pnl = pos["margin"] * pos["leverage"] * (result_pct / 100)
                            commission = (pos["size"] * COMMISSION_RATE) * 2
                            net_pnl = gross_pnl - commission
                            
                            global_balance += net_pnl

                            history.append({
                                "id": pos["id"], "symbol": symbol, "side": pos["side"],
                                "entry": pos["entry"], "exit": exit_price, "reason": reason, "pnl": net_pnl
                            })

                            icon = "🟢" if net_pnl > 0 else "🔴"
                            print(f"  >>> {icon} POZİSYON KAPANDI #{pos['id']} | {symbol} {pos['side']} | {reason} | K/Z: {net_pnl:+.2f} USDT")
                            send_telegram(
                                f"{icon} İZOLE POZİSYON KAPANDI #{pos['id']}\n"
                                f"Parite: {symbol} ({pos['side']})\nSebep: {reason}\n"
                                f"Net K/Z: {net_pnl:+.2f} USDT\nGüncel Cüzdan: {global_balance:.2f} USDT"
                            )
                            del positions[symbol]

                    time.sleep(0.05)
                except Exception:
                    continue

            for symbol, data in found_signals:
                signal = data["signal"]
                price = data["price"]
                tp = data["long_tp"] if signal == "LONG" else data["short_tp"]
                sl = data["long_sl"] if signal == "LONG" else data["short_sl"]

                if not tp or not sl:
                    continue

                trade_id += 1
                margin = global_balance * MARGIN_PERCENT
                size = margin * LEVERAGE

                positions[symbol] = {
                    "id": trade_id,
                    "symbol": symbol,
                    "side": signal,
                    "entry": price,
                    "tp": tp,
                    "sl": sl,
                    "margin": margin,
                    "size": size,
                    "leverage": LEVERAGE,
                    "opened_at": now_text()
                }

                print(f"  >>> 🚀 YENİ İZOLE İŞLEM #{trade_id} | {symbol} | {signal} @ {price:.4f} | Marjin: {margin:.2f} USDT")
                send_telegram(
                    f"🚀 YENİ İZOLE İŞLEM AÇILDI #{trade_id} (Dinamik Takipli)\n"
                    f"Parite: {symbol}\nYön: {signal}\nGiriş: {price:.4f}\n"
                    f"İzole Marjin: {margin:.2f} USDT (%5) | Kaldıraç: {LEVERAGE}x\n"
                    f"İlk TP: {tp:.4f} | İlk SL: {sl:.4f}"
                )

            print_portfolio_status(global_balance, history, positions)

            time.sleep(LOOP_SECONDS)

        except KeyboardInterrupt:
            print("\nProgram kullanıcı tarafından durduruldu.")
            print_portfolio_status(global_balance, history, positions)
            break
        except Exception as e:
            print(f"\nGENEL HATA DÖNGÜSÜ: {e}")
            time.sleep(15)


if __name__ == "__main__":
    run_bot()
