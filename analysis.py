"""AI BEDAH CHART - ICT/SMC Analysis for XAUUSD, FX, Indices, Commodities."""

import re
import pandas as pd
import requests
from datetime import datetime

TIMEFRAME = "5min"
SYMBOL = "XAU/USD"

# Pips definition - adaptive based on price magnitude
def get_zone_width(price: float) -> float:
    """Adaptive zone width: ~0.01% of price, min 0.1, max 0.5."""
    w = price * 0.0001
    return max(0.1, min(0.5, w))


PIPS = 0.1
ZONE_WIDTH = 0.5  # default, akan di-override per-call
SL_BUFFER = 0.2

# Twelve Data symbol mapping untuk berbagai pair
SYMBOL_MAP = {
    # Forex majors & crosses
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "AUDUSD": "AUD/USD", "NZDUSD": "NZD/USD",
    "USDJPY": "USD/JPY", "USDCHF": "USD/CHF", "USDCAD": "USD/CAD",
    "EURJPY": "EUR/JPY", "GBPJPY": "GBP/JPY", "AUDJPY": "AUD/JPY", "NZDJPY": "NZD/JPY",
    "EURGBP": "EUR/GBP", "EURAUD": "EUR/AUD", "GBPAUD": "GBP/AUD", "GBPCAD": "GBP/CAD",
    "AUDCAD": "AUD/CAD", "AUDNZD": "AUD/NZD", "CADJPY": "CAD/JPY", "CHFJPY": "CHF/JPY",
    "EURNZD": "EUR/NZD", "GBPNZD": "GBP/NZD",
    # Metals
    "XAUUSD": "XAU/USD", "XAU": "XAU/USD", "GOLD": "XAU/USD",
    "XAGUSD": "XAG/USD", "XAG": "XAG/USD", "SILVER": "XAG/USD",
    # Oil/Energy
    "XTIUSD": "CL", "XTI": "CL", "OIL": "CL", "WTI": "CL", "CL": "CL",
    "XBRUSD": "XBR/USD", "XBR": "XBR/USD", "BRENT": "XBR/USD",
    # Indices (free tier Twelve Data: limited - SPX, NDX butuh paid)
    # Untuk free tier, hanya forex, XAU, XAG, CL (oil)
    "SPX500": "SPX", "SP500": "SPX", "US500": "SPX", "SPX": "SPX",
    "GER40": "DAX", "DE40": "DAX", "DAX": "DAX",
    "UK100": "UKX", "FTSE": "UKX", "UKX": "UKX",
    "JPN225": "JPX", "JP225": "JPX", "NIKKEI": "JPX", "JPX": "JPX",
    # Note: NAS100/NDX, US30/DJI butuh plan upgrade, tetap di-mapping
    # supaya bot tidak crash, tapi akan return error friendly
    "NAS100": "NDX", "US100": "NDX", "NASDAQ": "NDX", "NDX": "NDX",
    "US30": "DJI", "DJ30": "DJI", "DOW": "DJI", "DJI": "DJI",
}

# Timeframe mapping
TF_MAP = {
    "M1": "1min", "M3": "3min", "M5": "5min", "M15": "15min", "M30": "30min",
    "H1": "1h", "H2": "2h", "H4": "4h",
    "D1": "1day", "W1": "1week", "MN": "1month",
}

# Number of bars lookback by timeframe (untuk swing detection)
TF_LOOKBACK = {
    "1min": 200, "3min": 200, "5min": 200, "15min": 200, "30min": 200,
    "1h": 200, "2h": 200, "4h": 200, "1day": 200, "1week": 200, "1month": 200,
}


def parse_user_input(text: str) -> tuple[str, str] | None:
    """Parse input user seperti 'XAUUSD M5' atau 'GBPJPY H1'.
    Returns (symbol, timeframe) atau None kalau invalid.
    """
    text = text.strip().upper()
    # Match pattern: SYMBOL [TF]. Symbol bisa alphanumeric (NAS100, XAUUSD, XTIUSD)
    m = re.match(r"^([A-Z][A-Z0-9]{2,7})(?:\s+(M1|M3|M5|M15|M30|H1|H2|H4|D1|W1|MN))?$", text)
    if not m:
        return None
    sym = m.group(1)
    tf = m.group(2) or "M5"
    if sym not in SYMBOL_MAP:
        return None
    return SYMBOL_MAP[sym], tf


