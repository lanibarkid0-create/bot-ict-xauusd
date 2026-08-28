"""ICT & SMC Professional Analysis for XAUUSD with H1 bias + Entry/SL/TP zones."""

import pandas as pd
import requests
from datetime import datetime

TIMEFRAME = "5min"
SYMBOL = "XAU/USD"

# Pips definition: untuk XAUUSD 1 pip = 0.1, jadi 5 pips = 0.5
PIPS = 0.1
ZONE_WIDTH = 0.5  # 5 pips zone width for entry/TP
SL_BUFFER = 0.2  # 2 pips buffer from entry to SL edge

# Map interval text to Twelve Data interval string
TF_MAP = {
    "M1": "1min", "M3": "3min", "M5": "5min", "M15": "15min", "M30": "30min",
    "H1": "1h", "H2": "2h", "H4": "4h", "D1": "1day", "W1": "1week",
}


def fetch_candles(api_key: str, symbol: str = SYMBOL, interval: str = TIMEFRAME, limit: int = 200) -> pd.DataFrame:
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
    """Tentukan trend & structure (BOS, CHoCH)."""
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
        if highs[last_h1] < highs[last_h2]:
            trend = "bullish"
        elif highs[last_h1] > highs[last_h2]:
            trend = "bearish"

    choch = None
    if trend == "bullish" and len(swing_lows) >= 2:
        l1, l2 = swing_lows[-2], swing_lows[-1]
        if lows[l2] > lows[l1]:
            choch = "bullish"
    elif trend == "bearish" and len(swing_highs) >= 2:
        h1, h2 = swing_highs[-2], swing_highs[-1]
        if highs[h2] < highs[h1]:
            choch = "bearish"

    bos = None
    if len(swing_highs) >= 2 and highs[swing_highs[-1]] > highs[swing_highs[-2]]:
        bos = "bullish"
    if len(swing_lows) >= 2 and lows[swing_lows[-1]] < lows[swing_lows[-2]]:
        bos = "bearish" if bos is None else bos

    return {
        "trend": trend, "choch": choch, "bos": bos,
        "swing_highs": swing_highs, "swing_lows": swing_lows,
    }


def detect_fvg(df: pd.DataFrame) -> list[dict]:
    """Detect Fair Value Gaps (inefficiencies)."""
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
    """Detect Order Blocks (full candle info)."""
    obses = []
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    opens = df["open"].tolist()
    closes = df["close"].tolist()
    n = len(df)

    for i in range(1, n - 1):
        if closes[i] < opens[i] and closes[i + 1] > opens[i + 1]:
            obses.append({
                "type": "bullish", "price": opens[i], "high": highs[i], "low": lows[i],
                "candle_idx": i,
            })
        if closes[i] > opens[i] and closes[i + 1] < opens[i + 1]:
            obses.append({
                "type": "bearish", "price": opens[i], "high": highs[i], "low": lows[i],
                "candle_idx": i,
            })
    return obses


def detect_liquidity(df: pd.DataFrame) -> dict:
    """Detect Buy-Side Liquidity (BSL) & Sell-Side Liquidity (SSL)."""
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    n = len(df)

    swing_lows = [i for i in range(1, n - 1) if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]]
    swing_highs = [i for i in range(1, n - 1) if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]]

    ssl_price = min(lows) if swing_lows else None
    bsl_price = max(highs) if swing_highs else None

    return {"ssl_price": ssl_price, "bsl_price": bsl_price, "swing_lows": swing_lows, "swing_highs": swing_highs}


def get_session_info() -> dict:
    """ICT session timing (UTC)."""
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


def is_mitigated_fvg(fvg: dict, df: pd.DataFrame) -> bool:
    """Cek apakah FVG sudah dimitigasi (harga sudah tembus)."""
    idx = fvg["candle_idx"]
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    if fvg["type"] == "bullish":
        # Bullish FVG dianggap dimitigasi jika harga close di bawah low FVG setelahnya
        for i in range(idx + 3, len(df)):
            if lows[i] < fvg["low"]:
                return True
    else:
        for i in range(idx + 3, len(df)):
            if highs[i] > fvg["high"]:
                return True
    return False


