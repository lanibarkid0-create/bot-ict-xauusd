"""Unified Multi-Timeframe Technical Analysis
==========================================
Gabungan 3 repo (Vibe-Trading, FinceptTerminal, AutoHedge) jadi SATU
analisa teknikal multi-timeframe: HTF menentukan BIAS, LTF menentukan
SATU ZONA ENTRY yang presisi.

Dua cara pakai:

1) Mode paket lama (bias HTF tetap per mode):
     scalp    : bias M15 -> entry M1   (zona 5 pip, SL 50 pip, TP 100/150 pip)
     intraday : bias H1  -> entry M5   (zona 8 pip, SL 80 pip, TP 160/240 pip)
     swing    : bias D1  -> entry H1   (zona 20 pip, SL 200 pip, TP 400/600 pip)

2) Matriks zona -> bias (`analyze_zone`): pengguna memilih TIMEFRAME ZONA
   (M1...H4) lalu GAYA (scalping/intraday/swing). Bias selalu datang dari
   timeframe yang lebih tinggi sesuai ZONE_BIAS_BY_TF
   (zona M1 <- bias M15, zona M5 <- bias H1, dst), sedangkan gaya mengatur
   profil risiko: lebar zona & SL/TP berbasis ATR, masa berlaku order,
   ukuran ekuitas untuk sizing, dan saringan tren TF lebih tinggi
   (intraday -> D1, swing -> W1).

Pemakaian:
    from unified_analysis import UnifiedAnalyzer
    hasil = UnifiedAnalyzer().analyze("scalp", {"M15": bars15, "M1": bars1}, spot=4340.7)
    hasil2 = UnifiedAnalyzer().analyze_zone("M5", "intraday", {"H1": h1, "M5": m5}, spot=4340.7)
    print(hasil2.report)
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from typing import Any, Optional

# 3 repo yang digabung (semua pure python, tanpa dependensi baru).
from vibe_trading import vibe_analyze as vibe_analyze_fn
from fincept_terminal import fincept_analyze as fincept_analyze_fn
from autohedge import director_agent, quant_agent, risk_agent, execution_agent

# ------------------------------------------------------------------ konfig
# XAUUSD: 1 pip = 1.00 poin harga (1 oz). Sesuaikan lewat GOLD_PIP_VALUE bila
# broker memakai kuotasi 2 desimal.
PIP_POINTS = 1.0

MODE_FULL: dict[str, str] = {
    "scalp": "SCALPING",
    "scalping": "SCALPING",
    "intraday": "INTRADAY",
    "swing": "SWING",
}
MODE_ICON: dict[str, str] = {
    "scalp": "⚡",
    "scalping": "⚡",
    "intraday": "📈",
    "swing": "🌊",
}

# HTF = penentu arah/bias, LTF = penentu zona entry.
HTF_BY_MODE: dict[str, str] = {"scalp": "M15", "intraday": "H1", "swing": "D1"}
LTF_BY_MODE: dict[str, str] = {"scalp": "M1", "intraday": "M5", "swing": "H1"}

# Urutan cadangan bila timeframe utama tidak tersedia di sumber data.
HTF_PRIORITY: dict[str, tuple[str, ...]] = {
    "scalp": ("M15", "M5", "H1"),
    "intraday": ("H1", "M15", "M5"),
    "swing": ("D1", "H1", "M15"),
}
LTF_PRIORITY: dict[str, tuple[str, ...]] = {
    "scalp": ("M1", "M5", "M15"),
    "intraday": ("M5", "M15", "M1"),
    "swing": ("H1", "M15", "M5"),
}

# Target tetap per mode (pip).
SL_PIPS_BY_MODE: dict[str, float] = {"scalp": 50.0, "intraday": 80.0, "swing": 200.0}
TP1_PIPS_BY_MODE: dict[str, float] = {"scalp": 100.0, "intraday": 160.0, "swing": 400.0}
TP2_PIPS_BY_MODE: dict[str, float] = {"scalp": 150.0, "intraday": 240.0, "swing": 600.0}
# Lebar SATU zona entry (pip).
ZONE_PIPS_BY_MODE: dict[str, float] = {"scalp": 5.0, "intraday": 8.0, "swing": 20.0}
EQUITY_BY_MODE: dict[str, float] = {
    "scalp": 10000.0,
    "intraday": 25000.0,
    "swing": 50000.0,
}

MODE_ALIAS: dict[str, str] = {
    "m5": "scalp", "m1": "scalp", "scalp": "scalp", "scalping": "scalp",
    "signal": "scalp", "vibe": "scalp",
    "intraday": "intraday", "intra": "intraday", "daytrade": "intraday",
    "smc": "intraday", "fincept": "intraday",
    "swing": "swing", "hedge": "swing", "autohedge": "swing",
    "fusion": "swing",
}


def normalize_mode(mode: str) -> str:
    """Ubah alias ('m5', 'intra', ...) menjadi mode internal."""
    m = (mode or "").strip().lower()
    if m in MODE_ALIAS:
        return MODE_ALIAS[m]
    raise ValueError(
        f"Mode tidak dikenal: {mode!r}. Pakai: scalp / intraday / swing.")


# ================================================== matriks zona -> bias
# Peta "zona -> bias": timeframe tempat ZONA ENTRY dicari (dipilih pengguna)
# dan timeframe yang menentukan BIAS/arah zona itu. Rasio dijaga ~6-30x agar
# bias tetap relevan dengan horizon zona (konvensi multi-timeframe: bias
# minimal ~4x zona supaya zonas masih terpakai, maksimal ~30x supaya bias
# tidak terlalu jauh dari horizon trading).
# Acuan yang diminta: zona M1 -> bias M15, zona M5 -> bias H1.
ZONE_BIAS_BY_TF: dict[str, str] = {
    "M1": "M15",   # 15x — zona 1 menit dibentuk struktur 15 menit
    "M3": "M30",   # 10x
    "M5": "H1",    # 12x
    "M10": "H1",   # 6x
    "M15": "H4",   # 16x
    "M20": "H4",   # 12x
    "M30": "H4",   # 8x
    "H1": "D1",    # 24x
    "H4": "D1",    # 6x — zona 4 jam ditradingkan pada struktur harian
}
# Urutan timeframe yang ditawarkan ke pengguna (cepat -> lambat).
ZONE_TF_ORDER: tuple[str, ...] = (
    "M1", "M3", "M5", "M10", "M15", "M20", "M30", "H1", "H4")
# Menit per timeframe (untuk rasio zona:bias + kedekatan urutan cadangan).
TF_MINUTES: dict[str, float] = {
    "M1": 1, "M3": 3, "M5": 5, "M10": 10, "M15": 15, "M20": 20, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440, "W1": 10080,
}
ZONE_TF_LABEL: dict[str, str] = {
    "M1": "1 menit", "M3": "3 menit", "M5": "5 menit", "M10": "10 menit",
    "M15": "15 menit", "M20": "20 menit", "M30": "30 menit",
    "H1": "1 jam", "H4": "4 jam", "D1": "1 hari", "W1": "1 minggu",
}
TF_ALIAS: dict[str, str] = {
    "1M": "M1", "3M": "M3", "5M": "M5", "10M": "M10", "15M": "M15",
    "20M": "M20", "30M": "M30", "1H": "H1", "4H": "H4", "1D": "D1",
    "1W": "W1", "H": "H1", "D": "D1", "W": "W1",
}
# Lebar minimum zona per timeframe (pip) — jaring pengaman saat ATR sangat
# kecil (pasar datar/libur) supaya zona & SL tidak pernah 0 dan selalu lebih
# lebar dari spread XAUUSD (0,2-0,5 poin). Batas atas zona = 6x nilai ini.
TF_MIN_ZONE_PIPS: dict[str, float] = {
    "M1": 1.0, "M3": 1.2, "M5": 1.5, "M10": 2.0, "M15": 2.5, "M20": 3.0,
    "M30": 4.0, "H1": 6.0, "H4": 10.0, "D1": 30.0, "W1": 80.0,
}


# ================================================== gaya trading (style)
# Gaya BUKAN sekadar label: ia mengubah profil risiko — lebar zona & SL/TP
# (kali ATR timeframe zona), masa berlaku order, ekuitas untuk position
# sizing, syarat minimal engine sepakat, dan saringan tren TF lebih tinggi.
# Rasio SL : lebar zona selalu >= 2 (2.0-2.5) supaya stop di luar rentang
# entry noise, dan R:R tetap 1:2 / 1:3 (rr1/rr2).
STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "scalping": {
        "label": "SCALPING", "icon": "⚡", "zone_atr": 0.60, "sl_atr": 1.50,
        "rr1": 2.0, "rr2": 3.0, "valid_hours": 2.0, "equity": 10000.0,
        "min_agree": 2, "filter_tf": None, "filter_mode": "soft",
        "deskripsi": "Zona ketat, SL/TP pendek, order berlaku 2 jam",
    },
    "intraday": {
        "label": "INTRADAY", "icon": "📈", "zone_atr": 0.90, "sl_atr": 2.00,
        "rr1": 2.0, "rr2": 3.0, "valid_hours": 12.0, "equity": 25000.0,
        "min_agree": 2, "filter_tf": "D1", "filter_mode": "soft",
        "deskripsi": "Zona sedang + saringan tren D1, berlaku 1 sesi",
    },
    "swing": {
        "label": "SWING", "icon": "🌊", "zone_atr": 1.20, "sl_atr": 3.00,
        "rr1": 2.0, "rr2": 3.0, "valid_hours": 72.0, "equity": 50000.0,
        "min_agree": 2, "filter_tf": "W1", "filter_mode": "hard",
        "deskripsi": "Zona lebar, wajib searah tren W1, berlaku 3 hari",
    },
}
STYLE_ALIAS: dict[str, str] = {
    "scalp": "scalping", "scalping": "scalping", "scalp5": "scalping",
    "m5": "scalping", "m1": "scalping", "signal": "scalping",
    "intraday": "intraday", "intra": "intraday", "daytrade": "intraday",
    "smc": "intraday",
    "swing": "swing", "hedge": "swing",
}
STYLE_ORDER: tuple[str, ...] = ("scalping", "intraday", "swing")


def normalize_tf(tf: str) -> str:
    """Normalisasi label timeframe ('15m' / 'M15' / '15M' -> 'M15')."""
    t = (tf or "").strip().upper().replace(" ", "")
    if t in TF_MINUTES:
        return t
    if t in TF_ALIAS:
        return TF_ALIAS[t]
    for sufiks in ("M", "H", "D"):
        if t.endswith(sufiks) and t[:-1].isdigit() and f"{sufiks}{t[:-1]}" in TF_MINUTES:
            return f"{sufiks}{t[:-1]}"
    raise ValueError(
        f"Timeframe tidak dikenal: {tf!r}. Pilih: {', '.join(ZONE_TF_ORDER)}.")


def normalize_zone_tf(tf: str) -> str:
    """Timeframe zona (tempat entry dicari) — harus salah satu ZONE_TF_ORDER."""
    t = normalize_tf(tf)
    if t not in ZONE_TF_ORDER:
        raise ValueError(
            f"Zona {t} tidak didukung. Pilih: {', '.join(ZONE_TF_ORDER)}.")
    return t


def normalize_style(style: str) -> str:
    """Ubah alias gaya ('scalp', 'intra', ...) menjadi kunci internal."""
    s = (style or "").strip().lower()
    if s in STYLE_ALIAS:
        return STYLE_ALIAS[s]
    raise ValueError(
        f"Gaya tidak dikenal: {style!r}. Pakai: {' / '.join(STYLE_ORDER)}.")


def style_preset(style: str) -> dict[str, Any]:
    """Profil risiko gaya terpilih (lihat STYLE_PRESETS)."""
    return STYLE_PRESETS[normalize_style(style)]


def zone_bias_tf(zone_tf: str) -> str:
    """Bias pembentuk zona: M1 -> M15, M5 -> H1, dst (ZONE_BIAS_BY_TF)."""
    return ZONE_BIAS_BY_TF[normalize_zone_tf(zone_tf)]


def zone_ratio(zone_tf: str, bias_tf: str) -> float:
    """Rasio bias:zona (mis. zona M1 & bias M15 -> 15.0)."""
    return round(
        TF_MINUTES[normalize_tf(bias_tf)] / TF_MINUTES[normalize_tf(zone_tf)], 1)


def _tf_by_distance(
    target_menit: float, skip: set[str] | None = None
) -> tuple[str, ...]:
    """Label TF diurutkan dari menit yang paling dekat ke `target_menit`."""
    lewat = skip or set()
    kandidat = [tf for tf in TF_MINUTES if tf not in lewat]
    kandidat.sort(
        key=lambda tf: (abs(math.log2(TF_MINUTES[tf] / target_menit)), TF_MINUTES[tf])
    )
    return tuple(kandidat)


def zone_priority(zone_tf: str) -> tuple[str, ...]:
    """Urutan cadangan zona: zona pilihan, lalu TF terdekat (naik/turun)."""
    z = normalize_zone_tf(zone_tf)
    urut = [tf for tf in _tf_by_distance(TF_MINUTES[z]) if tf in ZONE_TF_ORDER]
    return (z, *[tf for tf in urut if tf != z])


def bias_priority(zone_tf: str, bias_tf: str | None = None) -> tuple[str, ...]:
    """Urutan cadangan bias: bias peta dulu, lalu tangga bias terdekat.

    Cadangan dibatasi ke TF yang memang layak menjadi bias (nilai
    ZONE_BIAS_BY_TF + W1 sebagai jaring pengaman) dan selalu lebih besar dari
    timeframe zona, supaya horizon bias tidak pernah turun di bawah zona.
    Contoh: zona M1, data M15 hilang -> cadangan M30 (bukan M20 yang memang
    lebih dekat tapi di luar tangga bias peta).
    """
    z = normalize_zone_tf(zone_tf)
    b = normalize_tf(bias_tf) if bias_tf else ZONE_BIAS_BY_TF[z]
    target = TF_MINUTES[z] * zone_ratio(z, b)
    tangga = set(ZONE_BIAS_BY_TF.values()) | {"W1"}
    kandidat = sorted(
        (tf for tf in tangga
         if tf != b and TF_MINUTES[tf] > TF_MINUTES[z]),
        key=lambda tf: (abs(math.log2(TF_MINUTES[tf] / target)), TF_MINUTES[tf]),
    )
    return (b, *kandidat)


def tf_min_zone_pips(zone_tf: str) -> float:
    """Lebar minimum zona (pip) untuk sebuah timeframe."""
    return TF_MIN_ZONE_PIPS[normalize_tf(zone_tf)]


def timeframe_menu() -> dict[str, Any]:
    """Menu TF + gaya untuk keyboard Telegram (satu sumber kebenaran)."""
    return {
        "timeframes": [
            {
                "kode": z, "label": z, "bias": ZONE_BIAS_BY_TF[z],
                "rasio": f"{zone_ratio(z, ZONE_BIAS_BY_TF[z]):g}×",
                "durasi": ZONE_TF_LABEL[z],
                "peta": f"{z} → {ZONE_BIAS_BY_TF[z]}",
            }
            for z in ZONE_TF_ORDER
        ],
        "styles": [
            {
                "kode": k, "label": STYLE_PRESETS[k]["label"],
                "ikon": STYLE_PRESETS[k]["icon"],
                "deskripsi": STYLE_PRESETS[k]["deskripsi"],
                "valid_hours": STYLE_PRESETS[k]["valid_hours"],
                "filter_tf": STYLE_PRESETS[k]["filter_tf"],
            }
            for k in STYLE_ORDER
        ],
        "catatan": (
            "Zona = timeframe tempat entry dicari, bias = timeframe penentu "
            "arah (M1←M15, M5←H1, dst). Gaya mengatur lebar zona, SL/TP, masa "
            "berlaku order, dan saringan tren TF lebih tinggi."
        ),
    }


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# -------------------------------------------------------------- data class
@dataclass
class HTFAnalysis:
    """Analisa timeframe tinggi (penentu BIAS)."""
    timeframe: str
    vibe: dict[str, Any]
    fincept: dict[str, Any]
    director: dict[str, Any]
    votes: list[str]
    bias: str
    conviction: float
    agree: int


@dataclass
class LTFAnalysis:
    """Analisa timeframe rendah (penentu ZONA ENTRY)."""
    timeframe: str
    wanted: str
    fallback_used: bool
    vibe: dict[str, Any]
    quant: dict[str, Any]
    risk: dict[str, Any]
    execution: dict[str, Any]
    zona_bawah: float
    zona_atas: float
    entry_limit: float
    sl: float
    tp1: float
    tp2: float
    lot: float
    atr: float


@dataclass
class UnifiedAnalysisResult:
    """Hasil akhir: SATU arah + SATU zona entry hasil gabungan 3 repo."""
    mode: str
    spot: float
    timestamp: str
    htf: HTFAnalysis
    ltf: LTFAnalysis
    direction: str
    zona_bawah: float
    zona_atas: float
    entry_limit: float
    sl: float
    tp1: float
    tp2: float
    lot: float
    confidence: float
    confluence_factors: list[str] = field(default_factory=list)
    report: str = ""
    # --- konteks pilihan pengguna (diisi oleh analyze_zone; kosong di mode lama)
    zone_tf: str = ""          # timeframe zona yang dipilih
    style: str = ""            # gaya trading (scalping/intraday/swing)
    style_label: str = ""      # label tampilan gaya (SCALPING/INTRADAY/SWING)
    bias_want: str = ""        # bias sesuai peta zona (sebelum fallback)
    valid_hours: float = 0.0   # masa berlaku order limit (jam)
    filter_tf: str = ""        # TF saringan tren (D1/W1; kosong = tanpa saringan)
    filter_bias: str = ""      # bias TF saringan
    wait_reason: str = ""      # alasan bila arah WAIT (syarat/saringan)


# ------------------------------------------------------------------ helper
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _series(rows: list[dict[str, Any]] | None, key: str) -> list[float]:
    """Ambil satu kolom OHLC sebagai list float (bar rusak dilewati)."""
    out: list[float] = []
    for row in rows or []:
        try:
            out.append(float(row[key]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _pick_series(
    hist: dict[str, list[dict[str, Any]]], order: tuple[str, ...]
) -> tuple[str, list[dict[str, Any]], bool]:
    """Ambil series pertama yang layak (>= 25 bar) dari urutan prioritas.

    Return: (timeframe terpakai, bar, apakah fallback dari pilihan utama).
    """
    for i, tf in enumerate(order):
        rows = hist.get(tf) or []
        if len(rows) >= 25:
            return tf, rows, i > 0
    for tf in order:  # darurat: apa pun yang ada walau < 25 bar
        rows = hist.get(tf) or []
        if rows:
            return tf, rows, True
    raise ValueError(f"Tidak ada data OHLC untuk timeframe {'/'.join(order)}")


def _atr(rows: list[dict[str, Any]], periode: int = 14) -> float:
    """ATR sederhana dari bar OHLC."""
    h, l, c = _series(rows, "high"), _series(rows, "low"), _series(rows, "close")
    if len(c) < 3:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(c)):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    trs = trs[-periode:]
    return round(sum(trs) / len(trs), 2) if trs else 0.0


# ------------------------------------------------------------------ engine
class UnifiedAnalyzer:
    """Gabungan Vibe-Trading + FinceptTerminal + AutoHedge -> satu zona entry."""

    # ------------------------------------------------------------- publik
    def analyze(
        self,
        mode: str,
        hist: dict[str, list[dict[str, Any]]],
        spot: Optional[float] = None,
    ) -> UnifiedAnalysisResult:
        """Analisa multi-timeframe: HTF -> bias, LTF -> SATU zona entry.

        mode : 'scalp' (bias M15 -> entry M1), 'intraday' (H1 -> M5),
               'swing' (D1 -> H1). Alias lama ('m5', 'intra', ...) tetap jalan.
        hist : peta timeframe -> list bar OHLC {'open','high','low','close'}
        spot : harga spot terkini (opsional; default = close terakhir HTF)
        """
        m = normalize_mode(mode)
        return self._run(
            m, hist, spot=spot,
            htf_order=HTF_PRIORITY[m], ltf_want=LTF_BY_MODE[m],
            ltf_order=LTF_PRIORITY[m], sl_pips=SL_PIPS_BY_MODE[m],
            tp1_pips=TP1_PIPS_BY_MODE[m], tp2_pips=TP2_PIPS_BY_MODE[m],
            zone_pips=ZONE_PIPS_BY_MODE[m], equity=EQUITY_BY_MODE[m],
        )

    def analyze_zone(
        self,
        zone_tf: str,
        style: str,
        hist: dict[str, list[dict[str, Any]]],
        spot: Optional[float] = None,
        *,
        bias_tf: Optional[str] = None,
    ) -> UnifiedAnalysisResult:
        """Analisa matriks zona -> bias untuk pilihan pengguna.

        zone_tf : timeframe ZONA (tempat entry dicari) — M1...H4.
        style   : gaya trading 'scalping' / 'intraday' / 'swing' (alias boleh).
        hist    : peta timeframe (label M1...W1) -> list bar OHLC.
        spot    : harga spot terkini.
        bias_tf : override bias (default dari ZONE_BIAS_BY_TF, mis. M1 -> M15).

        Profil risiko (lebar zona, SL/TP, berlaku order, syarat engine, dan
        saringan tren TF lebih tinggi) mengikuti STYLE_PRESETS[style] dan
        diskalakan ke ATR timeframe zona, sehingga zona cepat dapat SL ketat
        dan zona lambat dapat SL lebar — semua dengan R:R tetap 1:2 / 1:3.
        """
        zone = normalize_zone_tf(zone_tf)
        style_key = normalize_style(style)
        spec = STYLE_PRESETS[style_key]
        bias_want = normalize_tf(bias_tf) if bias_tf else ZONE_BIAS_BY_TF[zone]

        htf_order = bias_priority(zone, bias_want)
        ltf_order = zone_priority(zone)
        htf_tf, htf_rows, htf_fallback = _pick_series(hist, htf_order)
        ltf_tf, ltf_rows, ltf_fallback = _pick_series(hist, ltf_order)

        closes = _series(htf_rows, "close")
        spot_val = float(spot) if spot else (closes[-1] if closes else 0.0)
        if spot_val <= 0:
            raise ValueError("Harga spot tidak valid (<= 0)")

        # Lebar zona & SL/TP adaptif ATR timeframe zona (dengan lantai minimum
        # per TF agar pasar datar tetap menghasilkan level yang masuk akal, dan
        # batas atas 6x lantai agar volatilitas ekstrem tidak membuat zona liar).
        atr_zona = _atr(ltf_rows) or _atr(htf_rows)
        lantai = tf_min_zone_pips(zone)
        zona_pips = round(
            _clamp(spec["zone_atr"] * atr_zona, lantai, lantai * 6), 2)
        sl_pips = round(max(spec["sl_atr"] * atr_zona, zona_pips * 1.5), 2)
        tp1_pips = round(sl_pips * spec["rr1"], 2)
        tp2_pips = round(sl_pips * spec["rr2"], 2)

        htf = self._analyze_htf(htf_tf, htf_rows, spot_val)
        ctx: dict[str, Any] = {
            "zone_tf": zone, "style": style_key, "style_label": spec["label"],
            "bias_want": bias_want, "htf_fallback": htf_fallback,
            "valid_hours": float(spec["valid_hours"]),
            "min_agree": int(spec["min_agree"]),
            "filter_tf": "", "filter_bias": "", "filter_mode": spec["filter_mode"],
            "atr_zona": atr_zona,
        }
        filter_tf = spec.get("filter_tf")
        filter_rows = hist.get(filter_tf) if filter_tf else None
        if filter_tf and filter_rows and len(filter_rows) >= 25:
            saringan = self._analyze_htf(filter_tf, filter_rows, spot_val)
            ctx["filter_tf"] = filter_tf
            ctx["filter_bias"] = saringan.bias

        ltf = self._analyze_ltf(
            ltf_tf, zone, ltf_rows, ltf_fallback, spot_val, htf,
            sl_pips=sl_pips, tp1_pips=tp1_pips, tp2_pips=tp2_pips,
            zone_pips=zona_pips, equity=spec["equity"],
        )
        return self._synthesize(style_key, spot_val, htf, ltf, ctx=ctx)

    # ---------------------------------------------------------- internal
    def _run(
        self,
        mode: str,
        hist: dict[str, list[dict[str, Any]]],
        *,
        spot: Optional[float],
        htf_order: tuple[str, ...],
        ltf_want: str,
        ltf_order: tuple[str, ...],
        sl_pips: float,
        tp1_pips: float,
        tp2_pips: float,
        zone_pips: float,
        equity: float,
        ctx: Optional[dict[str, Any]] = None,
    ) -> UnifiedAnalysisResult:
        """Inti analisa: pilih seri HTF/LTF, jalankan engine, sintesis hasil."""
        htf_tf, htf_rows, _ = _pick_series(hist, htf_order)
        ltf_tf, ltf_rows, ltf_fallback = _pick_series(hist, ltf_order)

        closes = _series(htf_rows, "close")
        spot_val = float(spot) if spot else (closes[-1] if closes else 0.0)
        if spot_val <= 0:
            raise ValueError("Harga spot tidak valid (<= 0)")

        htf = self._analyze_htf(htf_tf, htf_rows, spot_val)
        ltf = self._analyze_ltf(
            ltf_tf, ltf_want, ltf_rows, ltf_fallback, spot_val, htf,
            sl_pips=sl_pips, tp1_pips=tp1_pips, tp2_pips=tp2_pips,
            zone_pips=zone_pips, equity=equity,
        )
        return self._synthesize(mode, spot_val, htf, ltf, ctx=ctx)

    # ---------------------------------------------------------- internal
    def _analyze_htf(
        self, tf: str, rows: list[dict[str, Any]], spot: float
    ) -> HTFAnalysis:
        """Bias HTF: vote Vibe-Trading + Fincept-DCF + AutoHedge-Director."""
        vibe = vibe_analyze_fn(rows, spot)
        vibe["spot_price"] = spot
        fincept = fincept_analyze_fn(rows, spot)
        fincept["spot_price"] = spot
        director = director_agent(spot, _series(rows, "close"), vibe, fincept)

        dcf_v = str(fincept.get("dcf", {}).get("verdict", "FAIR")).upper()
        votes = [
            str(vibe.get("bias", "NETRAL")),
            {"UNDERVALUED": "BUY", "OVERVALUED": "SELL"}.get(dcf_v, "NETRAL"),
            str(director.get("vibe_bias", "NETRAL")),
        ]
        bobot = {"BUY": 1, "SELL": -1}
        total = sum(bobot.get(v, 0) for v in votes)
        # Mayoritas menang: 2-1 sudah cukup (vote berbeda-beda antar repo wajar).
        bias = "BUY" if total >= 1 else ("SELL" if total <= -1 else "NETRAL")
        # `agree` = jumlah engine yang SEPAKAT dengan arah pemenang (2 BUY vs
        # 1 SELL tetap 2/3 sepakat), dipakai untuk syarat minimal engine dan
        # bobot confidence.
        sepakat = votes.count(bias) if bias in ("BUY", "SELL") else 0
        return HTFAnalysis(
            timeframe=tf, vibe=vibe, fincept=fincept, director=director,
            votes=votes, bias=bias,
            conviction=float(director.get("conviction", 50.0) or 50.0),
            agree=sepakat,
        )

    def _analyze_ltf(
        self, tf: str, wanted: str, rows: list[dict[str, Any]], fallback: bool,
        spot: float, htf: HTFAnalysis, *, sl_pips: float, tp1_pips: float,
        tp2_pips: float, zone_pips: float, equity: float,
    ) -> LTFAnalysis:
        """Zona entry LTF: Vibe (momentum) + AutoHedge (quant/risk/execution)."""
        vibe = vibe_analyze_fn(rows, spot)
        vibe["spot_price"] = spot
        quant = quant_agent(rows, spot)
        direction = htf.bias if htf.bias in ("BUY", "SELL") else "WAIT"
        risk = risk_agent(spot, direction, quant, equity=equity,
                          sl_pts=sl_pips * PIP_POINTS)

        atr = float(vibe.get("atr14") or 0.0) or _atr(rows)
        lebar = zone_pips * PIP_POINTS
        # Entry LIMIT menunggu pullback maks 0.5 ATR (tidak lebih lebar dari zona).
        pull = _clamp(atr * 0.5, lebar * 0.5, max(lebar, atr))
        if direction == "BUY":
            bawah = spot - pull
            atas = bawah + lebar
            entry = atas
            sl = entry - sl_pips * PIP_POINTS
            tp1 = entry + tp1_pips * PIP_POINTS
            tp2 = entry + tp2_pips * PIP_POINTS
        elif direction == "SELL":
            atas = spot + pull
            bawah = atas - lebar
            entry = bawah
            sl = entry + sl_pips * PIP_POINTS
            tp1 = entry - tp1_pips * PIP_POINTS
            tp2 = entry - tp2_pips * PIP_POINTS
        else:
            bawah = atas = entry = sl = tp1 = tp2 = spot

        execution = execution_agent(direction, spot, sl_pips * PIP_POINTS,
                                    tp1_pips * PIP_POINTS, tp2_pips * PIP_POINTS, risk)
        return LTFAnalysis(
            timeframe=tf, wanted=wanted, fallback_used=fallback, vibe=vibe,
            quant=quant, risk=risk, execution=execution,
            zona_bawah=round(bawah, 2), zona_atas=round(atas, 2),
            entry_limit=round(entry, 2), sl=round(sl, 2), tp1=round(tp1, 2),
            tp2=round(tp2, 2), lot=float(risk.get("lots", 0.0) or 0.0),
            atr=round(atr, 2),
        )

    def _synthesize(
        self, mode: str, spot: float, htf: HTFAnalysis, ltf: LTFAnalysis,
        ctx: Optional[dict[str, Any]] = None,
    ) -> UnifiedAnalysisResult:
        """Gabungkan HTF + LTF jadi SATU keputusan + SATU zona entry."""
        direction = htf.bias if htf.bias in ("BUY", "SELL") else "WAIT"
        wait_reason = ""
        # Saringan 1: jumlah engine minimal yang harus sepakat (per gaya).
        min_agree = int((ctx or {}).get("min_agree") or 0)
        if direction != "WAIT" and min_agree and htf.agree < min_agree:
            wait_reason = (
                f"hanya {htf.agree}/3 engine sepakat soal bias {htf.bias}; gaya "
                f"{(ctx or {}).get('style_label', '')} butuh ≥{min_agree}/3")
            direction = "WAIT"
        # Saringan 2: tren TF lebih tinggi (D1 untuk intraday, W1 untuk swing).
        # filter_mode="hard" (swing) wajib searah; "soft" hanya jadi pengurang.
        filter_bias = str((ctx or {}).get("filter_bias") or "")
        filter_tf = str((ctx or {}).get("filter_tf") or "")
        filter_opp = (
            direction in ("BUY", "SELL")
            and filter_bias in ("BUY", "SELL")
            and filter_bias != direction
        )
        if filter_opp and (ctx or {}).get("filter_mode") == "hard":
            wait_reason = (
                f"bias {direction} berlawanan dengan tren {filter_tf} "
                f"({filter_bias}); gaya {(ctx or {}).get('style_label', '')} "
                "tidak melawan tren timeframe lebih tinggi")
            direction = "WAIT"
        penalti = 15.0 if (filter_opp and direction != "WAIT") else 0.0
        if ctx is not None:
            ctx["filter_opp"] = bool(filter_opp and direction != "WAIT")

        strength = self._confluence(htf, ltf, direction)
        factors = self._factors(htf, ltf, direction, strength, ctx=ctx)
        conf = self._confidence(htf, ltf, strength, direction, penalti=penalti)
        konteks = self._ctx_fields(ctx, htf)
        if direction == "WAIT":
            return UnifiedAnalysisResult(
                mode=mode, spot=spot, timestamp=_now(), htf=htf, ltf=ltf,
                direction="WAIT", zona_bawah=0.0, zona_atas=0.0, entry_limit=0.0,
                sl=0.0, tp1=0.0, tp2=0.0, lot=0.0, confidence=conf,
                confluence_factors=factors, wait_reason=wait_reason,
                report=self._report_wait(
                    mode, spot, htf, ltf, factors, ctx=ctx, reason=wait_reason),
                **konteks,
            )
        return UnifiedAnalysisResult(
            mode=mode, spot=spot, timestamp=_now(), htf=htf, ltf=ltf,
            direction=direction, zona_bawah=ltf.zona_bawah,
            zona_atas=ltf.zona_atas, entry_limit=ltf.entry_limit, sl=ltf.sl,
            tp1=ltf.tp1, tp2=ltf.tp2, lot=ltf.lot, confidence=conf,
            confluence_factors=factors,
            report=self._report(mode, spot, htf, ltf, factors, conf, ctx=ctx),
            **konteks,
        )

    @staticmethod
    def _ctx_fields(ctx: Optional[dict[str, Any]], htf: HTFAnalysis) -> dict[str, Any]:
        """Field konteks pilihan pengguna yang dipasang ke hasil analisa."""
        if not ctx:
            return {}
        return {
            "zone_tf": str(ctx.get("zone_tf", "")),
            "style": str(ctx.get("style", "")),
            "style_label": str(ctx.get("style_label", "")),
            "bias_want": str(ctx.get("bias_want", "")),
            "valid_hours": float(ctx.get("valid_hours", 0.0) or 0.0),
            "filter_tf": str(ctx.get("filter_tf", "")),
            "filter_bias": str(ctx.get("filter_bias", "")),
        }

    @staticmethod
    def _ctx_lines(ctx: Optional[dict[str, Any]], htf: HTFAnalysis) -> list[str]:
        """Baris laporan: peta zona->bias, gaya, saringan, dan syarat entry."""
        if not ctx:
            return []
        zona = str(ctx.get("zone_tf", ""))
        bias_tf = htf.timeframe
        gaya = str(ctx.get("style_label") or ctx.get("style", "")).upper()
        rasio = zone_ratio(zona, bias_tf) if zona and bias_tf else 0.0
        baris = [
            f"🧩 *PETA TF*: zona *{zona}* ← bias *{bias_tf}*"
            + (f" ({rasio:g}×)" if rasio else "")
            + (f" | gaya *{gaya}*" if gaya else "")
            + (f" | order berlaku {float(ctx.get('valid_hours', 0) or 0):g} jam"
               if ctx.get("valid_hours") else "")
        ]
        if ctx.get("htf_fallback") and ctx.get("bias_want"):
            baris.append(
                f"⚠️ Bias {ctx['bias_want']} belum tersedia di sumber data → "
                f"dipakai {bias_tf}")
        fb = str(ctx.get("filter_tf") or "")
        fbia = str(ctx.get("filter_bias") or "")
        if fb and fbia:
            if fbia == "NETRAL":
                baris.append(f"🛡️ Saringan {fb}: NETRAL (belum memberi arah)")
            else:
                searah = fbia == (htf.bias if htf.bias in ("BUY", "SELL") else "")
                baris.append(
                    f"🛡️ Saringan {fb}: *{fbia}* "
                    + ("searah ✅" if searah else "berlawanan ⚠️"))
        minta = int(ctx.get("min_agree") or 0)
        if minta:
            baris.append(
                f"✅ Syarat: ≥{minta}/3 engine sepakat — terpenuhi "
                f"{htf.agree}/3")
        return baris

    def _confluence(
        self, htf: HTFAnalysis, ltf: LTFAnalysis, direction: str
    ) -> float:
        """Kekuatan konfluensi 0..1 (makin tinggi makin layak entry)."""
        if direction == "WAIT":
            return 0.0
        skor = float(ltf.vibe.get("score_0_100", 50) or 50) / 100 * 0.25
        if htf.bias == ltf.vibe.get("bias"):
            skor += 0.25
        skor += 0.20 * min(1.0, htf.agree / 3)
        vol = float(ltf.quant.get("vol_daily", 0) or 0)
        if 0.05 <= vol <= 1.5:
            skor += 0.15
        rsi = float(ltf.vibe.get("rsi14", 50) or 50)
        if (direction == "BUY" and rsi < 70) or (direction == "SELL" and rsi > 30):
            skor += 0.15
        return round(_clamp(skor, 0.0, 1.0), 2)

    def _confidence(
        self, htf: HTFAnalysis, ltf: LTFAnalysis, strength: float, direction: str,
        penalti: float = 0.0,
    ) -> float:
        """Derajat kepercayaan 0..100 (efektif, bukan janji profit)."""
        if direction == "WAIT":
            return max(15.0, round(30.0 - htf.agree * 5.0, 1))
        base = 30.0 + 15.0 * htf.agree
        base += 15.0 * ((float(ltf.vibe.get("score_0_100", 50) or 50) - 50) / 50)
        base += 20.0 * strength
        if ltf.fallback_used:
            base -= 5.0
        base -= penalti  # saringan TF lebih tinggi berlawanan (soft filter)
        return round(_clamp(base, 5.0, 95.0), 1)

    def _factors(
        self, htf: HTFAnalysis, ltf: LTFAnalysis, direction: str, strength: float,
        ctx: Optional[dict[str, Any]] = None,
    ) -> list[str]:
        """Daftar faktor pendukung (jejak audit dari 3 repo)."""
        dcf = htf.fincept.get("dcf", {})
        sv = htf.fincept.get("sharpe_var", {})
        macd_h = htf.vibe.get("macd", {}).get("hist")
        macd_l = ltf.vibe.get("macd", {}).get("hist")
        f = [
            f"Bias HTF {htf.timeframe}: {htf.bias} (vote {'|'.join(htf.votes)}, "
            f"{htf.agree}/3 engine sepakat)",
            f"Vibe-Trading HTF: {htf.vibe.get('bias')} "
            f"{htf.vibe.get('score_0_100')}/100 — EMA {htf.vibe.get('ema9')}/"
            f"{htf.vibe.get('ema21')}, RSI {htf.vibe.get('rsi14')}, MACD {macd_h:+}",
            f"Fincept DCF: {dcf.get('verdict', 'FAIR')} "
            f"(gap {float(dcf.get('gap_pct', 0) or 0):+.2f}%, fair "
            f"{dcf.get('fair_value')})",
            f"Fincept risiko: Sharpe {sv.get('sharpe_daily')} | "
            f"VaR95 {sv.get('var95_daily_pct')}%",
            f"AutoHedge Director: {htf.director.get('thesis')} "
            f"(conviction {htf.conviction}%)",
            f"Trigger LTF {ltf.timeframe}: Vibe {ltf.vibe.get('bias')} "
            f"{ltf.vibe.get('score_0_100')}/100 — RSI {ltf.vibe.get('rsi14')}, "
            f"MACD {macd_l:+}",
            f"AutoHedge Quant: vol {ltf.quant.get('vol_daily')}%/hari, "
            f"range14 {ltf.quant.get('range14')}, tren {ltf.quant.get('trend')}",
            f"AutoHedge Risk: {ltf.risk.get('note')}",
            f"Kekuatan konfluensi: {strength * 100:.0f}%",
        ]
        if ltf.fallback_used:
            f.append(
                f"Catatan: {ltf.wanted} belum tersedia di sumber data → entry "
                f"pakai {ltf.timeframe}"
            )
        if ctx and ctx.get("zone_tf"):
            zona = str(ctx["zone_tf"])
            f.insert(0, (
                f"Peta TF: zona {zona} dibentuk struktur {htf.timeframe} "
                f"({zone_ratio(zona, htf.timeframe):g}×) | gaya {ctx.get('style_label', '')}"))
            if ctx.get("atr_zona"):
                f.append(f"ATR zona {zona}: {float(ctx['atr_zona']):,.2f} poin "
                         "(dasar lebar zona & SL/TP)")
            if ctx.get("filter_tf") and ctx.get("filter_bias"):
                if ctx.get("filter_opp"):
                    f.append(
                        f"Saringan {ctx['filter_tf']}: {ctx['filter_bias']} berlawanan "
                        "→ confidence dikurangi 15 poin (perkecil risiko / tunggu)")
                else:
                    f.append(
                        f"Saringan {ctx['filter_tf']}: {ctx['filter_bias']} "
                        f"{'searah' if ctx['filter_bias'] == htf.bias else 'netral'}")
            if ctx.get("valid_hours"):
                f.append(
                    f"Masa berlaku order limit {float(ctx['valid_hours']):g} jam "
                    "(batalkan bila belum tersentuh)")
        return f

    # ------------------------------------------------------------ laporan
    def _report(
        self, mode: str, spot: float, htf: HTFAnalysis, ltf: LTFAnalysis,
        factors: list[str], conf: float,
        ctx: Optional[dict[str, Any]] = None,
    ) -> str:
        """Laporan Telegram (BUY/SELL) — teks siap kirim."""
        full = MODE_FULL.get(mode, mode.upper())
        icon = MODE_ICON.get(mode, "")
        # Jarak level diambil dari hasil analisa (bukan konstanta mode) supaya
        # tetap akurat saat gaya memakai SL/TP adaptif ATR.
        sl_pips = round(abs(ltf.entry_limit - ltf.sl) / PIP_POINTS, 2)
        tp1_pips = round(abs(ltf.tp1 - ltf.entry_limit) / PIP_POINTS, 2)
        tp2_pips = round(abs(ltf.tp2 - ltf.entry_limit) / PIP_POINTS, 2)
        zona_pips = round((ltf.zona_atas - ltf.zona_bawah) / PIP_POINTS, 2)
        rr_basis = sl_pips if sl_pips > 0 else 1e-9
        dcf = htf.fincept.get("dcf", {})
        sv = htf.fincept.get("sharpe_var", {})
        macd_h = htf.vibe.get("macd", {}).get("hist")
        macd_l = ltf.vibe.get("macd", {}).get("hist")
        jarak = ltf.entry_limit - spot
        if ltf.zona_bawah <= spot <= ltf.zona_atas:
            status = "harga SUDAH di dalam zona → siap pasang limit"
        else:
            status = f"tunggu harga masuk zona ({abs(jarak):.2f} poin dari spot)"
        risk_usd = sl_pips * ltf.lot * 100
        lines = [
            f"{icon} *ANALISIS TERPADU {full} XAUUSD*",
            f"💵 Spot: *{spot:,.2f}*",
            f"⏰ {_now()} WIB",
            f"📋 *{full}* — bias *{htf.timeframe}* → entry *{ltf.timeframe}*",
            *self._ctx_lines(ctx, htf),
            "",
            f"📊 *HTF {htf.timeframe} — BIAS & DASAR*",
            f"• Vibe-Trading: *{htf.vibe.get('bias')}* "
            f"{htf.vibe.get('score_0_100')}/100",
            f"  EMA {float(htf.vibe.get('ema9', 0)):,.2f}/"
            f"{float(htf.vibe.get('ema21', 0)):,.2f} | RSI {htf.vibe.get('rsi14')} | "
            f"MACD {float(macd_h or 0):+} | ATR {htf.vibe.get('atr14')}",
            f"• FinceptTerminal: DCF *{dcf.get('verdict', 'FAIR')}* "
            f"(gap {float(dcf.get('gap_pct', 0) or 0):+.2f}%) | "
            f"Sharpe {sv.get('sharpe_daily')} | VaR95 {sv.get('var95_daily_pct')}%",
            f"• AutoHedge Director: {htf.director.get('thesis')}",
            f"  Strategi: {htf.director.get('strategy')} | "
            f"Conviction {htf.conviction}%",
            f"• *Bias HTF: {htf.bias}* ({htf.agree}/3 engine sepakat)",
            "",
            f"⚡ *LTF {ltf.timeframe} — ZONA ENTRY & TRIGGER*",
            f"• Vibe-Trading: *{ltf.vibe.get('bias')}* "
            f"{ltf.vibe.get('score_0_100')}/100",
            f"  EMA {float(ltf.vibe.get('ema9', 0)):,.2f}/"
            f"{float(ltf.vibe.get('ema21', 0)):,.2f} | RSI {ltf.vibe.get('rsi14')} | "
            f"MACD {float(macd_l or 0):+} | ATR {ltf.atr}",
            f"• AutoHedge Quant: vol {ltf.quant.get('vol_daily')}%/hari | "
            f"range14 {ltf.quant.get('range14')} | tren {ltf.quant.get('trend')}",
            f"• AutoHedge Risk: {ltf.risk.get('note')}",
            "",
            f"🎯 *SATU ZONA ENTRY ({zona_pips:.0f} pip) — KONFLUENSI 3 REPO*",
            f"• Arah: *{htf.bias}*",
            f"• 📦 Zona entry: *{ltf.zona_bawah:,.2f} - {ltf.zona_atas:,.2f}*",
            f"• 🎯 Entry limit: *{ltf.entry_limit:,.2f}* ({jarak:+.2f} poin dari spot)",
            f"• 🛑 SL: *{ltf.sl:,.2f}* ({sl_pips:.0f} pip, risiko ${risk_usd:,.2f})",
            f"• 🥇 TP1: *{ltf.tp1:,.2f}* ({tp1_pips:.0f} pip, R:R 1:{tp1_pips / rr_basis:.1f})",
            f"• 🥈 TP2: *{ltf.tp2:,.2f}* ({tp2_pips:.0f} pip, R:R 1:{tp2_pips / rr_basis:.1f})",
            f"• 📦 Ukuran: *{ltf.lot} lot* XAUUSD (100 oz/lot)",
            f"• 🎚️ Confidence: *{conf}%*",
            f"• 📍 {status}",
            "",
            f"📋 *FAKTOR KONFLUENSI ({len(factors)}):*",
        ]
        lines += [f"  • {x}" for x in factors]
        lines += [
            "",
            f"💡 HTF {htf.timeframe} = arah, LTF {ltf.timeframe} = titik entry presisi.",
            "⚠️ Edukasi/analisis teknis, bukan saran keuangan. Pakai SL & risiko 1%.",
        ]
        return "\n".join(lines)

    def _report_wait(
        self, mode: str, spot: float, htf: HTFAnalysis, ltf: LTFAnalysis,
        factors: list[str], ctx: Optional[dict[str, Any]] = None,
        reason: str = "",
    ) -> str:
        """Laporan Telegram saat bias belum jelas (WAIT)."""
        full = MODE_FULL.get(mode, mode.upper())
        icon = MODE_ICON.get(mode, "")
        dcf = htf.fincept.get("dcf", {})
        macd_h = htf.vibe.get("macd", {}).get("hist")
        lines = [
            f"{icon} *ANALISIS TERPADU {full} XAUUSD — TUNGGU*",
            f"💵 Spot: *{spot:,.2f}*",
            f"⏰ {_now()} WIB",
            f"📋 *{full}* — bias *{htf.timeframe}* → entry *{ltf.timeframe}*",
        ]
        if reason:
            lines.append(f"⛔ *Alasan tunggu*: {reason}")
        lines += [
            *self._ctx_lines(ctx, htf),
            "",
            f"📊 *HTF {htf.timeframe} — DETEKSI BIAS*",
            f"• Vibe-Trading: {htf.vibe.get('bias')} "
            f"{htf.vibe.get('score_0_100')}/100 — EMA "
            f"{float(htf.vibe.get('ema9', 0)):,.2f}/"
            f"{float(htf.vibe.get('ema21', 0)):,.2f}, "
            f"RSI {htf.vibe.get('rsi14')}, MACD {float(macd_h or 0):+}",
            f"• FinceptTerminal: DCF {dcf.get('verdict', 'FAIR')} "
            f"(gap {float(dcf.get('gap_pct', 0) or 0):+.2f}%)",
            f"• AutoHedge Director: {htf.director.get('thesis')}",
            f"• *Bias HTF: NETRAL* (vote {'|'.join(htf.votes)})",
            "",
            f"📈 *LTF {ltf.timeframe} — BELUM KONFIRMASI*",
            f"• Vibe-Trading LTF: {ltf.vibe.get('bias')} "
            f"{ltf.vibe.get('score_0_100')}/100 — RSI {ltf.vibe.get('rsi14')}",
            f"• AutoHedge Quant: tren {ltf.quant.get('trend')} | "
            f"vol {ltf.quant.get('vol_daily')}%/hari",
            "",
            "📋 *FAKTOR:*",
        ]
        lines += [f"  • {x}" for x in factors]
        lines += [
            "",
            "💡 KESIMPULAN: engine belum sepakat soal arah. Tunggu candle "
            f"{ltf.timeframe} konfirmasi, atau coba kombinasi timeframe & gaya "
            "lain dari menu pilihan.",
            "⚠️ Edukasi/analisis teknis, bukan saran keuangan.",
        ]
        return "\n".join(lines)


def analyze(
    spot: float,
    hist: dict[str, list[dict[str, Any]]],
    mode: str = "scalp",
    preferensi_entry: Optional[str] = None,
) -> UnifiedAnalysisResult:
    """API ringkas: analyze(spot, hist, 'scalp') -> UnifiedAnalysisResult.

    preferensi_entry='market' memaksa entry di harga spot (tanpa pullback).
    """
    hasil = UnifiedAnalyzer().analyze(mode, hist, spot=spot)
    if preferensi_entry == "market" and hasil.direction in ("BUY", "SELL"):
        lebar = ZONE_PIPS_BY_MODE[hasil.mode]
        hasil.entry_limit = round(float(spot), 2)
        if hasil.direction == "BUY":
            hasil.zona_bawah = round(spot, 2)
            hasil.zona_atas = round(spot + lebar, 2)
        else:
            hasil.zona_bawah = round(spot - lebar, 2)
            hasil.zona_atas = round(spot, 2)
    return hasil





