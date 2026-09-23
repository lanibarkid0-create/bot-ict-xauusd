"""Fincept adapter (terinspirasi FinceptTerminal).
Analitik gaya terminal Bloomberg-gratis: DCF/value-gap, VaR/Sharpe,
derivatives pricing (Black-Scholes sederhana), fixed-income yield,
portfolio optimisation (mean-variance 2 aset), QuantLib-lite.
Tanpa dependensi baru (math saja).
"""
from __future__ import annotations

import math


def _ret(closes: list[float]) -> list[float]:
    return [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1]]


def sharpe_var(closes: list[float]) -> dict:
    r = _ret(closes)
    if len(r) < 5:
        return {"sharpe_daily": 0.0, "var95_daily_pct": 0.0, "vol_daily_pct": 0.0}
    mu = sum(r) / len(r)
    sd = (sum((x - mu) ** 2 for x in r) / len(r)) ** 0.5
    sharpe = round(mu / sd * math.sqrt(252), 2) if sd else 0.0
    var95 = round(-1.645 * sd * 100, 3)
    return {"sharpe_daily": sharpe, "var95_daily_pct": var95,
            "vol_daily_pct": round(sd * 100, 3)}


def dcf_gap(spot: float, closes: list[float], growth: float = 0.03, discount: float = 0.08) -> dict:
    """Value-gap sederhana untuk komoditas: fair value = rata-rata 20 close
    terakhir (jangkar mean-reversion), gap = (spot - fair) / fair * 100.

    Parameter growth/discount dipertahankan untuk kompatibilitas API; pada
    komoditas tanpa arus kas, harga wajar lebih tepat dijangkar ke rata-rata
    pergerakan terakhir, bukan model DCF penuh.
    """
    if not closes:
        return {"fair_value": round(spot, 2), "gap_pct": 0.0, "verdict": "FAIR"}
    window = closes[-20:]
    fair = sum(window) / len(window)
    if fair <= 0:
        return {"fair_value": round(spot, 2), "gap_pct": 0.0, "verdict": "FAIR"}
    gap = round((spot - fair) / fair * 100, 2)
    verdict = "OVERVALUED" if gap > 2 else ("UNDERVALUED" if gap < -2 else "FAIR")
    return {"fair_value": round(fair, 2), "gap_pct": gap, "verdict": verdict}


def black_scholes_call(spot: float, strike: float, t_years: float = 30 / 365,
                       rate: float = 0.04, vol: float = 0.20) -> dict:
    """Harga opsi call Eropa (pendekatan Black-Scholes, N via erf)."""
    def N(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))
    if spot <= 0 or strike <= 0 or t_years <= 0 or vol <= 0:
        return {"call": 0.0, "delta": 0.0}
    d1 = (math.log(spot / strike) + (rate + vol ** 2 / 2) * t_years) / (vol * math.sqrt(t_years))
    d2 = d1 - vol * math.sqrt(t_years)
    call = spot * N(d1) - strike * math.exp(-rate * t_years) * N(d2)
    return {"call": round(call, 2), "delta": round(N(d1), 3)}


def bond_yield(price: float, face: float = 100.0, coupon_pct: float = 5.0, years: int = 10) -> dict:
    approx = (coupon_pct + (face - price) / years) / ((face + price) / 2) * 100
    return {"yield_approx_pct": round(approx, 3)}


def portfolio_2asset(ret_a: list[float], ret_b: list[float]) -> dict:
    """Alokasi min-variance 2 aset (emas vs proxy)."""
    n = min(len(ret_a), len(ret_b))
    if n < 5:
        return {"w_gold": 0.5, "w_proxy": 0.5, "note": "data kurang"}
    a, b = ret_a[-n:], ret_b[-n:]

    def stats(x: list[float]) -> tuple[float, float]:
        m = sum(x) / len(x)
        v = sum((i - m) ** 2 for i in x) / len(x)
        return m, v
    _, va = stats(a)
    _, vb = stats(b)
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / n
    denom = va + vb - 2 * cov
    w = (vb - cov) / denom if denom else 0.5
    w = max(0.0, min(1.0, w))
    return {"w_gold": round(w, 3), "w_proxy": round(1 - w, 3),
            "note": "min-variance 2 aset"}


def fincept_analyze(history: list[dict], spot: float) -> dict:
    closes = [float(r["close"]) for r in history]
    sv = sharpe_var(closes)
    dcf = dcf_gap(spot, closes)
    opt = black_scholes_call(spot, round(spot))
    # proxy obligasi: yield dari harga par 100 disesuaikan volatilitas emas
    bond = bond_yield(100 - sv["vol_daily_pct"])
    rets = _ret(closes)
    half = len(rets) // 2
    port = portfolio_2asset(rets[:half] or rets, rets[half:] or rets)
    risks = []
    if sv["sharpe_daily"] < 0:
        risks.append(f"Sharpe negatif {sv['sharpe_daily']} → tren lemah")
    else:
        risks.append(f"Sharpe {sv['sharpe_daily']} → tren sehat")
    risks.append(f"VaR95 harian {sv['var95_daily_pct']}% (potensi rugi 5% terburuk)")
    risks.append(f"DCF {dcf['verdict']} (gap {dcf['gap_pct']}%)")
    return {"engine": "fincept-terminal", "sharpe_var": sv, "dcf": dcf,
            "option_call_ATM_30d": opt, "bond_proxy": bond,
            "portfolio": port, "risks": risks}
