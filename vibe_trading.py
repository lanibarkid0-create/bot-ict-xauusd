"""Vibe-Trading adapter (terinspirasi HKUDS/Vibe-Trading).
Personal Trading Agent versi ringan: indikator teknikal + backtest + narasi.
Tanpa dependensi baru (pure python). Sejarah format: list[dict] open/high/low/close.
"""
from __future__ import annotations


def _ema(vals: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    out: list[float] = []
    e = vals[0] if vals else 0.0
    for i, v in enumerate(vals):
        e = v if i == 0 else v * k + e * (1 - k)
        out.append(e)
    return out


def _rsi(closes: list[float], n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    g = l = 0.0
    for i in range(len(closes) - n, len(closes)):
        d = closes[i] - closes[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    if l == 0:
        return 100.0 if g else 50.0
    rs = (g / n) / (l / n)
    return round(100 - 100 / (1 + rs), 2)


def _atr(h: list[float], l: list[float], c: list[float], n: int = 14) -> float:
    if len(c) < 2:
        return 0.0
    trs = []
    for i in range(1, len(c)):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    trs = trs[-n:]
    return round(sum(trs) / len(trs), 2) if trs else 0.0


def _macd(closes: list[float]) -> dict:
    e12, e26 = _ema(closes, 12), _ema(closes, 26)
    line = e12[-1] - e26[-1]
    sig_series = _ema([a - b for a, b in zip(e12, e26)], 9)
    sig = sig_series[-1]
    return {"line": round(line, 2), "signal": round(sig, 2), "hist": round(line - sig, 2)}


def _bb(closes: list[float], n: int = 20) -> dict:
    w = closes[-n:] if len(closes) >= n else closes
    ma = sum(w) / len(w)
    var = sum((x - ma) ** 2 for x in w) / len(w)
    sd = var ** 0.5
    return {"mid": round(ma, 2), "upper": round(ma + 2 * sd, 2), "lower": round(ma - 2 * sd, 2)}


def backtest_ema_cross(closes: list[float], risk_pts: float = 20.0, reward_pts: float = 40.0) -> dict:
    """Backtest sederhana ala Vibe-Trading: cross EMA9/21, TP/SL tetap."""
    if len(closes) < 30:
        return {"trades": 0, "winrate_pct": 0.0, "expectancy_pts": 0.0, "note": "data kurang"}
    e9, e21 = _ema(closes, 9), _ema(closes, 21)
    wins = losses = 0
    for i in range(21, len(closes) - 1):
        prev = e9[i - 1] - e21[i - 1]
        now = e9[i] - e21[i]
        if prev <= 0 < now:  # golden cross -> long
            wins += 1 if closes[i + 1] - closes[i] > (reward_pts - risk_pts) / 4 else 0
            losses += 0 if closes[i + 1] - closes[i] > (reward_pts - risk_pts) / 4 else 1
        elif prev >= 0 > now:  # death cross -> short
            wins += 1 if closes[i] - closes[i + 1] > (reward_pts - risk_pts) / 4 else 0
            losses += 0 if closes[i] - closes[i + 1] > (reward_pts - risk_pts) / 4 else 1
    tot = wins + losses
    wr = round(100 * wins / tot, 1) if tot else 0.0
    exp = round(wr / 100 * reward_pts - (1 - wr / 100) * risk_pts, 2) if tot else 0.0
    return {"trades": tot, "winrate_pct": wr, "expectancy_pts": exp,
            "note": f"{tot} sinyal EMA9/21, WR {wr}% (SL {risk_pts} / TP {reward_pts} pts)"}


def vibe_analyze(history: list[dict], spot: float) -> dict:
    """Analisa gaya Vibe-Trading agent: indikator + backtest + skor 0-100."""
    closes = [float(r["close"]) for r in history]
    highs = [float(r["high"]) for r in history]
    lows = [float(r["low"]) for r in history]
    e9 = _ema(closes, 9)[-1]
    e21 = _ema(closes, 21)[-1]
    rsi = _rsi(closes)
    macd = _macd(closes)
    atr = _atr(highs, lows, closes)
    bb = _bb(closes)
    bt = backtest_ema_cross(closes)
    score = 50.0
    reasons = []
    if e9 > e21:
        score += 12
        reasons.append(f"EMA9 {e9:,.2f} > EMA21 {e21:,.2f} (momentum naik)")
    else:
        score -= 12
        reasons.append(f"EMA9 {e9:,.2f} < EMA21 {e21:,.2f} (momentum turun)")
    if rsi < 30:
        score += 8
        reasons.append(f"RSI {rsi} oversold → pantulan naik")
    elif rsi > 70:
        score -= 8
        reasons.append(f"RSI {rsi} overbought → rawan koreksi")
    else:
        reasons.append(f"RSI {rsi} netral")
    if macd["hist"] > 0:
        score += 7
        reasons.append(f"MACD hist {macd['hist']:+} bullish")
    else:
        score -= 7
        reasons.append(f"MACD hist {macd['hist']:+} bearish")
    if spot > bb["mid"]:
        score += 4
        reasons.append("Harga di atas BB-mid (bias beli)")
    else:
        score -= 4
        reasons.append("Harga di bawah BB-mid (bias jual)")
    score = max(0, min(100, round(score, 1)))
    bias = "BUY" if score >= 55 else ("SELL" if score <= 45 else "NETRAL")
    return {"engine": "vibe-trading", "bias": bias, "score_0_100": score,
            "ema9": round(e9, 2), "ema21": round(e21, 2), "rsi14": rsi,
            "macd": macd, "atr14": atr, "bollinger": bb, "backtest": bt,
            "reasons": reasons}
