"""Fusion entry-zone: gabungan 3 repo -> SATU zona entry presisi (pure python)."""
from __future__ import annotations


def _overlap(a_lo: float, a_hi: float, b_lo: float, b_hi: float):
    lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
    return (lo, hi) if lo < hi else None


def collect_layers(smc: dict, vibe: dict, fin: dict, hedge: dict, spot: float) -> list[dict]:
    """Tiap engine = 1 lapis zona: SMC-POI, EMA21-M15, Bollinger, DCF-value, Risk-band."""
    layers: list[dict] = []
    try:
        z = smc.get("zona_m15") or {}
        if z.get("bawah") and z.get("atas"):
            layers.append({"nama": "SMC-POI", "bawah": float(z["bawah"]),
                           "atas": float(z["atas"]), "arah": smc.get("bias", "")})
    except (TypeError, ValueError):
        pass
    try:
        atr = float(smc.get("atr15") or vibe.get("atr14") or 5.0)
        ema21 = float(smc.get("ema21_m15") or vibe.get("ema21") or spot)
        w = 0.2 * atr
        layers.append({"nama": "EMA21-M15", "bawah": round(ema21 - w, 2),
                       "atas": round(ema21 + w, 2), "arah": smc.get("bias", "")})
    except (TypeError, ValueError):
        pass
    try:
        bb = vibe.get("bollinger") or {}
        if bb.get("lower") and bb.get("upper"):
            layers.append({"nama": "Bollinger", "bawah": float(bb["lower"]),
                           "atas": float(bb["upper"]), "arah": vibe.get("bias", "")})
    except (TypeError, ValueError):
        pass
    try:
        fair = float(fin.get("dcf", {}).get("fair_value") or spot)
        w = abs(fair) * 0.02
        gv = fin.get("dcf", {}).get("verdict", "FAIR")
        arah = "BUY" if gv == "UNDERVALUED" else ("SELL" if gv == "OVERVALUED" else "NETRAL")
        layers.append({"nama": "DCF-value", "bawah": round(fair - w, 2),
                       "atas": round(fair + w, 2), "arah": arah})
    except (TypeError, ValueError):
        pass
    try:
        risk_usd = float(hedge.get("risk", {}).get("risk_usd", 100))
        sl_pts = min(max(abs(risk_usd) / 100, 5.0), 100.0)
        layers.append({"nama": "Risk-band", "bawah": round(spot - sl_pts / 2, 2),
                       "atas": round(spot + sl_pts / 2, 2),
                       "arah": hedge.get("director", {}).get("vibe_bias", "")})
    except (TypeError, ValueError):
        pass
    return layers


def find_best_zone(layers: list[dict], votes: list[str], spot: float, atr: float) -> dict:
    """Overlap antar lapis -> zona terbaik + skor 0-10 + grade A+/A/B/C."""
    cands: list[dict] = []
    for L in layers:
        cands.append({"bawah": L["bawah"], "atas": L["atas"], "lapis": [L["nama"]]})
    for i in range(len(layers)):
        for j in range(i + 1, len(layers)):
            ov = _overlap(layers[i]["bawah"], layers[i]["atas"],
                          layers[j]["bawah"], layers[j]["atas"])
            if ov:
                cands.append({"bawah": round(ov[0], 2), "atas": round(ov[1], 2),
                              "lapis": [layers[i]["nama"], layers[j]["nama"]]})
    if not cands:
        return {"bawah": round(spot, 2), "atas": round(spot, 2), "lapis": [],
                "skor_0_10": 0.0, "grade": "C", "lebar_atr": 0.0,
                "status": "tidak ada zona (data kurang)"}
    norm = {"BUY": 1, "SELL": -1}
    vote_bias = sum(norm.get(v, 0) for v in votes)
    best = None
    for c in cands:
        tol = 0.25 * (atr or 5.0)
        hit = [L["nama"] for L in layers
               if not (L["atas"] < c["bawah"] - tol or L["bawah"] > c["atas"] + tol)]
        skor = round(min(10.0, len(set(hit + c["lapis"])) * 2.0 + min(2.0, abs(vote_bias) * 0.5)), 1)
        lebar = round((c["atas"] - c["bawah"]) / (atr or 1.0), 2)
        if lebar > 2.5:
            skor = round(max(0.0, skor - 2.0), 1)
        c2 = {"bawah": c["bawah"], "atas": c["atas"], "lapis": sorted(set(hit + c["lapis"])),
              "skor_0_10": skor, "lebar_atr": lebar}
        key = (skor, -abs(lebar - 0.8))
        if best is None or key > (best["skor_0_10"], -abs(best["lebar_atr"] - 0.8)):
            best = c2
    assert best is not None
    grade = "A+" if best["skor_0_10"] >= 8 else ("A" if best["skor_0_10"] >= 6
            else ("B" if best["skor_0_10"] >= 4 else "C"))
    mid = (best["bawah"] + best["atas"]) / 2
    if spot < best["bawah"]:
        status = f"harga di BAWAH zona ({spot:,.2f} < {best['bawah']:,.2f}) -> limit BUY di {best['bawah']:,.2f}"
    elif spot > best["atas"]:
        status = f"harga di ATAS zona ({spot:,.2f} > {best['atas']:,.2f}) -> limit SELL di {best['atas']:,.2f}"
    else:
        status = f"harga DI DALAM zona ({best['bawah']:,.2f}-{best['atas']:,.2f}) -> entry searah bias, mid {mid:,.2f}"
    best["grade"] = grade
    best["status"] = status
    return best


def plan_entry(arah: str, zone: dict, spot: float, atr: float,
               sl_pts: float, tp1_pts: float, tp2_pts: float) -> dict:
    """Entry LIMIT di batas zona searah bias; SL di luar zona + buffer 0.2 ATR."""
    buf = round(0.2 * (atr or 5.0), 2)
    if arah == "BUY":
        entry = min(zone["bawah"], spot)
        sl = round(min(entry - sl_pts, zone["bawah"] - buf - sl_pts * 0.2), 2)
        tp1, tp2 = round(entry + tp1_pts, 2), round(entry + tp2_pts, 2)
    elif arah == "SELL":
        entry = max(zone["atas"], spot)
        sl = round(max(entry + sl_pts, zone["atas"] + buf + sl_pts * 0.2), 2)
        tp1, tp2 = round(entry - tp1_pts, 2), round(entry - tp2_pts, 2)
    else:
        entry = round((zone["bawah"] + zone["atas"]) / 2, 2)
        sl = tp1 = tp2 = entry
    return {"entry_limit": round(entry, 2), "stop_loss": sl,
            "take_profit_1": tp1, "take_profit_2": tp2,
            "jarak_entry_pts": round(abs(entry - spot), 2)}