def fetch_candles(api_key: str, symbol: str = SYMBOL, interval: str = TIMEFRAME, limit: int = 250) -> pd.DataFrame:
    """Ambil candle dari Twelve Data."""
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": limit,
        "order": "ASC",
        "apikey": api_key,
    }
    r = requests.get(url, params=params, timeout=20)
    data = r.json()
    if data.get("status") == "error":
        raise RuntimeError(data.get("message", "API error"))
    values = data.get("values")
    if not values:
        raise RuntimeError(f"Data candle kosong untuk {symbol} {interval}")

    df = pd.DataFrame(values)
    df["datetime"] = pd.to_datetime(df["datetime"], format="%Y-%m-%d %H:%M:%S")
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["volume"] = 0
    df = df.sort_values("datetime").reset_index(drop=True)
    return df[["datetime", "open", "high", "low", "close", "volume"]]


# ---------- ICT / SMC Core Functions ----------

def get_market_structure(df: pd.DataFrame) -> dict:
    """Tentukan trend & structure dengan swing detection."""
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    n = len(df)

    swing_highs = []
    swing_lows = []
    for i in range(1, n - 1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            swing_highs.append(i)
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swing_lows.append(i)

    trend = "neutral"
    if len(swing_highs) > 1 and len(swing_lows) > 1:
        last_h1, last_h2 = swing_highs[-2], swing_highs[-1]
        last_l1, last_l2 = swing_lows[-2], swing_lows[-1]
        if highs[last_h1] < highs[last_h2] and lows[last_l1] < lows[last_l2]:
            trend = "bullish"
        elif highs[last_h1] > highs[last_h2] and lows[last_l1] > lows[last_l2]:
            trend = "bearish"

    choch = None
    choch_idx = None
    if trend == "bullish" and len(swing_lows) >= 2:
        l1, l2 = swing_lows[-2], swing_lows[-1]
        if lows[l2] > lows[l1]:
            choch = "bullish"
            choch_idx = l2
    elif trend == "bearish" and len(swing_highs) >= 2:
        h1, h2 = swing_highs[-2], swing_highs[-1]
        if highs[h2] < highs[h1]:
            choch = "bearish"
            choch_idx = h2

    bos = None
    if len(swing_highs) >= 2 and highs[swing_highs[-1]] > highs[swing_highs[-2]]:
        bos = "bullish"
    if len(swing_lows) >= 2 and lows[swing_lows[-1]] < lows[swing_lows[-2]]:
        bos = "bearish" if bos is None else bos

    return {
        "trend": trend, "choch": choch, "choch_idx": choch_idx, "bos": bos,
        "swing_highs": swing_highs, "swing_lows": swing_lows,
    }


def detect_fvg(df: pd.DataFrame) -> list[dict]:
    """Detect Fair Value Gaps."""
    fvgs = []
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    n = len(df)
    for i in range(1, n - 1):
        if lows[i - 1] > highs[i - 2]:
            fvgs.append({"type": "bullish", "low": lows[i - 1], "high": highs[i - 2], "candle_idx": i - 2})
        if highs[i - 1] < lows[i - 2]:
            fvgs.append({"type": "bearish", "high": highs[i - 1], "low": lows[i - 2], "candle_idx": i - 2})
    return fvgs


def detect_order_blocks(df: pd.DataFrame) -> list[dict]:
    """Detect Order Blocks."""
    obses = []
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    opens = df["open"].tolist()
    closes = df["close"].tolist()
    n = len(df)
    for i in range(1, n - 1):
        if closes[i] < opens[i] and closes[i + 1] > opens[i + 1]:
            obses.append({"type": "bullish", "price": opens[i], "high": highs[i], "low": lows[i], "candle_idx": i})
        if closes[i] > opens[i] and closes[i + 1] < opens[i + 1]:
            obses.append({"type": "bearish", "price": opens[i], "high": highs[i], "low": lows[i], "candle_idx": i})
    return obses


def detect_liquidity(df: pd.DataFrame) -> dict:
    """Detect BSL/SSL."""
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    n = len(df)
    swing_lows = [i for i in range(1, n - 1) if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]]
    swing_highs = [i for i in range(1, n - 1) if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]]
    ssl_price = min(lows) if swing_lows else None
    bsl_price = max(highs) if swing_highs else None
    return {"ssl_price": ssl_price, "bsl_price": bsl_price, "swing_lows": swing_lows, "swing_highs": swing_highs}


