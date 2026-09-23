"""Uji matriks zona -> bias (pilih timeframe + gaya) TANPA jaringan.

Fokus verifikasi:
  1. Peta zona->bias lengkap untuk 9 timeframe + acuan M1<-M15 & M5<-H1,
     rasio dijaga 6-30x supaya bias tetap relevan dengan horizon zona.
  2. Katalog TF + resample bar (M3 dari 1m, M10/M20 dari 5m, H4 dari 60m).
  3. build_mtf_signal untuk SEMUA kombinasi TF x gaya: zona & bias sesuai peta,
     level SL/TP berurutan benar, lot > 0, laporan memuat peta TF.
  4. Gaya mengubah profil risiko (lebar zona/SL/TP: scalping < intraday < swing).
  5. Saringan tren: intraday -> D1 (soft), swing -> W1 (hard: berlawanan=TUNGGU).
  6. Jalur WAIT: data datar -> zona 0/lot 0 + alasan jelas.
  7. Fallback: data zona/bias utama kosong -> pakai cadangan + catatan laporan.
  8. Menu get_gold_timeframes sinkron dengan peta zona->bias.

    python test_mtf_signal.py
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

import gold_mcp_server as g  # noqa: E402
import unified_analysis as ua  # noqa: E402

HARGA_SPOT = 4340.70
TF_SEMUA = [*ua.ZONE_TF_ORDER, "D1", "W1"]

_results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    _results.append((name, ok))
    print(f"{'OK  ' if ok else 'GAGAL'} {name}")


def bar(n: int, base: float, step: float, pad: float = 1.2) -> list[dict[str, float]]:
    """Bar OHLC sintetis: tren naik akseleratif dengan pullback (RSI moderat).

    OHLC selalu konsisten (high >= max(open, close), low <= min(open, close))
    berapa pun `step`-nya, supaya validasi data di test ini benar-benar ketat.
    `pad` = setengah rentang tambahan di atas/bawah bar.
    """
    pola = (1.0, 1.0, -0.6, 1.0, 0.8, -0.5)
    rows: list[dict[str, float]] = []
    p = base
    for i in range(n):
        p += step * pola[i % len(pola)] * (1 + i / (2.0 * n))
        atas, bawah = max(p, p - step), min(p, p - step)
        rows.append({"open": round(p - step, 2), "high": round(atas + pad, 2),
                     "low": round(bawah - pad, 2), "close": round(p, 2),
                     "time": f"2026-09-22T{i:02d}:00:00Z"})
    return rows


def rata(n: int, harga: float) -> list[dict[str, float]]:
    """Bar datar (tanpa tren) untuk menguji jalur WAIT."""
    return [{"open": harga, "high": harga + 0.5, "low": harga - 0.5,
             "close": harga, "time": f"2026-09-22T{i:02d}:00:00Z"} for i in range(n)]


def data_naik() -> dict[str, list[dict[str, float]]]:
    """Semua timeframe tren naik, dengan ATR sebanding ATR nyata XAUUSD.

    Rentang bar = 4x lantai zona TF (semakin besar TF semakin besar rentang),
    supaya penskalaan lebar zona & SL/TP oleh GAYA benar-benar terlihat.
    """
    hasil: dict[str, list[dict[str, float]]] = {}
    for tf in TF_SEMUA:
        rng = 4 * ua.TF_MIN_ZONE_PIPS[tf]
        hasil[tf] = bar(140, 4100.0 + 40 * ua.TF_MINUTES[tf] ** 0.5, rng * 0.6,
                        pad=rng * 0.2)
    return hasil


def pasang_mock(
    data: dict[str, list[dict[str, float]]], catat: dict[str, int] | None = None
) -> None:
    """Mock harga spot + fetch_tf_history (tanpa jaringan)."""
    g._cache.clear()

    def _harga() -> dict[str, object]:
        return {"price": HARGA_SPOT, "change": "+1.0"}

    def _tf(tf: str) -> list[dict[str, float]]:
        label = ua.normalize_tf(tf)
        if catat is not None:
            catat[label] = catat.get(label, 0) + 1
        if label not in data:
            raise RuntimeError(f"TF {label} tidak tersedia (mock)")
        return [dict(r) for r in data[label]]

    g.fetch_gold_price_raw = _harga  # type: ignore[assignment]
    g.fetch_tf_history = _tf  # type: ignore[assignment]


def uji_peta() -> bool:
    """Peta zona->bias: lengkap, acuan benar, dan rasionya wajar (6-30x)."""
    ok = set(ua.ZONE_BIAS_BY_TF) == set(ua.ZONE_TF_ORDER)
    ok = ok and ua.ZONE_BIAS_BY_TF["M1"] == "M15" and ua.ZONE_BIAS_BY_TF["M5"] == "H1"
    rasio = {z: ua.zone_ratio(z, ua.ZONE_BIAS_BY_TF[z]) for z in ua.ZONE_TF_ORDER}
    ok = ok and all(6.0 <= r <= 30.0 for r in rasio.values())
    print("  peta:", " | ".join(
        f"{z}->{ua.ZONE_BIAS_BY_TF[z]} ({rasio[z]:g}x)" for z in ua.ZONE_TF_ORDER))
    check("peta zona->bias lengkap + acuan M1<-M15 & M5<-H1 + rasio 6-30x", ok)
    check("cadangan zona selalu mulai dari zona pilihan",
          all(ua.zone_priority(z)[0] == z for z in ua.ZONE_TF_ORDER))
    check("cadangan bias selalu mulai dari bias peta",
          all(ua.bias_priority(z)[0] == ua.ZONE_BIAS_BY_TF[z] for z in ua.ZONE_TF_ORDER))
    return ok


def uji_resample() -> bool:
    """Resample bar: OHLC agregat benar + bar depan tak genap dibuang."""
    rows = [{"open": float(i), "high": i + 1.0, "low": i - 1.0, "close": i + 0.5}
            for i in range(1, 8)]  # 7 bar
    hasil = g._resample_bars(rows, 3)
    ok = len(hasil) == 2  # 7 // 3 = 2 kelompok -> 1 bar depan dibuang
    ok = ok and hasil[0] == {"open": 2.0, "high": 5.0, "low": 1.0, "close": 4.5}
    ok = ok and hasil[1] == {"open": 5.0, "high": 8.0, "low": 4.0, "close": 7.5}
    ok = ok and g._resample_bars(rows, 1) == rows
    ok = ok and g._resample_bars([], 3) == []
    print("  7 bar --gabung 3-->", hasil)
    check("resample 3x: OHLC agregat + bar depan tak genap dibuang", ok)
    return ok


def uji_katalog_tf() -> bool:
    """fetch_tf_history: tiap TF terisi, >=25 bar, hasil resample sesuai basis."""
    data_basis = {
        "1m": bar(300, 4300.0, 0.05), "5m": bar(200, 4320.0, 0.15),
        "15m": bar(120, 4300.0, 0.50), "30m": bar(120, 4280.0, 0.90),
        "60m": bar(120, 4200.0, 1.50), "1d": bar(90, 4100.0, 12.0),
        "1wk": bar(80, 3000.0, 40.0),
    }
    g._cache.clear()
    g.fetch_intraday_history = lambda interval="15m": [dict(r) for r in data_basis[interval]]  # type: ignore[assignment]
    g.fetch_daily_history = lambda: [dict(r) for r in data_basis["1d"]]  # type: ignore[assignment]
    g.fetch_weekly_history = lambda: [dict(r) for r in data_basis["1wk"]]  # type: ignore[assignment]

    ok = True
    jumlah: dict[str, int] = {}
    for tf in TF_SEMUA:
        rows = g.fetch_tf_history(tf)
        jumlah[tf] = len(rows)
        ok = ok and len(rows) >= 25
        ok = ok and all(
            float(r["high"]) >= max(float(r["open"]), float(r["close"]))
            and float(r["low"]) <= min(float(r["open"]), float(r["close"]))
            for r in rows)
    ekspektasi = {"M1": 300, "M3": 100, "M5": 200, "M10": 100, "M15": 120,
                  "M20": 50, "M30": 120, "H1": 120, "H4": 30, "D1": 90, "W1": 80}
    ok = ok and jumlah == ekspektasi
    print("  jumlah bar per TF:", jumlah)
    check("katalog TF: OHLC valid & jumlah bar sesuai resample", ok)

    sebelum = len(g._cache)
    g.fetch_tf_history("H4")
    check("cache per TF aktif (panggilan kedua tidak resample ulang)",
          len(g._cache) == sebelum)
    return ok


def uji_matriks_penuh() -> bool:
    """Semua kombinasi TF x gaya: peta dipatuhi + level SL/TP & laporan valid."""
    pasang_mock(data_naik())
    ok = True
    ringkas: list[str] = []
    for style in ua.STYLE_ORDER:
        for zone in ua.ZONE_TF_ORDER:
            a = g.get_gold_mtf_signal(zone, style)
            bias_want = ua.ZONE_BIAS_BY_TF[zone]
            arah = a["direction"]
            lebar = round(a["zona_entry"]["atas"] - a["zona_entry"]["bawah"], 2)
            sl_jauh = round(abs(a["entry_limit"] - a["stop_loss"]), 2)
            urut_ok = (
                (arah == "BUY" and a["stop_loss"] < a["entry_limit"]
                 < a["take_profit_1"] < a["take_profit_2"])
                or (arah == "SELL" and a["stop_loss"] > a["entry_limit"]
                    > a["take_profit_1"] > a["take_profit_2"])
            )
            benar = (
                a["timeframe_zone"] == zone
                and a["timeframe_bias"] == bias_want
                and a["timeframe_bias_peta"] == bias_want
                and a["timeframe_zone_terpakai"] == zone
                and a["style"] == style
                and abs(a["spot_price"] - HARGA_SPOT) < 0.01
                and arah in ("BUY", "SELL")
                and lebar > 0 and sl_jauh > 0 and urut_ok
                and a["lot"] > 0 and 0 < a["confidence"] <= 95
                and f"zona *{zone}* ← bias *{bias_want}*" in a["report"]
            )
            ok = ok and benar
            ringkas.append(
                f"{style}/{zone}: bias {a['timeframe_bias']} | {arah} zona "
                f"{lebar:g} pip | SL {sl_jauh:g} | lot {a['lot']} | conf {a['confidence']}")
            if not benar:
                print("   GAGAL:", ringkas[-1])
    for baris in ringkas[:6]:
        print("  ", baris)
    check("27 kombinasi TF x gaya: zona/bias sesuai peta + level & laporan valid", ok)
    return ok


def uji_gaya_profil_risiko() -> bool:
    """Gaya mengubah profil risiko: zona & SL/TP melebar scalping<intraday<swing."""
    pasang_mock(data_naik())
    ukur: dict[str, tuple[float, float, float]] = {}
    for style in ua.STYLE_ORDER:
        a = g.get_gold_mtf_signal("M15", style)  # TF sama -> beda gaya saja
        ukur[style] = (
            round(a["zona_entry"]["atas"] - a["zona_entry"]["bawah"], 2),
            round(abs(a["entry_limit"] - a["stop_loss"]), 2),
            round(abs(a["take_profit_2"] - a["entry_limit"]), 2),
        )
    naik = (ukur["scalping"][0] < ukur["intraday"][0] < ukur["swing"][0]
            and ukur["scalping"][1] < ukur["intraday"][1] < ukur["swing"][1]
            and ukur["scalping"][2] < ukur["intraday"][2] < ukur["swing"][2])
    print("  zona/SL/TP2 per gaya (M15):", ukur)
    check("gaya melebarkan zona & SL/TP (scalping < intraday < swing)", naik)
    rr_ok = all(
        round(abs(tp2_jauh / sl_jauh), 2) == ua.STYLE_PRESETS[s]["rr2"]
        for s, (_, sl_jauh, tp2_jauh) in ukur.items())
    check("R:R TP2 tetap 1:3 (dari preset gaya) di semua gaya", rr_ok)
    return naik and rr_ok


def uji_saringan() -> bool:
    """Saringan tren: D1 untuk intraday (soft), W1 untuk swing (hard)."""
    data = data_naik()
    data["W1"] = bar(140, 9000.0, -40.0, pad=8.0)  # tren mingguan turun -> SELL
    pasang_mock(data)
    a_intra = g.get_gold_mtf_signal("M1", "intraday")
    a_swing = g.get_gold_mtf_signal("M1", "swing")
    intra_ok = (a_intra["filter_timeframe"] == "D1" and a_intra["filter_bias"] == "BUY"
                and a_intra["direction"] == "BUY"
                and "Saringan D1" in a_intra["report"])
    swing_ok = (a_swing["filter_timeframe"] == "W1" and a_swing["filter_bias"] == "SELL"
                and a_swing["direction"] == "WAIT" and "W1" in a_swing["wait_reason"]
                and "TUNGGU" in a_swing["report"])
    check("intraday pakai saringan D1 (searah -> sinyal tetap keluar)", intra_ok)
    check("swing wajib searah W1 (berlawanan -> TUNGGU + alasan)", swing_ok)
    print("   alasan tunggu swing:", a_swing["wait_reason"])

    data2 = data_naik()
    data2["D1"] = bar(140, 9000.0, -40.0, pad=8.0)  # D1 turun -> saringan soft
    pasang_mock(data2)
    a_soft = g.get_gold_mtf_signal("M15", "intraday")  # bias H4, saringan D1
    soft_ok = (a_soft["direction"] == "BUY" and a_soft["filter_bias"] == "SELL"
               and a_soft["filter_timeframe"] == "D1"
               and any("dikurangi 15 poin" in x for x in a_soft["confluence_factors"]))
    check("saringan soft berlawanan -> confidence dipotong (bukan TUNGGU)", soft_ok)
    return intra_ok and swing_ok and soft_ok


def uji_wait_dan_fallback() -> bool:
    """Data datar -> WAIT; data utama kosong -> pakai cadangan."""
    pasang_mock({tf: rata(140, HARGA_SPOT) for tf in TF_SEMUA})
    a = g.get_gold_mtf_signal("M5", "intraday")
    wait_ok = (a["direction"] == "WAIT" and a["zona_entry"]["bawah"] == 0.0
               and a["zona_entry"]["atas"] == 0.0 and a["lot"] == 0.0
               and "TUNGGU" in a["report"])
    check("data datar -> WAIT (zona & lot nol, laporan TUNGGU)", wait_ok)

    data = data_naik()  # zona M1 tidak tersedia -> cadangan terdekat (M3)
    del data["M1"]
    pasang_mock(data)
    a = g.get_gold_mtf_signal("M1", "scalping")
    zona_ok = (a["timeframe_zone"] == "M1" and a["timeframe_zone_terpakai"] == "M3"
               and any("M1 belum tersedia" in x for x in a["confluence_factors"]))
    check("zona M1 tanpa data -> entry pakai M3 + catatan fallback", zona_ok)

    data = data_naik()  # bias M15 tidak tersedia -> bias cadangan (M30)
    del data["M15"]
    pasang_mock(data)
    a = g.get_gold_mtf_signal("M1", "scalping")
    bias_ok = (a["timeframe_bias_peta"] == "M15" and a["timeframe_bias"] == "M30"
               and "Bias M15 belum tersedia" in a["report"])
    check("bias M15 tanpa data -> bias pakai M30 + catatan di laporan", bias_ok)

    pasang_mock({})  # semua sumber gagal -> error jelas (bukan laporan kosong)
    try:
        g.get_gold_mtf_signal("M5", "swing")
        error_ok = False
    except Exception as exc:  # noqa: BLE001 - memang diharapkan
        error_ok = "OHLC" in str(exc)
        print("   error yang benar:", exc)
    check("semua sumber data gagal -> RuntimeError informatif", error_ok)
    return wait_ok and zona_ok and bias_ok and error_ok


def uji_menu() -> bool:
    """Menu TF/gaya untuk keyboard bot sinkron dengan peta zona->bias."""
    pasang_mock(data_naik())
    menu = g.get_gold_timeframes()
    kode = [t["kode"] for t in menu["timeframes"]]
    gaya = [s["kode"] for s in menu["styles"]]
    ok = (kode == list(ua.ZONE_TF_ORDER) and gaya == list(ua.STYLE_ORDER))
    ok = ok and all(t["bias"] == ua.ZONE_BIAS_BY_TF[t["kode"]] for t in menu["timeframes"])
    ok = ok and menu["peta_zona_bias"] == ua.ZONE_BIAS_BY_TF
    ok = ok and all(s["valid_hours"] > 0 and s["deskripsi"] for s in menu["styles"])
    print("  menu TF:", kode)
    print("  menu gaya:", [(s["kode"], s["valid_hours"], s["filter_tf"])
                          for s in menu["styles"]])
    check("menu TF & gaya lengkap + sinkron dengan peta zona->bias", ok)
    return ok


def main() -> int:
    print("=== 1) peta zona->bias ==="); uji_peta()
    print("\n=== 2) resample bar ==="); uji_resample()
    print("\n=== 3) katalog TF & cache ==="); uji_katalog_tf()
    print("\n=== 4) matriks penuh 9 TF x 3 gaya ==="); uji_matriks_penuh()
    print("\n=== 5) profil risiko per gaya ==="); uji_gaya_profil_risiko()
    print("\n=== 6) saringan tren (D1/W1) ==="); uji_saringan()
    print("\n=== 7) WAIT & fallback ==="); uji_wait_dan_fallback()
    print("\n=== 8) menu bot ==="); uji_menu()

    pasang_mock(data_naik())
    print("\n--- contoh laporan: zona M1 + SCALPING (bias M15) ---")
    for line in g.get_gold_mtf_signal_html("M1", "scalping").splitlines()[:14]:
        print(line)
    print("\n--- contoh laporan: zona M5 + INTRADAY (bias H1) ---")
    for line in g.get_gold_mtf_signal_html("M5", "intraday").splitlines()[:14]:
        print(line)

    gagal = [n for n, ok in _results if not ok]
    print(f"\n=== {len(_results) - len(gagal)}/{len(_results)} CEK LULUS ===")
    if gagal:
        print("GAGAL: " + ", ".join(gagal))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