def is_mitigated_ob(ob: dict, df: pd.DataFrame) -> bool:
    """Cek apakah OB sudah dimitigasi."""
    idx = ob["candle_idx"]
    if ob["type"] == "bullish":
        # Bullish OB dimitigasi jika candle close di bawah OB high (jika OB di bawah harga)
        for i in range(idx + 1, len(df)):
            if df["low"].iloc[i] < ob["low"]:
                return True
    else:
        for i in range(idx + 1, len(df)):
            if df["high"].iloc[i] > ob["high"]:
                return True
    return False


# ---------- Main Analysis ----------

def analyze_ict(df: pd.DataFrame, df_h1: pd.DataFrame = None, interval: str = "5min") -> dict:
    """Main ICT/SMC analysis dengan H1 bias & zona entry/SL/TP.
    
    Args:
        df: Candle data timeframe yang sedang dianalisa
        df_h1: Candle data H1 untuk bias utama (optional, akan difetch jika None)
        interval: Label timeframe
    """
    close = df["close"].iloc[-1]
    price = float(close)
    n = len(df)

    ms = get_market_structure(df)
    fvgs = detect_fvg(df)
    obses = detect_order_blocks(df)
    liq = detect_liquidity(df)
    session = get_session_info()

    # --- H1 BIAS (jika ada) ---
    h1_bias_info = None
    if df_h1 is not None and not df_h1.empty:
        h1_ms = get_market_structure(df_h1)
        h1_bias_info = {
            "trend": h1_ms["trend"],
            "choch": h1_ms["choch"],
            "bos": h1_ms["bos"],
        }

    # --- Filter unmitigated FVGs & OBs ---
    active_fvgs = [f for f in fvgs if not is_mitigated_fvg(f, df) and f["candle_idx"] >= n - 30]
    active_obs = [o for o in obses if not is_mitigated_ob(o, df) and o["candle_idx"] >= n - 30]

    # --- Scoring & Reasoning ---
    score = 0
    reasons_bull = []
    reasons_bear = []

    # 1. H1 Bias (priority)
    if h1_bias_info:
        if h1_bias_info["trend"] == "bullish":
            score += 2
            reasons_bull.append("H1 Bias: BULLISH")
        elif h1_bias_info["trend"] == "bearish":
            score -= 2
            reasons_bear.append("H1 Bias: BEARISH")
        if h1_bias_info["choch"] == "bullish":
            score += 2
            reasons_bull.append("H1 CHoCH: Bullish")
        elif h1_bias_info["choch"] == "bearish":
            score -= 2
            reasons_bear.append("H1 CHoCH: Bearish")

    # 2. Lower TF Trend
    if ms["trend"] == "bullish":
        score += 1
        reasons_bull.append(f"{interval} Trend: Bullish")
    elif ms["trend"] == "bearish":
        score -= 1
        reasons_bear.append(f"{interval} Trend: Bearish")

    # 3. CHoCH
    if ms["choch"] == "bullish":
        score += 2
        reasons_bull.append("CHoCH: Bullish")
    elif ms["choch"] == "bearish":
        score -= 2
        reasons_bear.append("CHoCH: Bearish")

    # 4. BOS
    if ms["bos"] == "bullish":
        score += 1
        reasons_bull.append("BOS: Bullish")
    elif ms["bos"] == "bearish":
        score -= 1
        reasons_bear.append("BOS: Bearish")

    # 5. Session filter
    if session["session"] in ("london", "ny"):
        score += 1
        reasons_bull.append(f"Session: {session['session'].upper()} active")
    elif session["session"] == "off-hours":
        score -= 1
        reasons_bear.append("Session: Off-hours")

    # --- Determine signal ---
    if score >= 3:
        signal, emoji = "BUY", "🟢"
    elif score <= -3:
        signal, emoji = "SELL", "🔴"
    else:
        signal, emoji = "WAIT / NEUTRAL", "🟡"

    # --- Generate Entry/SL/TP Zones ---
    entry_zone = None
    sl_zone = None
    tp_zones = []
    entry_confidence = "Low"
    entry_reason = ""

    swing_lows_idx = ms["swing_lows"]
    swing_highs_idx = ms["swing_highs"]

    if signal == "BUY":
        # Entry Zone: bullish OB atau FVG di bawah harga, max 0.5 (5 pips) wide
        bull_obs = [o for o in active_obs if o["type"] == "bullish" and o["high"] < price]
        bull_fvgs = [f for f in active_fvgs if f["type"] == "bullish" and f["high"] < price]

        # Helper: shrink zone to max 0.5 wide
        def shrink_to_pips(low, high, width=ZONE_WIDTH):
            mid = (low + high) / 2
            return {"low": mid - width / 2, "high": mid + width / 2}

        if bull_obs:
            ob = bull_obs[-1]
            entry_zone = shrink_to_pips(ob["low"], ob["high"])
            entry_confidence = "High"
            entry_reason = f"Bullish Order Block @ {ob['low']:.2f} - {ob['high']:.2f}"
        elif bull_fvgs:
            fvg = bull_fvgs[-1]
            entry_zone = shrink_to_pips(fvg["low"], fvg["high"])
            entry_confidence = "Medium"
            entry_reason = f"Bullish FVG @ {fvg['low']:.2f} - {fvg['high']:.2f}"
        elif swing_lows_idx:
            sl_idx = swing_lows_idx[-1]
            sl_price = float(df["low"].iloc[sl_idx])
            # Zone 5 pips wide centered di swing low
            entry_zone = shrink_to_pips(sl_price - 0.25, sl_price + 0.25)
            entry_confidence = "Low"
            entry_reason = "Recent swing low zone"
        else:
            entry_zone = shrink_to_pips(price - 0.5, price - 0.1)
            entry_reason = "Default zone"

        # SL Zone: tight, di bawah entry zone + liquidity (1-2 pips)
        if swing_lows_idx:
            sl_ref = float(df["low"].iloc[swing_lows_idx[-1]])
        else:
            sl_ref = liq["ssl_price"] if liq["ssl_price"] else entry_zone["low"] - 5
        # SL zone 0.3 wide (3 pips)
        sl_mid = min(sl_ref + 0.1, entry_zone["low"] - SL_BUFFER)
        sl_zone = {"low": sl_mid - 0.15, "high": sl_mid + 0.15}

        # TP Zones: 3 targets, masing-masing 0.5 wide (5 pips)
        # TP1: nearest resistance (swing high atau FVG) 5 pips wide
        bear_fvgs = [f for f in active_fvgs if f["type"] == "bearish" and f["low"] > price]
        if bear_fvgs:
            tp1_zone = shrink_to_pips(bear_fvgs[0]["low"], bear_fvgs[0]["high"])
        elif swing_highs_idx:
            sh = float(df["high"].iloc[swing_highs_idx[0]])
            tp1_zone = shrink_to_pips(sh - 0.25, sh + 0.25)
        else:
            tp1_zone = shrink_to_pips(price + 0.5, price + 1.0)
        tp_zones.append({**tp1_zone, "rr": 1.0})

        # TP2: 1:1.5 RR (5 pips wide)
        entry_mid = (entry_zone["low"] + entry_zone["high"]) / 2
        risk = entry_mid - sl_zone["high"]
        if risk > 0:
            tp2_mid = entry_mid + risk * 1.5
        else:
            tp2_mid = entry_mid + 1.0
        tp_zones.append({**shrink_to_pips(tp2_mid - 0.25, tp2_mid + 0.25), "rr": 1.5})

        # TP3: BSL atau 1:3 RR (5 pips wide)
        if liq["bsl_price"] and liq["bsl_price"] > price:
            tp3_mid = liq["bsl_price"] - 0.25
        elif risk > 0:
            tp3_mid = entry_mid + risk * 3
        else:
            tp3_mid = entry_mid + 2.0
        tp_zones.append({**shrink_to_pips(tp3_mid - 0.25, tp3_mid + 0.25), "rr": 3.0})

    elif signal == "SELL":
        # Helper: shrink zone to max 0.5 wide
        def shrink_to_pips(low, high, width=ZONE_WIDTH):
            mid = (low + high) / 2
            return {"low": mid - width / 2, "high": mid + width / 2}

        # Entry Zone: bearish OB atau FVG di atas harga, max 0.5 (5 pips) wide
        bear_obs = [o for o in active_obs if o["type"] == "bearish" and o["low"] > price]
        bear_fvgs = [f for f in active_fvgs if f["type"] == "bearish" and f["low"] > price]

        if bear_obs:
            ob = bear_obs[-1]
            entry_zone = shrink_to_pips(ob["low"], ob["high"])
            entry_confidence = "High"
            entry_reason = f"Bearish Order Block @ {ob['low']:.2f} - {ob['high']:.2f}"
        elif bear_fvgs:
            fvg = bear_fvgs[-1]
            entry_zone = shrink_to_pips(fvg["low"], fvg["high"])
            entry_confidence = "Medium"
            entry_reason = f"Bearish FVG @ {fvg['low']:.2f} - {fvg['high']:.2f}"
        elif swing_highs_idx:
            sh_idx = swing_highs_idx[-1]
            sh_price = float(df["high"].iloc[sh_idx])
            # Zone 5 pips wide centered di swing high
            entry_zone = shrink_to_pips(sh_price - 0.25, sh_price + 0.25)
            entry_confidence = "Low"
            entry_reason = "Recent swing high zone"
        else:
            entry_zone = shrink_to_pips(price + 0.1, price + 0.5)
            entry_reason = "Default zone"

        # SL Zone: tight, di atas entry zone (1-2 pips)
        sl_ref_high = max(
            entry_zone["high"] + 3,
            float(df["high"].iloc[swing_highs_idx[-1]]) if swing_highs_idx else 0,
            liq["bsl_price"] if liq["bsl_price"] else 0,
        )
        # SL zone 0.3 wide (3 pips)
        sl_mid = max(sl_ref_high - 0.1, entry_zone["high"] + SL_BUFFER)
        sl_zone = {"low": sl_mid - 0.15, "high": sl_mid + 0.15}

        # TP Zones: 3 targets, masing-masing 0.5 wide (5 pips)
        # TP1: nearest support (swing low atau FVG) 5 pips wide
        bull_fvgs = [f for f in active_fvgs if f["type"] == "bullish" and f["high"] < price]
        if bull_fvgs:
            tp1_zone = shrink_to_pips(bull_fvgs[0]["low"], bull_fvgs[0]["high"])
        elif swing_lows_idx:
            sl = float(df["low"].iloc[swing_lows_idx[0]])
            tp1_zone = shrink_to_pips(sl - 0.25, sl + 0.25)
        else:
            tp1_zone = shrink_to_pips(price - 1.0, price - 0.5)
        tp_zones.append({**tp1_zone, "rr": 1.0})

        # TP2: 1:1.5 RR (5 pips wide, di bawah entry)
        entry_mid = (entry_zone["low"] + entry_zone["high"]) / 2
        risk = sl_zone["low"] - entry_mid
        if risk > 0:
            tp2_mid = entry_mid - risk * 1.5
        else:
            tp2_mid = entry_mid - 1.0
        tp_zones.append({**shrink_to_pips(tp2_mid - 0.25, tp2_mid + 0.25), "rr": 1.5})

        # TP3: SSL atau 1:3 RR (5 pips wide)
        if liq["ssl_price"] and liq["ssl_price"] < price:
            tp3_mid = liq["ssl_price"] + 0.25
        elif risk > 0:
            tp3_mid = entry_mid - risk * 3
        else:
            tp3_mid = entry_mid - 2.0
        tp_zones.append({**shrink_to_pips(tp3_mid - 0.25, tp3_mid + 0.25), "rr": 3.0})

    # Sort TP zones by distance from entry mid (nearest first) dan rename labels
    if tp_zones and entry_zone:
        entry_mid_all = (entry_zone["low"] + entry_zone["high"]) / 2
        if signal == "BUY":
            tp_zones.sort(key=lambda z: z["low"])  # ascending: lowest first = nearest
        else:
            tp_zones.sort(key=lambda z: -z["high"])  # descending: highest first = nearest
        for i, tp in enumerate(tp_zones, 1):
            tp["label"] = f"TP{i}"

    fmt_zone = lambda z: f"{z['low']:.2f} - {z['high']:.2f}" if z else "-"

    text_lines = [
        f"📊 ICT/SMC ANALISA XAUUSD {interval}",
        f"🕒 {df['datetime'].iloc[-1]:%d %b %Y %H:%M} UTC",
        "",
        f"💰 Harga sekarang: {price:.2f}",
        "",
        "━━━ BIAS H1 (UTAMA) ━━━",
        f"Trend: {h1_bias_info['trend'].upper() if h1_bias_info else '-'}",
        f"CHoCH: {h1_bias_info['choch'] if h1_bias_info and h1_bias_info['choch'] else '-'}",
        f"BOS: {h1_bias_info['bos'] if h1_bias_info and h1_bias_info['bos'] else '-'}",
        "",
        f"━━━ STRUKTUR {interval} ━━━",
        f"Trend: {ms['trend'].upper()}",
        f"CHoCH: {ms['choch'] or '-'}",
        f"BOS: {ms['bos'] or '-'}",
        "",
        "━━━ ZONA ENTRY ━━━",
        f"Type: {entry_reason}",
        f"Confidence: {entry_confidence}",
        f"📍 Entry: {fmt_zone(entry_zone)}",
        "",
        "━━━ STOP LOSS ━━━",
        f"🛡️ SL: {fmt_zone(sl_zone)}",
        "",
        "━━━ TAKE PROFIT ━━━",
    ]
    for tp in tp_zones:
        text_lines.append(f"🎯 {tp['label']} (RR 1:{tp['rr']}): {fmt_zone(tp)}")
    text_lines += [
        "",
        "━━━ LIQUIDITY ━━━",
        f"SSL (Sell-Side): {liq['ssl_price']:.2f}" if liq['ssl_price'] else "SSL: -",
        f"BSL (Buy-Side): {liq['bsl_price']:.2f}" if liq['bsl_price'] else "BSL: -",
        "",
        f"🕐 Session: {session['session'].upper()}",
        "",
        f"🎯 SINYAL: {emoji} {signal} (skor {score:+d})",
        "",
        "Alasan Bullish:" + ("\n• " + "\n• ".join(reasons_bull) if reasons_bull else "\n• -"),
        "Alasan Bearish:" + ("\n\n• " + "\n• ".join(reasons_bear) if reasons_bear else "\n-"),
        "",
        "⚠️ Bukan nasihat keuangan. Gunakan money management.",
    ]

    return {
        "price": price,
        "signal": signal,
        "score": score,
        "h1_bias": h1_bias_info,
        "entry_zone": entry_zone,
        "sl_zone": sl_zone,
        "tp_zones": tp_zones,
        "text": "\n".join(text_lines),
    }


def full_analysis(api_key: str, symbol: str = SYMBOL, interval: str = TIMEFRAME) -> str:
    """Wrapper untuk bot - fetch H1 bias + target TF analysis."""
    df = fetch_candles(api_key, symbol=symbol, interval=interval)
    # Selalu fetch H1 untuk bias
    try:
        df_h1 = fetch_candles(api_key, symbol=symbol, interval="1h")
    except Exception:
        df_h1 = None
    interval_label = interval.upper() if interval.isalpha() else interval
    result = analyze_ict(df, df_h1=df_h1, interval=interval_label)
    return result["text"]