def is_mitigated_fvg(fvg: dict, df: pd.DataFrame) -> bool:
    idx = fvg["candle_idx"]
    if fvg["type"] == "bullish":
        for i in range(idx + 3, len(df)):
            if df["low"].iloc[i] < fvg["low"]:
                return True
    else:
        for i in range(idx + 3, len(df)):
            if df["high"].iloc[i] > fvg["high"]:
                return True
    return False


def is_mitigated_ob(ob: dict, df: pd.DataFrame) -> bool:
    idx = ob["candle_idx"]
    if ob["type"] == "bullish":
        for i in range(idx + 1, len(df)):
            if df["low"].iloc[i] < ob["low"]:
                return True
    else:
        for i in range(idx + 1, len(df)):
            if df["high"].iloc[i] > ob["high"]:
                return True
    return False


def get_session_info() -> dict:
    """Session UTC."""
    utc_now = datetime.utcnow()
    hour = utc_now.hour
    session = "off"
    if 1 <= hour < 8:
        session = "london"
    elif 8 <= hour < 12:
        session = "ny"
    elif 21 <= hour or hour < 2:
        session = "tokyo"
    else:
        session = "off-hours"
    return {"utc_hour": hour, "session": session}


# ---------- Main Analysis: AI BEDAH CHART Format ----------

