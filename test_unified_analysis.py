"""Uji cepat unified_analysis.py tanpa jaringan (data sintetis).

Fokus verifikasi:
  1. scalp  -> bias M15, entry M1
  2. intraday -> bias H1, entry M5
  3. swing  -> bias D1, entry H1
  4. spot yang dipakai benar (bukan 0 / negatif)
  5. zona entry selebar 5 pip (scalp) + SL 50 pip + TP 100/150 pip
  6. fallback M5 dipakai bila data M1 tidak tersedia
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

from unified_analysis import (  # noqa: E402
    LTF_BY_MODE, SL_PIPS_BY_MODE, TP1_PIPS_BY_MODE, TP2_PIPS_BY_MODE,
    ZONE_PIPS_BY_MODE, UnifiedAnalyzer,
)


def make(n: int, base: float, step: float) -> list[dict[str, float]]:
    """Bar sintetis tren naik akseleratif dengan pullback (RSI tidak ekstrem)."""
    pola = (1.0, 1.0, -0.6, 1.0, 0.8, -0.5)
    rows: list[dict[str, float]] = []
    p = base
    for i in range(n):
        p += step * pola[i % len(pola)] * (1 + i / (2.0 * n))
        rows.append({"open": round(p - step, 2), "high": round(p + 1.2, 2),
                     "low": round(p - 1.2, 2), "close": round(p, 2)})
    return rows


def main() -> int:
    hist = {
        "D1": make(60, 4100.0, 15.0),
        "H1": make(80, 4280.0, 2.0),
        "M15": make(90, 4300.0, 0.6),
        "M5": make(120, 4330.0, 0.15),
        "M1": make(200, 4335.0, 0.05),
    }
    spot = 4340.70
    analyzer = UnifiedAnalyzer()
    ok = True

    for mode, htf_want in (("scalp", "M15"), ("intraday", "H1"), ("swing", "D1")):
        r = analyzer.analyze(mode, hist, spot=spot)
        lebar_zona = round(r.zona_atas - r.zona_bawah, 2)
        sl_jarak = round(abs(r.entry_limit - r.sl), 2)
        tp1_jarak = round(abs(r.tp1 - r.entry_limit), 2)
        benar = (
            r.htf.timeframe == htf_want
            and r.ltf.timeframe == LTF_BY_MODE[mode]
            and not r.ltf.fallback_used
            and abs(r.spot - spot) < 0.01
            and lebar_zona == ZONE_PIPS_BY_MODE[mode]
            and sl_jarak == SL_PIPS_BY_MODE[mode]
            and tp1_jarak == TP1_PIPS_BY_MODE[mode]
            and abs(r.tp2 - r.entry_limit) == TP2_PIPS_BY_MODE[mode]
            and r.direction in ("BUY", "SELL")
        )
        ok = ok and benar
        print(f"[{'OK ' if benar else 'GAGAL'}] {mode}: bias {r.htf.timeframe} -> entry "
              f"{r.ltf.timeframe} | {r.direction} entry {r.entry_limit:,.2f} "
              f"zona {r.zona_bawah:,.2f}-{r.zona_atas:,.2f} ({lebar_zona:g} pip) "
              f"SL {r.sl:,.2f} ({sl_jarak:g} pip) TP1 {r.tp1:,.2f} ({tp1_jarak:g} pip) "
              f"lot {r.lot} conf {r.confidence}%")

    # fallback: M1 tidak ada -> scalp pakai M5
    tanpa_m1 = {k: v for k, v in hist.items() if k != "M1"}
    r = analyzer.analyze("scalp", tanpa_m1, spot=spot)
    fallback_ok = r.ltf.timeframe == "M5" and r.ltf.fallback_used
    ok = ok and fallback_ok
    print(f"[{'OK ' if fallback_ok else 'GAGAL'}] fallback: scalp tanpa M1 -> "
          f"{r.ltf.timeframe} (fallback={r.ltf.fallback_used})")

    contoh = analyzer.analyze("scalp", hist, spot=spot)
    print("\n--- contoh laporan /m5 ---")
    print(contoh.report)
    print("\nSEMUA OK" if ok else "\nADA MASALAH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
