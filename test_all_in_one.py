"""Uji tool MCP get_gold_all_in_one tanpa jaringan (fetcher di-mock).

Verifikasi:
  1. /m5      -> bias M15, entry M1, zona 5 pip, SL 50, TP 100/150
  2. /intraday-> bias H1,  entry M5
  3. /swing   -> bias D1,  entry H1
  4. spot_price sama dengan harga spot yang di-mock (bukan 0/negatif)
  5. M1 gagal dimuat -> fallback M5 + catatan di faktor konfluensi
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

import gold_mcp_server as g  # noqa: E402

HARGA_SPOT = 4340.70


def bar(n: int, base: float, step: float, dasar: float | None = None) -> list[dict[str, object]]:
    """Bar OHLC sintetis: tren naik akseleratif dengan pullback (RSI moderat)."""
    pola = (1.0, 1.0, -0.6, 1.0, 0.8, -0.5)
    rows: list[dict[str, object]] = []
    p = base if dasar is None else dasar
    for i in range(n):
        p += step * pola[i % len(pola)] * (1 + i / (2.0 * n))
        rows.append({"open": round(p - step, 2), "high": round(p + 1.2, 2),
                     "low": round(p - 1.2, 2), "close": round(p, 2),
                     "time": f"2026-09-22T{i:02d}:00:00Z"})
    return rows


def rata(n: int, harga: float) -> list[dict[str, object]]:
    """Bar datar (tanpa tren) untuk menguji jalur WAIT."""
    return [{"open": harga, "high": harga + 0.5, "low": harga - 0.5,
             "close": harga, "time": f"2026-09-22T{i:02d}:00:00Z"} for i in range(n)]


DATA = {
    "1m": bar(240, 4335.0, 0.05),
    "5m": bar(200, 4332.0, 0.15),
    "15m": bar(120, 4310.0, 0.50),
    "60m": bar(120, 4280.0, 1.50),
    "1d": bar(90, 4100.0, 12.0),
}


def pasang_mock(blokir_m1: bool = False, datar: bool = False) -> None:
    data = {k: rata(len(v), HARGA_SPOT) for k, v in DATA.items()} if datar else DATA
    g.fetch_gold_price_raw = lambda: {"price": HARGA_SPOT, "change": "+1.0"}  # type: ignore[assignment]
    g.fetch_daily_history = lambda: [dict(r) for r in data["1d"]]  # type: ignore[assignment]

    def _intraday(interval: str = "15m") -> list[dict[str, object]]:
        if blokir_m1 and interval == "1m":
            raise RuntimeError("interval 1m tidak tersedia (uji fallback)")
        return [dict(r) for r in data.get(interval, data["15m"])]

    g.fetch_intraday_history = _intraday  # type: ignore[assignment]


def main() -> int:
    ok = True
    pasang_mock()
    harapan = {"m5": ("M15", "M1", 5.0), "intraday": ("H1", "M5", 8.0),
               "swing": ("D1", "H1", 20.0)}
    for mode, (tf_bias, tf_entry, zona_pip) in harapan.items():
        a = g.get_gold_all_in_one(mode)
        lebar = round(a["zona_entry"]["atas"] - a["zona_entry"]["bawah"], 2)
        jarak_sl = round(abs(a["entry_limit"] - a["stop_loss"]), 2)
        benar = (
            a["timeframe_bias"] == tf_bias
            and a["timeframe_entry"] == tf_entry
            and abs(a["spot_price"] - HARGA_SPOT) < 0.01
            and a["direction"] in ("BUY", "SELL")
            and lebar == zona_pip
            and jarak_sl > 0
            and a["report"].strip() != ""
        )
        ok = ok and benar
        print(f"[{'OK ' if benar else 'GAGAL'}] /{mode}: bias {a['timeframe_bias']} -> "
              f"entry {a['timeframe_entry']} | spot {a['spot_price']:,.2f} | "
              f"{a['direction']} | zona {lebar:g} pip | SL {jarak_sl:g} pip | "
              f"lot {a['lot']} | conf {a['confidence']}%")

    # Jalur WAIT: data datar -> tidak ada bias, zona/lot nol
    pasang_mock(datar=True)
    a = g.get_gold_all_in_one("m5")
    wait_ok = (a["direction"] == "WAIT" and a["zona_entry"]["bawah"] == 0.0
               and a["zona_entry"]["atas"] == 0.0 and a["lot"] == 0.0
               and "TUNGGU" in a["report"])
    ok = ok and wait_ok
    print(f"[{'OK ' if wait_ok else 'GAGAL'}] data datar -> {a['direction']} "
          f"(zona {a['zona_entry']['bawah']}-{a['zona_entry']['atas']}, lot {a['lot']})")

    # Fallback M1 -> M5
    pasang_mock(blokir_m1=True)
    a = g.get_gold_all_in_one("m5")
    catatan = any("belum tersedia" in x for x in a["confluence_factors"])
    fallback_ok = a["timeframe_entry"] == "M5" and catatan
    ok = ok and fallback_ok
    print(f"[{'OK ' if fallback_ok else 'GAGAL'}] fallback M1 gagal -> entry "
          f"{a['timeframe_entry']} (catatan: {catatan})")

    pasang_mock()
    print("\n--- laporan /m5 (25 baris pertama) ---")
    for line in g.get_gold_all_in_one_html("m5").splitlines()[:25]:
        print(line)
    print("\nSEMUA OK" if ok else "\nADA MASALAH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())



if __name__ == "__main__":
    sys.exit(main())