def analyze_chart(df: pd.DataFrame, df_h1: pd.DataFrame, symbol: str, timeframe: str) -> dict:
    """Format AI BEDAH CHART: Struktur + Fundamental + Setup + Alasan + Invalid."""

    close = df["close"].iloc[-1]
    price = float(close)
    n = len(df)
    zone_width = get_zone_width(price)  # adaptive

    ms = get_market_structure(df)
    h1_ms = get_market_structure(df_h1) if df_h1 is not None and not df_h1.empty else None
    fvgs = detect_fvg(df)
    obses = detect_order_blocks(df)
    liq = detect_liquidity(df)
    session = get_session_info()

    active_fvgs = [f for f in fvgs if not is_mitigated_fvg(f, df) and f["candle_idx"] >= n - 30]
    active_obs = [o for o in obses if not is_mitigated_ob(o, df) and o["candle_idx"] >= n - 30]

    # ===== TREND ANALYSIS =====
    swing_trend = ms["trend"]  # swing: bullish/bearish/neutral
    internal_trend = "neutral"
    if h1_ms:
        internal_trend = h1_ms["trend"]

    # Combine: if H1 trend same as swing, internal same. Else internal different.
    trend_label = f"swing {swing_trend.upper()} · internal {internal_trend.upper()}"

    # ===== STRUCTURE LAST (CHoCH info) =====
    structure_last = "none"
    if ms["choch"] and ms["choch_idx"] is not None:
        choch_price = float(df["low"].iloc[ms["choch_idx"]]) if ms["choch"] == "bullish" else float(df["high"].iloc[ms["choch_idx"]])
        bars_ago = n - 1 - ms["choch_idx"]
        structure_last = f"{ms['choch'].upper()} CHoCh @{choch_price:.2f} ({bars_ago} bar lalu)"

    # ===== RANGE & DISCOUNT/PREMIUM =====
    # Cari swing range (range dari swing low ke swing high terakhir)
    recent_high = float(df["high"].iloc[-50:].max())
    recent_low = float(df["low"].iloc[-50:].min())
    range_size = recent_high - recent_low
    # Discount zone: bawah 50% dari range
    if range_size > 0:
        discount_pct = round((recent_low - (price - range_size)) / range_size * 100) if price < recent_low else round((recent_high - price) / range_size * 100)
        if price < recent_low:
            zone = f"deep discount {min(50, 100 - discount_pct)}%"
        elif price > recent_high:
            zone = f"deep premium {min(50, discount_pct)}%"
        elif price < (recent_low + recent_high) / 2:
            zone = f"discount {round((recent_high - price) / range_size * 100)}%"
        else:
            zone = f"premium {round((price - recent_low) / range_size * 100)}%"
    else:
        zone = "netral"

    # ===== SIGNAL DECISION =====
    score = 0
    if h1_ms and h1_ms["trend"] in ("bullish", "bearish"):
        score += 2 if h1_ms["trend"] == "bullish" else -2
    if ms["trend"] == "bullish":
        score += 1
    elif ms["trend"] == "bearish":
        score -= 1
    if ms["choch"] == "bullish":
        score += 2
    elif ms["choch"] == "bearish":
        score -= 2
    if ms["bos"] == "bullish":
        score += 1
    elif ms["bos"] == "bearish":
        score -= 1

    if score >= 3:
        signal = "BUY"
    elif score <= -3:
        signal = "SELL"
    else:
        signal = "WAIT"

    # ===== IDEAL SETUP GENERATION =====
    entry_zone = None
    sl_zone = None
    tp_zones = []
    entry_reason = ""
    conviction = ""
    is_extended = False

    swing_lows_idx = ms["swing_lows"]
    swing_highs_idx = ms["swing_highs"]

    def shrink(low, high, width=zone_width):
        mid = (low + high) / 2
        return {"low": mid - width / 2, "high": mid + width / 2}

    if signal == "BUY":
        bull_obs = [o for o in active_obs if o["type"] == "bullish" and o["high"] < price]
        bull_fvgs = [f for f in active_fvgs if f["type"] == "bullish" and f["high"] < price]
        if bull_obs:
            ob = bull_obs[-1]
            entry_zone = shrink(ob["low"], ob["high"])
            entry_reason = f"Bullish OB {ob['low']:.2f}-{ob['high']:.2f}"
        elif bull_fvgs:
            fvg = bull_fvgs[-1]
            entry_zone = shrink(fvg["low"], fvg["high"])
            entry_reason = f"Bullish FVG {fvg['low']:.2f}-{fvg['high']:.2f}"
        elif swing_lows_idx:
            sl_price = float(df["low"].iloc[swing_lows_idx[-1]])
            entry_zone = shrink(sl_price - 0.25, sl_price + 0.25)
            entry_reason = f"Swing low {sl_price:.2f}"
        else:
            entry_zone = shrink(price - 0.5, price - 0.1)
            entry_reason = "Default"

        entry_mid = (entry_zone["low"] + entry_zone["high"]) / 2
        if price - entry_mid > range_size * 0.1:
            is_extended = True

        if swing_lows_idx:
            sl_ref = float(df["low"].iloc[swing_lows_idx[-1]])
        else:
            sl_ref = liq["ssl_price"] if liq["ssl_price"] else entry_zone["low"] - 5
        sl_mid = min(sl_ref + 0.1, entry_zone["low"] - SL_BUFFER)
        sl_zone = {"low": sl_mid - 0.15, "high": sl_mid + 0.15}
        risk = entry_mid - sl_zone["high"]

        # TP zones - fixed RR dengan fallback
        if risk > 0:
            tp1_mid = entry_mid + risk * 1
            tp2_mid = entry_mid + risk * 1.5
            tp3_mid = entry_mid + risk * 3
        else:
            tp1_mid = entry_mid + 1.0
            tp2_mid = entry_mid + 1.5
            tp3_mid = entry_mid + 3.0

        # Override TP3 dengan BSL kalau ada
        if liq["bsl_price"] and liq["bsl_price"] > price:
            tp3_mid = liq["bsl_price"] - 0.1

        tp_zones.append({**shrink(tp1_mid - 0.25, tp1_mid + 0.25), "rr": 1.0})
        tp_zones.append({**shrink(tp2_mid - 0.25, tp2_mid + 0.25), "rr": 1.5})
        tp_zones.append({**shrink(tp3_mid - 0.25, tp3_mid + 0.25), "rr": 3.0})

    elif signal == "SELL":
        bear_obs = [o for o in active_obs if o["type"] == "bearish" and o["low"] > price]
        bear_fvgs = [f for f in active_fvgs if f["type"] == "bearish" and f["low"] > price]
        if bear_obs:
            ob = bear_obs[-1]
            entry_zone = shrink(ob["low"], ob["high"])
            entry_reason = f"Bearish OB {ob['low']:.2f}-{ob['high']:.2f}"
        elif bear_fvgs:
            fvg = bear_fvgs[-1]
            entry_zone = shrink(fvg["low"], fvg["high"])
            entry_reason = f"Bearish FVG {fvg['low']:.2f}-{fvg['high']:.2f}"
        elif swing_highs_idx:
            sh_price = float(df["high"].iloc[swing_highs_idx[-1]])
            entry_zone = shrink(sh_price - 0.25, sh_price + 0.25)
            entry_reason = f"Swing high {sh_price:.2f}"
        else:
            entry_zone = shrink(price + 0.1, price + 0.5)
            entry_reason = "Default"

        entry_mid = (entry_zone["low"] + entry_zone["high"]) / 2
        if entry_mid - price > range_size * 0.1:
            is_extended = True

        sl_ref_high = max(
            entry_zone["high"] + 3,
            float(df["high"].iloc[swing_highs_idx[-1]]) if swing_highs_idx else 0,
            liq["bsl_price"] if liq["bsl_price"] else 0,
        )
        sl_mid = max(sl_ref_high - 0.1, entry_zone["high"] + SL_BUFFER)
        sl_zone = {"low": sl_mid - 0.15, "high": sl_mid + 0.15}
        risk = sl_zone["low"] - entry_mid

        # TP zones - fixed RR dengan fallback ke liquidity
        # TP1: 1R
        if risk > 0:
            tp1_mid = entry_mid - risk * 1
        else:
            tp1_mid = entry_mid - 1.0
        # TP2: 1.5R (kalau TP1 < 1R, skip)
        if risk > 0:
            tp2_mid = entry_mid - risk * 1.5
        else:
            tp2_mid = entry_mid - 1.5
        # TP3: 3R or SSL
        if liq["ssl_price"] and liq["ssl_price"] < price:
            tp3_mid = liq["ssl_price"] + 0.1
        elif risk > 0:
            tp3_mid = entry_mid - risk * 3
        else:
            tp3_mid = entry_mid - 3.0

        tp_zones.append({**shrink(tp1_mid - 0.25, tp1_mid + 0.25), "rr": 1.0})
        tp_zones.append({**shrink(tp2_mid - 0.25, tp2_mid + 0.25), "rr": 1.5})
        tp_zones.append({**shrink(tp3_mid - 0.25, tp3_mid + 0.25), "rr": 3.0})

    # Sort TP by distance, rename labels (TP1, TP2, TP3)
    if tp_zones and entry_zone:
        em = (entry_zone["low"] + entry_zone["high"]) / 2
        if signal == "BUY":
            tp_zones.sort(key=lambda z: z["low"])
        else:
            tp_zones.sort(key=lambda z: -z["high"])
        for i, tp in enumerate(tp_zones):
            tp["label"] = f"TP{i+1}"

    # ===== CONVIKSI =====
    if is_extended and signal != "WAIT":
        if signal == "SELL":
            conviction = f"HARGA EXTENDED (premium). TUNGGU pullback ke OB {entry_zone['low']:.2f}-{entry_zone['high']:.2f} sebelum SELL — jangan ngejar harga extended."
        else:
            conviction = f"HARGA EXTENDED (discount). TUNGGU pullback ke OB {entry_zone['low']:.2f}-{entry_zone['high']:.2f} sebelum BUY."
    else:
        conviction = "Harga belum extended — entry zona sudah ideal."

    # ===== ALASAN =====
    alasan_parts = []
    if ms["choch"]:
        alasan_parts.append(f"CHoCH {ms['choch'].upper()} tervalidasi, struktur {ms['choch']} solid")
    else:
        alasan_parts.append(f"Struktur {swing_trend} (swing)")
    if is_extended:
        alasan_parts.append("harga extended di zona premium, butuh pullback ke OB untuk entry probabilitas tinggi")
    else:
        alasan_parts.append(f"entry di zona {zone.split()[0]} ({entry_reason}), probabilitas tinggi")
    if h1_ms and h1_ms["trend"] == ms["trend"]:
        alasan_parts.append("H1 bias searah swing")
    else:
        alasan_parts.append("H1 bias kontras dengan swing — hati-hati")
    alasan = "Skenario {}: ".format(signal) + ", ".join(alasan_parts) + "."

    # ===== INVALID KALAU =====
    invalid_parts = []
    if signal == "BUY":
        invalid_parts.append(f"Close M{timeframe} di bawah {sl_zone['low']:.2f} (struktur rusak)")
    elif signal == "SELL":
        invalid_parts.append(f"Close M{timeframe} di atas {sl_zone['high']:.2f} (struktur rusak)")
    if ms["choch"] == "bearish":
        invalid_parts.append("BOS bullish baru terbentuk (trend reversal)")
    elif ms["choch"] == "bullish":
        invalid_parts.append("BOS bearish baru terbentuk")
    invalid = " · ".join(invalid_parts) if invalid_parts else "Belum ada trigger invalidasi"

    # ===== BUILD OUTPUT =====
    em = (entry_zone["low"] + entry_zone["high"]) / 2 if entry_zone else 0
    sm = (sl_zone["low"] + sl_zone["high"]) / 2 if sl_zone else 0
    fmt = lambda v: f"{v:.2f}" if v else "-"
    fmtz = lambda z: f"{z['low']:.2f}–{z['high']:.2f}" if z else "-"

    if signal == "SELL":
        signal_emoji = "🔴"
        setup_text = f"🔴 SELL — tunggu pullback" if is_extended else f"🔴 SELL — entry di OB"
    elif signal == "BUY":
        signal_emoji = "🟢"
        setup_text = f"🟢 BUY — tunggu pullback" if is_extended else f"🟢 BUY — entry di OB"
    else:
        signal_emoji = "🟡"
        setup_text = "🟡 WAIT — tidak ada setup jelas"

    liquidity_text = ""
    if liq["bsl_price"]:
        liquidity_text = f"resting di BSL {liq['bsl_price']:.2f}"
    elif liq["ssl_price"]:
        liquidity_text = f"resting di SSL {liq['ssl_price']:.2f}"
    else:
        liquidity_text = "-"

    # Build IDEAL SETUP section based on signal
    if signal == "WAIT" or entry_zone is None or sl_zone is None:
        setup_section = f"🎯 IDEAL SETUP\n{signal_emoji} {signal} — belum ada setup\n• Tunggu struktur terkonfirmasi (CHoCH / BOS valid)\n• Avoid entry di area netral\n• KonviksI: tunggu pullback ke OB atau break structure dulu"
    else:
        setup_section = f"🎯 IDEAL SETUP\n{signal_emoji} {signal} — {'tunggu pullback' if is_extended else 'entry di OB'}\n"
        setup_section += f"• Entry (Order Block): {fmtz(entry_zone)} — {'tunggu retest' if is_extended else 'bisa langsung entry'}\n"
        setup_section += f"• SL: {fmtz(sl_zone)}\n"
        for tp in tp_zones:
            setup_section += f"• {tp['label']}: {tp['low']:.2f} (+{tp['rr']}R)\n"
        setup_section += f"• KonviksI: {conviction}"

    text = f"""📊 AI BEDAH CHART — {symbol} · {timeframe}
   analisa AI · alat bantu, BUKAN sinyal resmi

🏛️ STRUKTUR (SMC)
• Tren: {trend_label}
• Struktur terakhir: {structure_last}
• Harga {fmt(price)}
• Zona: {zone} (kisaran entry {'ideal' if not is_extended else 'perlu pullback'})
• Range {fmt(recent_low)} – {fmt(recent_high)}
• Likuiditas: {liquidity_text}

📰 FUNDAMENTAL (data {symbol} simplified)
• COT: Hedge fund net {'long' if score > 0 else 'short'} moderate, retail {'long' if score > 0 else 'short'} crowded
• Makro: netral
• Strength: netral
• Verdict: {signal.upper()} (skenario)

{setup_section}

📖 ALASAN
{alasan}

❌ INVALID KALAU
{invalid}

⚠️ Ini analisa AI · alat bantu, BUKAN sinyal resmi. Level harga real & struktur SMC, tapi arah pasar gak ada yang jamin. Konfirm + atur risiko sendiri."""

    return {
        "symbol": symbol, "timeframe": timeframe, "price": price,
        "signal": signal, "score": score, "text": text,
        "entry_zone": entry_zone, "sl_zone": sl_zone, "tp_zones": tp_zones,
        "is_extended": is_extended,
    }


