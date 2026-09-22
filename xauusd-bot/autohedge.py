"""AutoHedge adapter (terinspirasi The-Swarm-Corporation/AutoHedge).
Pipeline multi-agen ringan: Director -> Quant -> Risk -> Execution.
Tanpa dependensi AI/eksternal: semua rule-based dari data OHLC + spot.
"""
from __future__ import annotations

from typing import Any


def director_agent(spot: float, closes: list[float], vibe: dict, fincept: dict) -> dict:
    trend_up = vibe.get("ema9", 0) > vibe.get("ema21", 0)
    thesis = ("Emas dalam fase akumulasi momentum beli" if trend_up
              else "Emas dalam fase distribusi/tekanan jual")
    strat = "trend-follow limit di POI" if vibe.get("bias") in ("BUY", "SELL") else "mean-reversion, tunggu"
    conviction = round(min(95, max(5, vibe.get("score_0_100", 50))), 1)
    return {"thesis": thesis, "strategy": strat, "conviction": conviction,
            "vibe_bias": vibe.get("bias"), "fincept_verdict": fincept.get("dcf", {}).get("verdict")}


def quant_agent(history: list[dict], spot: float) -> dict:
    closes = [float(r["close"]) for r in history]
    highs = [float(r["high"]) for r in history]
    lows = [float(r["low"]) for r in history]
    rets = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1]]
    vol = (sum((x - sum(rets) / len(rets)) ** 2 for x in rets) / len(rets)) ** 0.5 if len(rets) > 2 else 0.0
    rng = (max(highs[-14:]) - min(lows[-14:])) if len(highs) >= 14 else (max(highs) - min(lows) if highs else 0)
    return {"vol_daily": round(vol * 100, 3), "range14": round(rng, 2),
            "trend": "UP" if closes[-1] > closes[-5] else ("DOWN" if closes[-1] < closes[-5] else "FLAT")}


def risk_agent(spot: float, direction: str, quant: dict, equity: float = 10000.0,
               risk_pct: float = 1.0, sl_pts: float = 50.0) -> dict:
    """Position sizing: risiko % ekuitas / jarak SL. 1 lot XAU = 100 oz."""
    risk_usd = equity * risk_pct / 100
    lot = risk_usd / (sl_pts * 100) if sl_pts > 0 else 0.0
    lot = max(0.01, round(lot, 2))
    vol = quant.get("vol_daily", 0)
    if vol > 1.5:  # pasar liar -> kecilkan setengah
        lot = round(max(0.01, lot / 2), 2)
        note = f"Volatilitas tinggi ({vol}%) → lot dipangkas ke {lot}"
    else:
        note = f"Risiko {risk_pct}% (${risk_usd:,.0f}) dgn SL {sl_pts} pts → {lot} lot"
    return {"equity": equity, "risk_pct": risk_pct, "risk_usd": round(risk_usd, 2),
            "lots": lot, "note": note, "direction": direction}


def execution_agent(direction: str, spot: float, sl_pts: float, tp1_pts: float,
                    tp2_pts: float, risk: dict) -> dict:
    if direction == "BUY":
        entry, sl = round(spot, 2), round(spot - sl_pts, 2)
        tp1, tp2 = round(spot + tp1_pts, 2), round(spot + tp2_pts, 2)
    elif direction == "SELL":
        entry, sl = round(spot, 2), round(spot + sl_pts, 2)
        tp1, tp2 = round(spot - tp1_pts, 2), round(spot - tp2_pts, 2)
    else:
        return {"action": "WAIT", "reason": "Direktur memutuskan tunggu (bias netral)"}
    return {"action": f"{direction} LIMIT @ {entry:,.2f}", "entry": entry, "sl": sl,
            "tp1": tp1, "tp2": tp2, "lots": risk.get("lots"),
            "order": f"{direction} {risk.get('lots')} lot XAUUSD limit {entry:,.2f} | SL {sl:,.2f} | TP1 {tp1:,.2f} | TP2 {tp2:,.2f}"}


def autohedge_pipeline(spot: float, history: list[dict], vibe: dict, fincept: dict,
                       sl_pts: float = 50.0, tp1_pts: float = 100.0,
                       tp2_pts: float = 150.0) -> dict[str, Any]:
    direction = vibe.get("bias", "NETRAL")
    if direction not in ("BUY", "SELL"):
        direction = "WAIT"
    director = director_agent(spot, [float(r["close"]) for r in history], vibe, fincept)
    quant = quant_agent(history, spot)
    risk = risk_agent(spot, direction, quant, sl_pts=sl_pts)
    execution = execution_agent(direction, spot, sl_pts, tp1_pts, tp2_pts, risk)
    return {"engine": "autohedge", "director": director, "quant": quant,
            "risk": risk, "execution": execution}