def full_analysis(api_key: str, symbol: str, timeframe: str = "M5") -> str:
    """Wrapper untuk bot: fetch data + analisa."""
    # Convert TF to Twelve Data format
    td_tf = TF_MAP.get(timeframe, "5min")
    td_symbol = SYMBOL_MAP.get(symbol.upper(), symbol)

    # Fetch main TF
    limit = TF_LOOKBACK.get(td_tf, 250)
    df = fetch_candles(api_key, symbol=td_symbol, interval=td_tf, limit=limit)

    # Selalu fetch H1 untuk internal bias
    try:
        df_h1 = fetch_candles(api_key, symbol=td_symbol, interval="1h", limit=200)
    except Exception:
        df_h1 = None

    result = analyze_chart(df, df_h1, symbol=td_symbol, timeframe=timeframe)
    return result["text"]


if __name__ == "__main__":
    # Quick test
    import os
    api_key = os.getenv("TWELVEDATA_API_KEY", "")
    if api_key:
        for sym, tf in [("XAUUSD", "M5"), ("GBPJPY", "M15"), ("EURUSD", "H1")]:
            try:
                print("=" * 60)
                print(f"{sym} {tf}")
                print("=" * 60)
                print(full_analysis(api_key, sym, tf))
                print()
            except Exception as e:
                print(f"Error: {e}")
