"""
MCP server: get_gold_price
==========================
Mengekspos tool MCP `get_gold_price` untuk mengambil harga spot XAUUSD (gold)
secara real-time dari API publik gold-api.com.

Menjalankan sebagai server MCP stdio:
    python gold_mcp_server.py

Bisa juga diluncurkan dari bridge Telegram (bot_telegram.py) sebagai subprocess
stdio melalui MCP client.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from mcp.server.mcpserver import MCPServer

# ---------------------------------------------------------------- konfigurasi
GOLD_API_URL = "https://api.gold-api.com/price/XAU"
# Histori harian diambil dari Yahoo Finance (COMEX gold futures GC=F) sebagai
# proxy struktur harga; nilainya diselaraskan ke harga spot via offset.
YAHOO_HISTORY_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1d&range=1mo",
    "https://query2.finance.yahoo.com/v8/finance/chart/GC=F?interval=1d&range=1mo",
)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
TIMEOUT_SECONDS = 12
# Zona waktu untuk label jam bar intraday (WIB = UTC+7).
WIB = timezone(timedelta(hours=7))
# Retry untuk error jaringan transien (mis. DNS sempat gagal resolve).
HTTP_RETRIES = 3
HTTP_BACKOFF_SECONDS = 1.5
# Cache TTL (detik) untuk mengurangi panggilan HTTP & mempercepat respons.
# Set 0 untuk menonaktifkan cache.
PRICE_CACHE_TTL = float(os.getenv("GOLD_PRICE_CACHE_TTL", "30"))
HISTORY_CACHE_TTL = float(os.getenv("GOLD_HISTORY_CACHE_TTL", "600"))
INTRADAY_CACHE_TTL = float(os.getenv("GOLD_INTRADAY_CACHE_TTL", "180"))
M5_CACHE_TTL = float(os.getenv("GOLD_M5_CACHE_TTL", "120"))

# ------------------------------------------------- mode scalping momentum
# Histori intraday dipakai untuk membaca momentum jangka pendek, per timeframe:
# M15 sebagai zona/bias, M5 sebagai trigger entry.
YAHOO_INTRADAY_URLS: dict[str, tuple[str, ...]] = {
    "15m": (
        "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=15m&range=5d",
        "https://query2.finance.yahoo.com/v8/finance/chart/GC=F?interval=15m&range=5d",
    ),
    "5m": (
        "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=5m&range=5d",
        "https://query2.finance.yahoo.com/v8/finance/chart/GC=F?interval=5m&range=5d",
    ),
}
# TTL cache per timeframe (detik): M15 lebih lambat berubah, M5 lebih cepat.
INTRADAY_CACHE_TTLS = {"15m": INTRADAY_CACHE_TTL, "5m": M5_CACHE_TTL}
# Nilai 1 pip untuk XAUUSD. Default 1 pip = 1.00 poin harga ($1 per troy oz).
# Broker dengan kuotasi 2 desimal biasanya memakai 0.1 atau 0.01 — sesuaikan
# lewat env GOLD_PIP_VALUE agar 50 pip = 50 * GOLD_PIP_VALUE poin.
PIP_VALUE = float(os.getenv("GOLD_PIP_VALUE", "1.0"))
# Target tetap: SL 50 pip, TP1 100 pip (R:R 1:2), TP2 150 pip (R:R 1:3).
SCALP_SL_PIPS = float(os.getenv("GOLD_SCALP_SL_PIPS", "50"))
SCALP_TP1_PIPS = float(os.getenv("GOLD_SCALP_TP1_PIPS", "100"))
SCALP_TP2_PIPS = float(os.getenv("GOLD_SCALP_TP2_PIPS", "150"))
# Ambang skor confluence (0-6) agar sinyal dianggap high-probability.
# Naikkan (mis. 5) untuk lebih ketat/selektif, turunkan (mis. 3) untuk lebih sering.
SCALP_MIN_SCORE = float(os.getenv("GOLD_SCALP_MIN_SCORE", "4"))
# Masa berlaku order limit (jam) sebelum setup dianggap kedaluwarsa.
SCALP_VALID_HOURS = float(os.getenv("GOLD_SCALP_VALID_HOURS", "4"))

# ------------------------- mode scalping momentum M5 (trigger) + zona M15
# Trigger dibaca dari M5, sedangkan zona limit order & filter arah dari M15.
# SL ketat 5 pip (= 5.00 poin bila GOLD_PIP_VALUE=1.0), TP1 10 pip (R:R 1:2),
# TP2 15 pip (R:R 1:3). Mode cepat → TP kecil, cocok untuk scalping M5.
SCALP_M5_SL_PIPS = float(os.getenv("GOLD_SCALP_M5_SL_PIPS", "5"))
SCALP_M5_TP1_PIPS = float(os.getenv("GOLD_SCALP_M5_TP1_PIPS", "10"))
SCALP_M5_TP2_PIPS = float(os.getenv("GOLD_SCALP_M5_TP2_PIPS", "15"))
SCALP_M5_MIN_SCORE = float(os.getenv("GOLD_SCALP_M5_MIN_SCORE", "4"))
# Order M5 cepat basi: default 1,5 jam (bukan 4 jam seperti M15).
SCALP_M5_VALID_HOURS = float(os.getenv("GOLD_SCALP_M5_VALID_HOURS", "1.5"))

# ----------------- mode analisa SMC / ICT-style (`/smc`): bias → skenario →
# pindai order block LTF. Bobot per komponen bisa di-tune lewat env ini.
SMC_BIAS_W_HTF = float(os.getenv("GOLD_SMC_BIAS_W_HTF", "2"))
SMC_BIAS_W_MOM = float(os.getenv("GOLD_SMC_BIAS_W_MOM", "2"))
SMC_BIAS_W_STRUCT = float(os.getenv("GOLD_SMC_BIAS_W_STRUCT", "3"))
SMC_BIAS_W_DISP = float(os.getenv("GOLD_SMC_BIAS_W_DISP", "1"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("gold_mcp")

# cache sederhana in-memory: {key: (kedaluwarsa_pada_monotonic, nilai)}
_cache: dict[str, tuple[float, object]] = {}


def _cache_get(key: str) -> object | None:
    """Ambil nilai cache bila masih berlaku, else None."""
    item = _cache.get(key)
    if item is None:
        return None
    expires_at, value = item
    if time.monotonic() >= expires_at:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: object, ttl: float) -> object:
    """Simpan nilai ke cache (ttl <= 0 berarti cache dilewati)."""
    if ttl > 0:
        _cache[key] = (time.monotonic() + ttl, value)
    return value

mcp = MCPServer("gold-server")


def _http_get(url: str, headers: dict[str, str] | None = None) -> requests.Response:
    """GET dengan retry berjenjang untuk error jaringan transien (DNS/timeout)."""
    last_exc: Exception | None = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:  # noqa: PERF203
            last_exc = exc
            logger.warning(
                "GET %s gagal (percobaan %d/%d): %s", url, attempt, HTTP_RETRIES, exc
            )
            if attempt < HTTP_RETRIES:
                time.sleep(HTTP_BACKOFF_SECONDS * attempt)
    raise RuntimeError(
        f"Gagal menghubungi {url} setelah {HTTP_RETRIES} percobaan"
    ) from last_exc


def fetch_gold_price_raw() -> dict[str, Any]:
    """Ambil data mentah dari gold-api.com (dengan cache). Raise pada error."""
    cached = _cache_get("price")
    if cached is not None:
        logger.info("Cache harga dipakai (TTL %ss)", PRICE_CACHE_TTL)
        return dict(cached)  # type: ignore[arg-type]
    data = _http_get(GOLD_API_URL).json()
    _cache_set("price", data, PRICE_CACHE_TTL)
    return data


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Rapikan nilai agar mudah dibaca."""
    price = float(data["price"])
    ts = data.get("updatedAt", "")
    return {
        "symbol": data.get("symbol", "XAU"),
        "currency": data.get("currency", "USD"),
        "price": round(price, 2),
        "price_usd_per_oz": round(price, 2),
        "updated_at": ts,
        "readable_time": data.get("updatedAtReadable", ""),
    }


@mcp.tool()
def get_gold_price() -> dict[str, Any]:
    """
    Mengambil harga spot emas XAUUSD (Gold per troy ounce) dalam USD saat ini.
    Mengembalikan dict berisi symbol, currency, price, dan waktu update.
    """
    data = fetch_gold_price_raw()
    return _clean(data)


@mcp.tool()
def get_gold_price_html() -> str:
    """
    Mengambil harga XAUUSD dan mengembalikannya sebagai teks siap-format
    untuk pesan Telegram/chat (dengan emoji dan satuan).
    """
    c = _clean(fetch_gold_price_raw())
    return (
        f"🥇 Gold ({c['symbol']}) — {c['currency']}\n"
        f"💰 Harga: ${c['price']:,.2f} / troy oz\n"
        f"🕒 Update: {c['readable_time']} ({c['updated_at']})"
    )


def fetch_daily_history() -> list[dict[str, Any]]:
    """Ambil OHLC harian GC=F dari Yahoo (cache → query1 → query2)."""
    cached = _cache_get("history")
    if cached is not None:
        logger.info("Cache histori dipakai (TTL %ss)", HISTORY_CACHE_TTL)
        return [dict(row) for row in cached]  # type: ignore[union-attr]
    last_exc: Exception | None = None
    for url in YAHOO_HISTORY_URLS:
        try:
            rows = _parse_yahoo_chart(_http_get(url, headers={"User-Agent": USER_AGENT}).json())
        except Exception as exc:  # noqa: BLE001 - coba sumber berikutnya
            last_exc = exc
            logger.warning("Sumber histori %s gagal: %s", url, exc)
            continue
        if rows:
            _cache_set("history", rows, HISTORY_CACHE_TTL)
            return rows
        last_exc = RuntimeError(f"Data kosong dari {url}")
    raise RuntimeError("Semua sumber histori Yahoo Finance gagal") from last_exc


def fetch_intraday_history(interval: str = "15m") -> list[dict[str, Any]]:
    """Ambil OHLC intraday GC=F (5 hari) dari Yahoo (cache → query1 → query2).

    interval: "15m" (default) untuk zona/bias, atau "5m" untuk trigger scalping.
    """
    urls = YAHOO_INTRADAY_URLS.get(interval)
    if urls is None:
        raise ValueError(f"Interval intraday tidak didukung: {interval}")
    ttl = INTRADAY_CACHE_TTLS[interval]
    cache_key = f"intraday_{interval}"
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("Cache intraday %s dipakai (TTL %ss)", interval, ttl)
        return [dict(row) for row in cached]  # type: ignore[union-attr]
    last_exc: Exception | None = None
    for url in urls:
        try:
            rows = _parse_yahoo_chart(
                _http_get(url, headers={"User-Agent": USER_AGENT}).json(), with_time=True
            )
        except Exception as exc:  # noqa: BLE001 - coba sumber berikutnya
            last_exc = exc
            logger.warning("Sumber intraday %s (%s) gagal: %s", interval, url, exc)
            continue
        if rows:
            _cache_set(cache_key, rows, ttl)
            return rows
        last_exc = RuntimeError(f"Data intraday {interval} kosong dari {url}")
    raise RuntimeError(f"Semua sumber intraday {interval} Yahoo Finance gagal") from last_exc


def _parse_yahoo_chart(
    payload: dict[str, Any], with_time: bool = False
) -> list[dict[str, Any]]:
    """Ubah JSON chart Yahoo Finance menjadi list baris OHLC.

    with_time=True menambahkan label jam bar (WIB) — dipakai data intraday M15.
    """
    result = payload["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    rows: list[dict[str, Any]] = []
    for i in range(len(ts)):
        close = q["close"][i]
        if close is None:
            continue  # lewati bar kosong
        stamp = datetime.fromtimestamp(ts[i], tz=timezone.utc)
        row: dict[str, Any] = {
            "date": stamp.strftime("%Y-%m-%d"),
            "open": q["open"][i],
            "high": q["high"][i],
            "low": q["low"][i],
            "close": close,
        }
        if with_time:
            row["time"] = stamp.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")
        rows.append(row)
    return rows


def _atr(highs: list[float], lows: list[float], n: int = 14) -> float:
    """ATR sederhana: rata-rata range (high-low) n bar terakhir (0.0 jika kosong)."""
    ranges = [h - l for h, l in zip(highs[-n:], lows[-n:])]
    if not ranges:
        return 0.0
    return round(sum(ranges) / len(ranges), 2)


def _swing_points(
    highs: list[float], lows: list[float], lookback: int = 3
) -> tuple[list[int], list[int]]:
    """Indeks swing high / swing low memakai fraktal sederhana.

    Bar i disebut swing high bila high[i] adalah yang tertinggi dalam jendela
    ±lookback; begitu pula swing low untuk low[i]. Dua bar terakhir dilewati
    karena fraktal butuh bar konfirmasi di kanan.
    """
    swing_high: list[int] = []
    swing_low: list[int] = []
    n = len(highs)
    for i in range(lookback, n - lookback):
        window_h = highs[i - lookback : i + lookback + 1]
        if highs[i] >= max(window_h) and highs[i] > highs[i - 1] and highs[i] >= highs[i + 1]:
            swing_high.append(i)
        window_l = lows[i - lookback : i + lookback + 1]
        if lows[i] <= min(window_l) and lows[i] < lows[i - 1] and lows[i] <= lows[i + 1]:
            swing_low.append(i)
    return swing_high, swing_low


def _displacement(
    opens: list[float],
    closes: list[float],
    highs: list[float],
    lows: list[float],
    n: int = 5,
) -> dict[str, Any]:
    """Ukur displacement: body vs range pada n bar terakhir.

    Mengembalikan rasio body/range rata-rata, arah bar terakhir, serta flag
    impulse bila body melebihi 60% range dan arahnya konsisten.
    """
    tail_c = closes[-n:]
    tail_o = opens[-n:]
    rng = list(zip(highs[-n:], lows[-n:]))
    bodies = [abs(c - o) for c, o in zip(tail_c, tail_o)]
    ranges = [max(h - l, 1e-9) for h, l in rng]
    ratios = [b / r for b, r in zip(bodies, ranges)]
    ratio = round(sum(ratios) / len(ratios), 3) if ratios else 0.0
    arah = "UP" if tail_c[-1] > tail_c[0] else ("DOWN" if tail_c[-1] < tail_c[0] else "FLAT")
    return {
        "ratio": ratio,
        "arah": arah,
        "impulse": ratio > 0.6 and arah != "FLAT",
        "body_avg": round(sum(bodies) / len(bodies), 2) if bodies else 0.0,
    }


def _scan_order_blocks(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    atr: float,
    limit: int = 6,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pindai kandidat order block bullish/bearish dari data OHLC.

    Aturan (heuristik, LTF-friendly):
    * bullish OB = bar bearish terakhir SEBELUM rangkaian bar bullish yang
      menembus high swing / membuat displacement ke atas;
    * bearish OB = kebalikannya.
    * OB dinyatakan *fresh* bila harga belum pernah kembali menembus zona
      (belum *mitigated*); bila sudah tersentuh, status = `mitigated`.
    * Ukuran body bar OB dibatasi ≤ 3×ATR agar bukan bar liar.
    """
    bullish: list[dict[str, Any]] = []
    bearish: list[dict[str, Any]] = []
    ref_atr = max(atr, 1e-9)
    n = len(closes)
    for i in range(3, n - 1):
        body = abs(closes[i] - opens[i])
        if body > 3 * ref_atr:
            continue
        # --- kandidat bullish OB: bar bearish (close<open) lalu 1-3 bar
        # berikutnya menembus high[i] dengan close di atasnya.
        if closes[i] < opens[i]:
            for j in range(i + 1, min(i + 4, n)):
                if closes[j] > highs[i]:
                    mitigated = any(
                        lows[k] <= highs[i] for k in range(j + 1, n)
                    )
                    bullish.append(
                        {
                            "bawah": round(lows[i], 2),
                            "atas": round(highs[i], 2),
                            "bar": i,
                            "status": "mitigated" if mitigated else "fresh",
                        }
                    )
                    break
        # --- kandidat bearish OB: bar bullish lalu ditembus ke bawah.
        if closes[i] > opens[i]:
            for j in range(i + 1, min(i + 4, n)):
                if closes[j] < lows[i]:
                    mitigated = any(
                        highs[k] >= lows[i] for k in range(j + 1, n)
                    )
                    bearish.append(
                        {
                            "bawah": round(lows[i], 2),
                            "atas": round(highs[i], 2),
                            "bar": i,
                            "status": "mitigated" if mitigated else "fresh",
                        }
                    )
                    break
    return bullish[-limit:], bearish[-limit:]


def _nearest(items: list[dict[str, Any]], price: float) -> dict[str, Any] | None:
    """Pilih zona (order block / OB MTF) yang paling dekat dengan harga."""
    if not items:
        return None
    return min(
        items,
        key=lambda z: 0.0 if z["bawah"] <= price <= z["atas"] else min(
            abs(price - z["bawah"]), abs(price - z["atas"])
        ),
    )


def _sma(values: list[float], n: int) -> float | None:
    """Simple Moving Average dari n nilai terakhir (None jika data kurang)."""
    if len(values) < n:
        return None
    return round(sum(values[-n:]) / n, 2)


def _ema(values: list[float], n: int) -> float | None:
    """Exponential Moving Average (None jika data kurang)."""
    if len(values) < n:
        return None
    k = 2 / (n + 1)
    ema = sum(values[:n]) / n
    for value in values[n:]:
        ema = value * k + ema * (1 - k)
    return round(ema, 2)


def _rsi(values: list[float], n: int = 14) -> float | None:
    """RSI Wilder (None jika data kurang)."""
    if len(values) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / n, losses / n
    for i in range(n + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(change, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-change, 0.0)) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


@mcp.tool()
def get_gold_analysis() -> dict[str, Any]:
    """
    Analisa price action sederhana XAUUSD dan menghasilkan signal trading:
    arah, entry, stop loss, TP1, TP2, risk–reward, dan alasan singkat.

    Metode: tren dari SMA5 vs SMA20 + posisi harga, level memakai swing high/low
    terakhir dan ATR (rata-rata range 14 hari). Bersifat edukasi, bukan nasihat.
    """
    spot = float(_clean(fetch_gold_price_raw())["price"])
    rows = fetch_daily_history()
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]

    ranges = [h - l for h, l in zip(highs, lows)]
    recent_ranges = ranges[-14:] or ranges
    atr = round(sum(recent_ranges) / len(recent_ranges), 2)

    sma5 = _sma(closes, 5)
    sma20 = _sma(closes, 20)

    # Selaraskan level futures (GC=F) ke harga spot via offset.
    offset = round(spot - closes[-1], 2)

    def adj(x: float) -> float:
        return round(x + offset, 2)

    # ---- penentuan tren
    if sma5 is not None and sma20 is not None and sma5 < sma20 and spot < sma20:
        trend = "DOWN"
    elif sma5 is not None and sma20 is not None and sma5 > sma20 and spot > sma20:
        trend = "UP"
    else:
        trend = "SIDEWAYS"

    # ---- hitung level
    if trend == "UP":
        direction = "BUY"
        entry = adj(min(lows[-3:]))
        sl = round(entry - atr, 2)
    elif trend == "DOWN":
        direction = "SELL"
        entry = adj(max(highs[-3:]))
        sl = round(entry + atr, 2)
    else:
        # Sideways: goreng range — jual di atas, beli di bawah (default jual).
        direction = "SELL"
        entry = adj(max(highs[-3:]))
        sl = round(entry + atr, 2)

    risk = round(abs(entry - sl), 2)
    if direction == "SELL":
        tp1 = round(entry - risk * 1.5, 2)
        tp2 = round(entry - risk * 2.5, 2)
    else:
        tp1 = round(entry + risk * 1.5, 2)
        tp2 = round(entry + risk * 2.5, 2)

    reason = (
        f"Tren {trend}: SMA5={sma5}, SMA20={sma20}, harga spot={spot}. "
        f"Swing high 3-hari={adj(max(highs[-3:]))}, swing low 3-hari={adj(min(lows[-3:]))}, "
        f"ATR14={atr}. Level {'jual di resistance saat koreksi' if direction == 'SELL' else 'beli di support saat pullback'}."
    )

    return {
        "symbol": "XAUUSD",
        "spot_price": spot,
        "trend": trend,
        "direction": direction,
        "entry": entry,
        "stop_loss": sl,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "risk_points": risk,
        "risk_reward_tp1": "1:1.50",
        "risk_reward_tp2": "1:2.50",
        "atr14": atr,
        "sma5": sma5,
        "sma20": sma20,
        "reason": reason,
        "disclaimer": "Edukasi/analisis teknis sederhana, bukan saran keuangan.",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@mcp.tool()
def get_gold_analysis_html() -> str:
    """Analisa XAUUSD dalam format teks siap-kirim ke Telegram."""
    a = get_gold_analysis()
    return (
        "📊 *Signal XAUUSD (price action)*\n"
        f"💵 Spot: ${a['spot_price']:,.2f}  |  Tren: {a['trend']}\n\n"
        f"➡️ Arah: *{a['direction']}*\n"
        f"🎯 Entry: {a['entry']:,.2f}\n"
        f"🛑 Stop Loss: {a['stop_loss']:,.2f}\n"
        f"🥇 TP1: {a['take_profit_1']:,.2f} (R:R {a['risk_reward_tp1']})\n"
        f"🥈 TP2: {a['take_profit_2']:,.2f} (R:R {a['risk_reward_tp2']})\n"
        f"📐 Risk: {a['risk_points']:,.2f} pt  |  ATR14: {a['atr14']}\n\n"
        f"🧠 Alasan: {a['reason']}\n\n"
        f"⚠️ {a['disclaimer']}"
    )


@mcp.tool()
def get_gold_scalping_signal(
    sl_pips: float | None = None,
    tp1_pips: float | None = None,
    tp2_pips: float | None = None,
) -> dict[str, Any]:
    """
    Sinyal SCALPING MOMENTUM XAUUSD (M15) dengan filter high-probability.

    * SL & TP tetap dalam pip: default SL 50 pip, TP1 100 pip (R:R 1:2),
      TP2 150 pip (R:R 1:3) — bisa dioverride lewat argumen atau env GOLD_SCALP_*.
      1 pip = GOLD_PIP_VALUE poin harga (default 1.0, jadi 50 pip = 50 poin).
    * Entry disarankan sebagai LIMIT ORDER di zona momentum (EMA9/EMA21 ± ATR15).
    * Skor confluence 0-6 (tren harian, momentum M15, RSI14, struktur swing,
      posisi harga di zona). Volatilitas menjadi GATE, bukan skor: ATR15 harus
      0,05%-0,60% harga. Sinyal hanya dikeluarkan bila skor >= GOLD_SCALP_MIN_SCORE
      (default 4) dan gate volatilitas lolos; selain itu arah = TUNGGU.

    Edukasi/analisis teknis sederhana, bukan saran keuangan.
    """
    sl_pips = float(sl_pips) if sl_pips else SCALP_SL_PIPS
    tp1_pips = float(tp1_pips) if tp1_pips else SCALP_TP1_PIPS
    tp2_pips = float(tp2_pips) if tp2_pips else SCALP_TP2_PIPS

    spot = float(_clean(fetch_gold_price_raw())["price"])
    rows = fetch_intraday_history()
    if len(rows) < 30:
        raise RuntimeError(
            "Data intraday M15 dari Yahoo Finance tidak cukup (butuh minimal 30 bar)"
        )

    # Selaraskan level futures (GC=F) ke harga spot via offset.
    offset = round(spot - rows[-1]["close"], 2)
    closes = [round(r["close"] + offset, 2) for r in rows]
    highs = [round(r["high"] + offset, 2) for r in rows]
    lows = [round(r["low"] + offset, 2) for r in rows]
    last_bar = rows[-1].get("time") or rows[-1].get("date", "")

    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    rsi14 = _rsi(closes, 14)
    atr15 = _atr(highs, lows, 14)
    momentum_3bar = round(closes[-1] - closes[-4], 2) if len(closes) >= 4 else 0.0

    dcloses = [r["close"] for r in fetch_daily_history()]
    sma5 = _sma(dcloses, 5)
    sma20 = _sma(dcloses, 20)
    if sma5 is not None and sma20 is not None and sma5 < sma20:
        daily_trend = "DOWN"
    elif sma5 is not None and sma20 is not None and sma5 > sma20:
        daily_trend = "UP"
    else:
        daily_trend = "SIDEWAYS"

    ref = ema21 if ema21 is not None else closes[-1]
    band = max(atr15, 0.1)
    zona_bawah = round(ref - band * 0.5, 2)
    zona_atas = round(ref + band * 0.5, 2)

    def confluence(direction: str) -> tuple[int, list[str]]:
        """Skor confluence 0-7 + catatan untuk satu arah."""
        up = direction == "BUY"
        skor = 0
        catatan: list[str] = []
        if ema9 is not None and ema21 is not None and ((ema9 > ema21) if up else (ema9 < ema21)):
            skor += 1
            catatan.append(
                f"Momentum M15 {'naik' if up else 'turun'} (EMA9 {ema9} vs EMA21 {ema21})"
            )
        if (closes[-1] >= ref) if up else (closes[-1] <= ref):
            skor += 1
            catatan.append(
                f"Harga di sisi {'atas' if up else 'bawah'} zona EMA21 (close {closes[-1]})"
            )
        if rsi14 is not None and ((45 <= rsi14 <= 70) if up else (30 <= rsi14 <= 55)):
            skor += 1
            catatan.append(f"RSI14 M15 {rsi14} mendukung {direction} (tidak ekstrem)")
        if daily_trend == ("UP" if up else "DOWN"):
            skor += 1
            catatan.append(
                f"Tren harian {daily_trend} searah {direction} (SMA5 {sma5} vs SMA20 {sma20})"
            )
        if len(lows) > 21 and len(highs) > 21:
            if up and lows[-1] > min(lows[-21:-1]):
                skor += 1
                catatan.append(f"Higher low M15: {lows[-1]} > {min(lows[-21:-1])}")
            elif not up and highs[-1] < max(highs[-21:-1]):
                skor += 1
                catatan.append(f"Lower high M15: {highs[-1]} < {max(highs[-21:-1])}")
        if abs(closes[-1] - ref) <= band * 0.75:
            skor += 1
            catatan.append(
                f"Harga di zona momentum {zona_bawah}-{zona_atas} → cocok untuk limit order"
            )
        return skor, catatan

    buy_score, buy_notes = confluence("BUY")
    sell_score, sell_notes = confluence("SELL")
    if buy_score > sell_score:
        bias, skor, catatan = "BUY", buy_score, buy_notes
    else:
        bias, skor, catatan = "SELL", sell_score, sell_notes
        if sell_score == buy_score:
            catatan = [*catatan, "Skor BUY & SELL imbang → bias default SELL (mean-reversion)"]

    # Gate volatilitas: ATR15 harus wajar (0,05%-0,6% harga) — bukan poin skor,
    # tapi syarat mutlak agar sinyal tidak diambil saat market terlalu mati/liar.
    vol_pct = round(atr15 / spot * 100, 2) if spot else 0.0
    vol_ok = bool(atr15) and 0.0005 * spot <= atr15 <= 0.006 * spot

    max_score = 6
    arah = bias if skor >= SCALP_MIN_SCORE else "TUNGGU"
    if not vol_ok:
        arah = "TUNGGU"

    # ---- level: SL/TP tetap dalam pip, entry sebagai limit di zona momentum
    pip = PIP_VALUE
    sl_dist = round(sl_pips * pip, 2)
    tp1_dist = round(tp1_pips * pip, 2)
    tp2_dist = round(tp2_pips * pip, 2)
    pullback = round(band * 0.2, 2)
    if bias == "BUY":
        entry_limit = round(min(ref, spot) - pullback, 2)
        sl = round(entry_limit - sl_dist, 2)
        tp1 = round(entry_limit + tp1_dist, 2)
        tp2 = round(entry_limit + tp2_dist, 2)
    else:
        entry_limit = round(max(ref, spot) + pullback, 2)
        sl = round(entry_limit + sl_dist, 2)
        tp1 = round(entry_limit - tp1_dist, 2)
        tp2 = round(entry_limit - tp2_dist, 2)

    if skor >= 5:
        label = "TINGGI"
    elif skor >= SCALP_MIN_SCORE:
        label = "SEDANG-TINGGI"
    elif skor >= 3:
        label = "RENDAH (tunggu konfirmasi)"
    else:
        label = "SANGAT RENDAH (hindari entry)"

    if arah == "TUNGGU":
        if not vol_ok:
            alasan = (
                f"Volatilitas M15 tidak wajar (ATR15 {atr15} = {vol_pct}% harga, syarat "
                f"0,05%-0,60%) → entry dibatalkan meski skor {skor}/{max_score} "
                f"(bias {bias}). Tunggu volatilitas normal."
            )
        else:
            alasan = (
                f"Skor confluence {skor}/{max_score} < ambang {SCALP_MIN_SCORE:.0f} → belum layak entry. "
                f"Bias sementara {bias}, momentum 3-bar M15 {momentum_3bar}, RSI14 {rsi14}, "
                f"tren harian {daily_trend}. Tunggu harga masuk zona "
                f"{zona_bawah:,.2f}-{zona_atas:,.2f} dan skor naik ke minimal {SCALP_MIN_SCORE:.0f}."
            )
    else:
        alasan = (
            f"Momentum M15 {'naik' if bias == 'BUY' else 'turun'} (EMA9 {ema9} vs EMA21 {ema21}, "
            f"RSI14 {rsi14}, momentum 3-bar {momentum_3bar}) dengan tren harian {daily_trend}. "
            f"Skor confluence {skor}/{max_score} ≥ ambang {SCALP_MIN_SCORE:.0f} → setup layak; "
            f"pasang {bias} LIMIT di {entry_limit:,.2f} (zona {zona_bawah:,.2f}-{zona_atas:,.2f}), "
            f"SL {sl_dist:.2f} poin, TP1 {tp1_dist:.2f} poin."
        )

    valid_until = (datetime.now(timezone.utc) + timedelta(hours=SCALP_VALID_HOURS)).astimezone(WIB)

    return {
        "symbol": "XAUUSD",
        "mode": "scalping-momentum (M15)",
        "spot_price": spot,
        "direction": arah,
        "bias": bias,
        "order_type": "LIMIT" if arah != "TUNGGU" else "WAIT",
        "entry_limit": entry_limit,
        "entry_market": spot,
        "stop_loss": sl,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "sl_pips": sl_pips,
        "tp1_pips": tp1_pips,
        "tp2_pips": tp2_pips,
        "pip_value": pip,
        "risk_points": sl_dist,
        "reward_points_tp1": tp1_dist,
        "reward_points_tp2": tp2_dist,
        "risk_reward_tp1": f"1:{tp1_pips / sl_pips:.2f}",
        "risk_reward_tp2": f"1:{tp2_pips / sl_pips:.2f}",
        "probability_score": f"{skor}/{max_score}",
        "probability_label": label,
        "min_score_required": SCALP_MIN_SCORE,
        "volatility_ok": vol_ok,
        "atr15_pct": vol_pct,
        "confluences": catatan,
        "zona_scalping": {"bawah": zona_bawah, "atas": zona_atas},
        "ema9_m15": ema9,
        "ema21_m15": ema21,
        "rsi14_m15": rsi14,
        "atr15m": atr15,
        "momentum_3bar": momentum_3bar,
        "daily_trend": daily_trend,
        "sma5": sma5,
        "sma20": sma20,
        "last_bar": last_bar,
        "valid_until": valid_until.strftime("%Y-%m-%d %H:%M WIB"),
        "reason": alasan,
        "disclaimer": "Edukasi/analisis teknis sederhana, bukan saran keuangan.",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@mcp.tool()
def get_gold_scalping_signal_html() -> str:
    """Sinyal scalping momentum XAUUSD dalam teks siap-kirim ke Telegram."""
    a = get_gold_scalping_signal()
    zona = a["zona_scalping"]
    tunggu = a["direction"] == "TUNGGU"
    header = (
        "⏳ *Scalping XAUUSD (momentum M15) — TUNGGU*\n"
        if tunggu
        else "⚡ *Scalping XAUUSD (momentum M15)*\n"
    )
    arah_line = f"➡️ Arah: *{a['direction']}*"
    if not tunggu:
        arah_line += f"  |  Order: *{a['order_type']}*\n🎯 Entry limit: {a['entry_limit']:,.2f}"
    else:
        arah_line += f"  |  Bias: {a['bias']}\n🎯 Zona limit terdekat: {a['entry_limit']:,.2f}"
    return (
        f"{header}"
        f"💵 Spot: ${a['spot_price']:,.2f}  |  Tren harian: {a['daily_trend']}\n\n"
        f"{arah_line}\n"
        f"📏 Zona momentum: {zona['bawah']:,.2f} – {zona['atas']:,.2f}\n"
        f"🛑 SL: {a['stop_loss']:,.2f}  ({a['sl_pips']:.0f} pip = {a['risk_points']:,.2f} poin)\n"
        f"🥇 TP1: {a['take_profit_1']:,.2f}  ({a['tp1_pips']:.0f} pip, R:R {a['risk_reward_tp1']})"
        " — tutup 50% & geser SL ke BE\n"
        f"🥈 TP2: {a['take_profit_2']:,.2f}  ({a['tp2_pips']:.0f} pip, R:R {a['risk_reward_tp2']})"
        " — sisanya trailing\n"
        f"⏰ Order berlaku s/d: {a['valid_until']}\n\n"
        f"📐 Skor confluence: *{a['probability_score']}* — {a['probability_label']} "
        f"(minimal {a['min_score_required']:.0f})\n"
        f"📊 RSI14 {a['rsi14_m15']} | EMA9 {a['ema9_m15']} | EMA21 {a['ema21_m15']} | "
        f"ATR15 {a['atr15m']} | Mom3 {a['momentum_3bar']}\n"
        f"🕒 Bar terakhir: {a['last_bar']}\n\n"
        "✅ Konfluence:\n"
        + "\n".join(f"• {n}" for n in a["confluences"])
        + f"\n\n🧠 {a['reason']}\n\n⚠️ {a['disclaimer']}"
    )


@mcp.tool()
def get_gold_scalping_m5_signal(
    sl_pips: float | None = None,
    tp1_pips: float | None = None,
    tp2_pips: float | None = None,
) -> dict[str, Any]:
    """
    Sinyal SCALPING M5 (trigger) dengan ZONA M15 (area limit order) XAUUSD.

    * Trigger & momentum dibaca dari M5: EMA9/EMA21, RSI14, ATR5, struktur swing M5.
    * Zona entry & filter arah diambil dari M15: EMA21 ± 0,5·ATR15. Limit order
      dipasang di batas zona M15 terdekat (BUY di bawah harga, SELL di atas harga).
    * SL & TP tetap dalam pip: default SL 5 pip (= 5.00 poin bila GOLD_PIP_VALUE=1.0),
      TP1 10 pip (R:R 1:2), TP2 15 pip (R:R 1:3) — override via argumen atau env
      GOLD_SCALP_M5_*. 1 pip = GOLD_PIP_VALUE poin harga (default 1.0).
    * Skor confluence 0-6 + gate volatilitas ATR5 (0,02%-0,30% harga). Sinyal hanya
      keluar bila skor >= GOLD_SCALP_M5_MIN_SCORE (default 4); selain itu TUNGGU.

    Edukasi/analisis teknis sederhana, bukan saran keuangan.
    """
    sl_pips = float(sl_pips) if sl_pips else SCALP_M5_SL_PIPS
    tp1_pips = float(tp1_pips) if tp1_pips else SCALP_M5_TP1_PIPS
    tp2_pips = float(tp2_pips) if tp2_pips else SCALP_M5_TP2_PIPS

    spot = float(_clean(fetch_gold_price_raw())["price"])
    rows5 = fetch_intraday_history("5m")
    rows15 = fetch_intraday_history("15m")
    if len(rows5) < 30 or len(rows15) < 30:
        raise RuntimeError(
            "Data intraday M5/M15 dari Yahoo Finance tidak cukup (butuh minimal 30 bar)"
        )

    # Selaraskan futures (GC=F) tiap seri ke harga spot via offset masing-masing.
    off5 = round(spot - rows5[-1]["close"], 2)
    closes5 = [round(r["close"] + off5, 2) for r in rows5]
    highs5 = [round(r["high"] + off5, 2) for r in rows5]
    lows5 = [round(r["low"] + off5, 2) for r in rows5]
    last_bar5 = rows5[-1].get("time") or rows5[-1].get("date", "")

    off15 = round(spot - rows15[-1]["close"], 2)
    closes15 = [round(r["close"] + off15, 2) for r in rows15]
    highs15 = [round(r["high"] + off15, 2) for r in rows15]
    lows15 = [round(r["low"] + off15, 2) for r in rows15]
    last_bar15 = rows15[-1].get("time") or rows15[-1].get("date", "")

    # ---- indikator M5 (trigger)
    ema9_5 = _ema(closes5, 9)
    ema21_5 = _ema(closes5, 21)
    rsi5 = _rsi(closes5, 14)
    atr5 = _atr(highs5, lows5, 14)
    mom3_5 = round(closes5[-1] - closes5[-4], 2) if len(closes5) >= 4 else 0.0

    # ---- indikator M15 (zona + filter arah)
    ema9_15 = _ema(closes15, 9)
    ema21_15 = _ema(closes15, 21)
    atr15 = _atr(highs15, lows15, 14)
    mom3_15 = round(closes15[-1] - closes15[-4], 2) if len(closes15) >= 4 else 0.0

    ref15 = ema21_15 if ema21_15 is not None else closes15[-1]
    band15 = max(atr15, 0.1)
    zona_bawah = round(ref15 - band15 * 0.5, 2)
    zona_atas = round(ref15 + band15 * 0.5, 2)

    if zona_bawah <= spot <= zona_atas:
        zona_status = "DI DALAM ZONA M15"
    elif spot > zona_atas:
        zona_status = "DI ATAS ZONA M15"
    else:
        zona_status = "DI BAWAH ZONA M15"

    def confluence(direction: str) -> tuple[int, list[str]]:
        """Skor confluence 0-6 + catatan untuk satu arah (trigger M5, filter M15)."""
        up = direction == "BUY"
        skor = 0
        catatan: list[str] = []
        if (
            ema9_5 is not None
            and ema21_5 is not None
            and ((ema9_5 > ema21_5) if up else (ema9_5 < ema21_5))
        ):
            skor += 1
            catatan.append(
                f"Momentum M5 {'naik' if up else 'turun'} (EMA9 {ema9_5} vs EMA21 {ema21_5})"
            )
        if (
            ema9_15 is not None
            and ema21_15 is not None
            and ((ema9_15 > ema21_15) if up else (ema9_15 < ema21_15))
        ):
            skor += 1
            catatan.append(
                f"Momentum M15 searah {direction} (EMA9 {ema9_15} vs EMA21 {ema21_15})"
            )
        if (closes5[-1] >= ref15) if up else (closes5[-1] <= ref15):
            skor += 1
            catatan.append(
                f"Harga di sisi {'atas' if up else 'bawah'} zona M15 (close M5 {closes5[-1]})"
            )
        if rsi5 is not None and ((45 <= rsi5 <= 70) if up else (30 <= rsi5 <= 55)):
            skor += 1
            catatan.append(f"RSI14 M5 {rsi5} mendukung {direction} (tidak ekstrem)")
        if len(lows5) > 21 and len(highs5) > 21:
            if up and lows5[-1] > min(lows5[-21:-1]):
                skor += 1
                catatan.append(f"Higher low M5: {lows5[-1]} > {min(lows5[-21:-1])}")
            elif not up and highs5[-1] < max(highs5[-21:-1]):
                skor += 1
                catatan.append(f"Lower high M5: {highs5[-1]} < {max(highs5[-21:-1])}")
        if abs(closes5[-1] - ref15) <= band15 * 1.5:
            skor += 1
            catatan.append(
                f"Harga menarik ke zona M15 {zona_bawah}-{zona_atas} → cocok limit order"
            )
        return skor, catatan

    buy_score, buy_notes = confluence("BUY")
    sell_score, sell_notes = confluence("SELL")
    if buy_score > sell_score:
        bias, skor, catatan = "BUY", buy_score, buy_notes
    else:
        bias, skor, catatan = "SELL", sell_score, sell_notes
        if sell_score == buy_score:
            catatan = [*catatan, "Skor BUY & SELL imbang → bias default SELL (mean-reversion)"]

    # Gate volatilitas M5: ATR5 harus wajar (0,02%-0,30% harga) — syarat mutlak,
    # bukan poin skor, agar sinyal tidak diambil saat market mati/liar.
    vol_pct = round(atr5 / spot * 100, 2) if spot else 0.0
    vol_ok = bool(atr5) and 0.0002 * spot <= atr5 <= 0.003 * spot

    max_score = 6
    arah = bias if skor >= SCALP_M5_MIN_SCORE else "TUNGGU"
    if not vol_ok:
        arah = "TUNGGU"

    # ---- level: SL/TP tetap dalam pip, limit order di batas zona M15
    pip = PIP_VALUE
    sl_dist = round(sl_pips * pip, 2)
    tp1_dist = round(tp1_pips * pip, 2)
    tp2_dist = round(tp2_pips * pip, 2)
    pullback5 = max(round(atr5 * 0.2, 2), 0.1)
    if bias == "BUY":
        entry_limit = round(min(zona_bawah, spot - pullback5), 2)
        sl = round(entry_limit - sl_dist, 2)
        tp1 = round(entry_limit + tp1_dist, 2)
        tp2 = round(entry_limit + tp2_dist, 2)
    else:
        entry_limit = round(max(zona_atas, spot + pullback5), 2)
        sl = round(entry_limit + sl_dist, 2)
        tp1 = round(entry_limit - tp1_dist, 2)
        tp2 = round(entry_limit - tp2_dist, 2)
    jarak_entry = round(abs(entry_limit - spot), 2)

    if skor >= 5:
        label = "TINGGI"
    elif skor >= SCALP_M5_MIN_SCORE:
        label = "SEDANG-TINGGI"
    elif skor >= 3:
        label = "RENDAH (tunggu konfirmasi)"
    else:
        label = "SANGAT RENDAH (hindari entry)"

    if arah == "TUNGGU":
        if not vol_ok:
            alasan = (
                f"Volatilitas M5 tidak wajar (ATR5 {atr5} = {vol_pct}% harga, syarat "
                f"0,02%-0,30%) → entry dibatalkan meski skor {skor}/{max_score} (bias {bias}). "
                "Tunggu volatilitas M5 normal."
            )
        else:
            alasan = (
                f"Skor confluence {skor}/{max_score} < ambang {SCALP_M5_MIN_SCORE:.0f} → belum layak entry. "
                f"Bias sementara {bias}; trigger M5 (EMA9 {ema9_5} vs EMA21 {ema21_5}, RSI14 {rsi5}, "
                f"mom3 {mom3_5}), zona M15 (mom3 {mom3_15}), harga {zona_status.lower()}. "
                f"Tunggu trigger M5 selaras zona {zona_bawah:,.2f}-{zona_atas:,.2f} dan skor naik ke "
                f"minimal {SCALP_M5_MIN_SCORE:.0f}."
            )
    else:
        alasan = (
            f"Trigger M5 {'naik' if bias == 'BUY' else 'turun'} (EMA9 {ema9_5} vs EMA21 {ema21_5}, "
            f"RSI14 {rsi5}, mom3 {mom3_5}) selaras momentum M15 (EMA9 {ema9_15} vs EMA21 {ema21_15}, "
            f"mom3 {mom3_15}). Skor confluence {skor}/{max_score} ≥ ambang {SCALP_M5_MIN_SCORE:.0f} → "
            f"pasang {bias} LIMIT di {entry_limit:,.2f} ({jarak_entry:.2f} poin dari spot) pada batas zona "
            f"M15 {zona_bawah:,.2f}-{zona_atas:,.2f}; SL {sl_dist:.2f} poin, TP1 {tp1_dist:.2f} poin."
        )

    valid_until = (datetime.now(timezone.utc) + timedelta(hours=SCALP_M5_VALID_HOURS)).astimezone(WIB)

    return {
        "symbol": "XAUUSD",
        "mode": "scalping-momentum M5 + zona M15",
        "timeframe_trigger": "M5",
        "timeframe_zona": "M15",
        "spot_price": spot,
        "direction": arah,
        "bias": bias,
        "order_type": "LIMIT" if arah != "TUNGGU" else "WAIT",
        "entry_limit": entry_limit,
        "entry_market": spot,
        "entry_distance_points": jarak_entry,
        "stop_loss": sl,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "sl_pips": sl_pips,
        "tp1_pips": tp1_pips,
        "tp2_pips": tp2_pips,
        "pip_value": pip,
        "risk_points": sl_dist,
        "reward_points_tp1": tp1_dist,
        "reward_points_tp2": tp2_dist,
        "risk_reward_tp1": f"1:{tp1_pips / sl_pips:.2f}",
        "risk_reward_tp2": f"1:{tp2_pips / sl_pips:.2f}",
        "probability_score": f"{skor}/{max_score}",
        "probability_label": label,
        "min_score_required": SCALP_M5_MIN_SCORE,
        "volatility_ok": vol_ok,
        "atr5_pct": vol_pct,
        "confluences": catatan,
        "zona_m15": {"bawah": zona_bawah, "atas": zona_atas, "ema21": ema21_15, "atr15": atr15},
        "zona_status": zona_status,
        "ema9_m5": ema9_5,
        "ema21_m5": ema21_5,
        "rsi14_m5": rsi5,
        "atr5": atr5,
        "momentum_3bar_m5": mom3_5,
        "ema9_m15": ema9_15,
        "ema21_m15": ema21_15,
        "atr15m": atr15,
        "momentum_3bar_m15": mom3_15,
        "last_bar_m5": last_bar5,
        "last_bar_m15": last_bar15,
        "valid_until": valid_until.strftime("%Y-%m-%d %H:%M WIB"),
        "reason": alasan,
        "disclaimer": "Edukasi/analisis teknis sederhana, bukan saran keuangan.",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@mcp.tool()
def get_gold_scalping_m5_signal_html() -> str:
    """Sinyal scalping M5 (trigger) + zona M15 XAUUSD dalam teks siap-kirim Telegram."""
    a = get_gold_scalping_m5_signal()
    zona = a["zona_m15"]
    tunggu = a["direction"] == "TUNGGU"
    header = (
        "⏳ *Scalping XAUUSD M5 + zona M15 — TUNGGU*\n"
        if tunggu
        else "⚡ *Scalping XAUUSD M5 + zona M15*\n"
    )
    arah_line = f"➡️ Arah: *{a['direction']}*"
    if not tunggu:
        arah_line += (
            f"  |  Order: *{a['order_type']}*\n"
            f"🎯 Entry limit: {a['entry_limit']:,.2f}"
            f"  ({a['entry_distance_points']:.2f} poin dari spot)"
        )
    else:
        arah_line += f"  |  Bias: {a['bias']}\n🎯 Zona limit terdekat: {a['entry_limit']:,.2f}"
    return (
        f"{header}"
        f"💵 Spot: ${a['spot_price']:,.2f}  |  Posisi: {a['zona_status']}\n\n"
        f"{arah_line}\n"
        f"📏 Zona M15: {zona['bawah']:,.2f} – {zona['atas']:,.2f}"
        f"  (EMA21 {zona['ema21']}, ATR15 {zona['atr15']})\n"
        f"🛑 SL: {a['stop_loss']:,.2f}  ({a['sl_pips']:.0f} pip = {a['risk_points']:,.2f} poin)\n"
        f"🥇 TP1: {a['take_profit_1']:,.2f}  ({a['tp1_pips']:.0f} pip, R:R {a['risk_reward_tp1']})"
        " — tutup 50% & geser SL ke BE\n"
        f"🥈 TP2: {a['take_profit_2']:,.2f}  ({a['tp2_pips']:.0f} pip, R:R {a['risk_reward_tp2']})"
        " — sisanya trailing\n"
        f"⏰ Order berlaku s/d: {a['valid_until']}\n\n"
        f"📐 Skor confluence: *{a['probability_score']}* — {a['probability_label']} "
        f"(minimal {a['min_score_required']:.0f})\n"
        f"📊 M5  → RSI14 {a['rsi14_m5']} | EMA9 {a['ema9_m5']} | EMA21 {a['ema21_m5']} | "
        f"ATR {a['atr5']} | Mom3 {a['momentum_3bar_m5']}\n"
        f"📊 M15 → EMA9 {a['ema9_m15']} | EMA21 {a['ema21_m15']} | ATR {a['atr15m']} | "
        f"Mom3 {a['momentum_3bar_m15']}\n"
        f"🕒 Bar M5: {a['last_bar_m5']}  |  M15: {a['last_bar_m15']}\n\n"
        "✅ Konfluence:\n"
        + "\n".join(f"• {n}" for n in a["confluences"])
        + f"\n\n🧠 {a['reason']}\n\n⚠️ {a['disclaimer']}"
    )





@mcp.tool()
def get_gold_smc_analysis(
    sl_pips: float | None = None,
    tp1_pips: float | None = None,
    tp2_pips: float | None = None,
) -> dict[str, Any]:
    """
    Analisa SMC / ICT-style XAUUSD: tentukan BIAS, narasikan SKENARIO imbalan
    (imbalance -> liquidity -> liquidity -> imbalance), lalu PINDAI ORDER BLOCK
    di low timeframe (M5) dengan filter arah dari M15 + bias harian.

    Skenario naratif yang dideteksi:
    * imbalan -> liquidity: harga meninggalkan FVG/OB (displacement), target
      likuiditas swing terdekat (equal highs/lows atau swing ekstrem);
    * liquidity -> imbalance: sweep (shadow menembus swing) lalu close kembali
      + displacement berlawanan (potensi reversal ke FVG/OB);
    * liquidity -> liquidity: rangkaian sweep berlanjut, bias = arah sweep terakhir;
    * imbalan -> imbalan: displacement berlanjut tanpa sweep (tren kuat).

    SL/TP tetap dalam pip (default SL 50 pip, TP1 100 pip, TP2 150 pip —
    mode SMC memakai 50/100/150 seperti `/scalp`; override via argumen).
    Entry default = LIMIT di order block LTF terdekat yang selaras bias.

    Edukasi/analisis teknis sederhana, bukan saran keuangan.
    """
    sl_pips = float(sl_pips) if sl_pips else 50.0
    tp1_pips = float(tp1_pips) if tp1_pips else 100.0
    tp2_pips = float(tp2_pips) if tp2_pips else 150.0

    spot = float(_clean(fetch_gold_price_raw())["price"])
    rows5 = fetch_intraday_history("5m")
    rows15 = fetch_intraday_history("15m")
    drows = fetch_daily_history()
    if len(rows5) < 30 or len(rows15) < 30 or len(drows) < 21:
        raise RuntimeError(
            "Data M5/M15/harian dari Yahoo Finance tidak cukup untuk analisa SMC"
        )

    off5 = round(spot - rows5[-1]["close"], 2)
    o5 = [round(r["open"] + off5, 2) for r in rows5]
    c5 = [round(r["close"] + off5, 2) for r in rows5]
    h5 = [round(r["high"] + off5, 2) for r in rows5]
    l5 = [round(r["low"] + off5, 2) for r in rows5]
    last_bar5 = rows5[-1].get("time") or rows5[-1].get("date", "")

    off15 = round(spot - rows15[-1]["close"], 2)
    o15 = [round(r["open"] + off15, 2) for r in rows15]
    c15 = [round(r["close"] + off15, 2) for r in rows15]
    h15 = [round(r["high"] + off15, 2) for r in rows15]
    l15 = [round(r["low"] + off15, 2) for r in rows15]
    last_bar15 = rows15[-1].get("time") or rows15[-1].get("date", "")

    dcloses = [r["close"] for r in drows]

    ema21_15 = _ema(c15, 21)
    ema9_15 = _ema(c15, 9)
    mom15 = (
        "UP" if ema9_15 is not None and ema21_15 is not None and ema9_15 > ema21_15
        else "DOWN" if ema9_15 is not None and ema21_15 is not None and ema9_15 < ema21_15
        else "FLAT"
    )
    ema9_5 = _ema(c5, 9)
    ema21_5 = _ema(c5, 21)
    mom5 = (
        "UP" if ema9_5 is not None and ema21_5 is not None and ema9_5 > ema21_5
        else "DOWN" if ema9_5 is not None and ema21_5 is not None and ema9_5 < ema21_5
        else "FLAT"
    )
    sma5 = _sma(dcloses, 5)
    sma20 = _sma(dcloses, 20)
    htf = (
        "DOWN" if sma5 is not None and sma20 is not None and sma5 < sma20
        else "UP" if sma5 is not None and sma20 is not None and sma5 > sma20
        else "FLAT"
    )
    sh15, sl15 = _swing_points(h15, l15)
    last_sh = h15[sh15[-1]] if sh15 else None
    last_sl = l15[sl15[-1]] if sl15 else None
    struct = (
        "UP" if last_sh is not None and last_sl is not None and c15[-1] > last_sh
        else "DOWN" if last_sh is not None and last_sl is not None and c15[-1] < last_sl
        else "RANGE"
    )
    disp5 = _displacement(o5, c5, h5, l5)

    skor_bias = {"BUY": 0.0, "SELL": 0.0}
    alasan_bias: list[str] = []
    if htf in ("UP", "DOWN"):
        sisi = "BUY" if htf == "UP" else "SELL"
        skor_bias[sisi] += SMC_BIAS_W_HTF
        alasan_bias.append(
            f"Tren harian {htf} (SMA5 {sma5} vs SMA20 {sma20}) -> +{SMC_BIAS_W_HTF:g} {sisi}"
        )
    for label, m in (("M15", mom15), ("M5", mom5)):
        if m in ("UP", "DOWN"):
            sisi = "BUY" if m == "UP" else "SELL"
            skor_bias[sisi] += SMC_BIAS_W_MOM
            alasan_bias.append(f"Momentum {label} {m} -> +{SMC_BIAS_W_MOM:g} {sisi}")
    if struct in ("UP", "DOWN"):
        sisi = "BUY" if struct == "UP" else "SELL"
        skor_bias[sisi] += SMC_BIAS_W_STRUCT
        alasan_bias.append(f"Struktur M15 {struct} (break swing) -> +{SMC_BIAS_W_STRUCT:g} {sisi}")
    if disp5["impulse"]:
        sisi = "BUY" if disp5["arah"] == "UP" else "SELL"
        skor_bias[sisi] += SMC_BIAS_W_DISP
        alasan_bias.append(
            f"Displacement M5 {disp5['arah']} (body/range {disp5['ratio']}) -> +{SMC_BIAS_W_DISP:g} {sisi}"
        )
    bias = (
        "NETRAL" if skor_bias["BUY"] == skor_bias["SELL"]
        else "BUY" if skor_bias["BUY"] > skor_bias["SELL"]
        else "SELL"
    )

    atr5 = _atr(h5, l5, 14)
    sh5, sl5 = _swing_points(h5, l5)
    tol_eq = max(atr5 * 0.25, 0.1)
    eq_highs = [
        h5[i] for i in sh5[-4:]
        if any(abs(h5[i] - h5[j]) <= tol_eq for j in sh5[-4:] if j != i)
    ]
    eq_lows = [
        l5[i] for i in sl5[-4:]
        if any(abs(l5[i] - l5[j]) <= tol_eq for j in sl5[-4:] if j != i)
    ]
    ref_highs = [h5[i] for i in sh5[-4:]] or [h5[-1]]
    ref_lows = [l5[i] for i in sl5[-4:]] or [l5[-1]]
    sweep_up = h5[-1] > max(ref_highs) and c5[-1] <= max(ref_highs)
    sweep_dn = l5[-1] < min(ref_lows) and c5[-1] >= min(ref_lows)
    fvg_up: list[dict[str, Any]] = []
    fvg_dn: list[dict[str, Any]] = []
    for i in range(max(2, len(c5) - 30), len(c5)):
        lo_body_prev = min(o5[i - 1], c5[i - 1])
        hi_body_prev = max(o5[i - 1], c5[i - 1])
        if min(o5[i], c5[i]) > hi_body_prev and (c5[i] - c5[i - 1]) > 0:
            fvg_up.append({"bawah": round(hi_body_prev, 2), "atas": round(min(o5[i], c5[i]), 2), "bar": i})
        if max(o5[i], c5[i]) < lo_body_prev and (c5[i] - c5[i - 1]) < 0:
            fvg_dn.append({"bawah": round(max(o5[i], c5[i]), 2), "atas": round(lo_body_prev, 2), "bar": i})
    fvg_up, fvg_dn = fvg_up[-4:], fvg_dn[-4:]

    skenario = "imbalan -> imbalan"
    narasi = ""
    liquidity_target: dict[str, Any] | None = None
    if sweep_up and disp5["arah"] == "DOWN":
        skenario = "liquidity -> imbalance"
        tgt = min(eq_lows) if eq_lows else (min(l5[-20:]) if len(l5) >= 20 else min(l5))
        liquidity_target = {"jenis": "equal lows / swing low M5", "harga": round(tgt, 2)}
        narasi = (
            f"Harga menyapu likuiditas atas (sweep high M5 di {h5[-1]:,.2f}, close kembali "
            f"{c5[-1]:,.2f}) lalu displacement turun — narasi liquidity -> imbalance: "
            f"target imbalan di bawah (FVG/OB bearish), waspadai reaksi beli di FVG."
        )
    elif sweep_dn and disp5["arah"] == "UP":
        skenario = "liquidity -> imbalance"
        tgt = max(eq_highs) if eq_highs else (max(h5[-20:]) if len(h5) >= 20 else max(h5))
        liquidity_target = {"jenis": "equal highs / swing high M5", "harga": round(tgt, 2)}
        narasi = (
            f"Harga menyapu likuiditas bawah (sweep low M5 di {l5[-1]:,.2f}, close kembali "
            f"{c5[-1]:,.2f}) lalu displacement naik — narasi liquidity -> imbalance: "
            f"target imbalan di atas (FVG/OB bullish)."
        )
    elif sweep_up or sweep_dn:
        skenario = "liquidity -> liquidity"
        sisi_sweep = "atas" if sweep_up else "bawah"
        narasi = (
            f"Sweep {sisi_sweep} tanpa displacement balikan — narasi liquidity -> liquidity: "
            f"smart money kemungkinan lanjut memburu likuiditas berikutnya searah sweep. "
            f"Bias mengikuti arah sweep terakhir."
        )
    elif disp5["impulse"]:
        skenario = "imbalan -> liquidity"
        window_h = h5[-20:] if len(h5) >= 20 else h5
        window_l = l5[-20:] if len(l5) >= 20 else l5
        tgt = max(window_h) if disp5["arah"] == "UP" else min(window_l)
        liquidity_target = {"jenis": "swing 20-bar M5 searah displacement", "harga": round(tgt, 2)}
        narasi = (
            f"Displacement {disp5['arah']} (body/range {disp5['ratio']}) meninggalkan imbalan "
            f"(FVG) — narasi imbalan -> liquidity: harga cenderung ditarik ke likuiditas "
            f"swing {tgt:,.2f} sebelum reaksi."
        )
    else:
        narasi = (
            "Tidak ada sweep maupun displacement kuat — narasi imbalan -> imbalan: "
            "harga berputar di dalam range, tunggu ekspansi (displacement) dulu."
        )

    ob_bull, ob_bear = _scan_order_blocks(o5, h5, l5, c5, atr5)
    highs15 = [round(r["high"] + off15, 2) for r in rows15]
    lows15 = [round(r["low"] + off15, 2) for r in rows15]
    closes15 = [round(r["close"] + off15, 2) for r in rows15]
    atr15 = _atr(highs15, lows15, 14)
    ema21_15 = _ema(closes15, 21)
    ref15 = ema21_15 if ema21_15 is not None else closes15[-1]
    band15 = max(atr15, 0.1)
    zona = {"bawah": round(ref15 - band15 * 0.5, 2), "atas": round(ref15 + band15 * 0.5, 2)}

    arah: str
    zona_pakai: dict[str, Any] | None = None
    catatan: list[str] = []
    if bias == "BUY":
        kandidat = [z for z in ob_bull if z["status"] == "fresh"] or ob_bull
        zona_pakai = _nearest(kandidat, spot)
        arah = "BUY" if zona_pakai else "TUNGGU"
        if zona_pakai:
            catatan.append(
                f"Order block bullish M5 {zona_pakai['bawah']:,.2f}-{zona_pakai['atas']:,.2f} "
                f"({zona_pakai['status']}) selaras bias BUY -> entry LIMIT di zona"
            )
        else:
            catatan.append("Tidak ada order block bullish M5 -> TUNGGU sampai terbentuk")
    elif bias == "SELL":
        kandidat = [z for z in ob_bear if z["status"] == "fresh"] or ob_bear
        zona_pakai = _nearest(kandidat, spot)
        arah = "SELL" if zona_pakai else "TUNGGU"
        if zona_pakai:
            catatan.append(
                f"Order block bearish M5 {zona_pakai['bawah']:,.2f}-{zona_pakai['atas']:,.2f} "
                f"({zona_pakai['status']}) selaras bias SELL -> entry LIMIT di zona"
            )
        else:
            catatan.append("Tidak ada order block bearish M5 -> TUNGGU sampai terbentuk")
    else:
        arah = "TUNGGU"
        zona_pakai = _nearest([*ob_bull, *ob_bear], spot)
        catatan.append(f"Bias NETRAL (skor {skor_bias['BUY']:g} vs {skor_bias['SELL']:g}) -> TUNGGU")

    pip = PIP_VALUE
    sl_dist = round(sl_pips * pip, 2)
    tp1_dist = round(tp1_pips * pip, 2)
    tp2_dist = round(tp2_pips * pip, 2)
    if arah == "BUY" and zona_pakai:
        entry_limit = round(min(zona_pakai["atas"], spot - 0.1), 2)
        sl = round(entry_limit - sl_dist, 2)
        tp1 = round(entry_limit + tp1_dist, 2)
        tp2 = round(entry_limit + tp2_dist, 2)
    elif arah == "SELL" and zona_pakai:
        entry_limit = round(max(zona_pakai["bawah"], spot + 0.1), 2)
        sl = round(entry_limit + sl_dist, 2)
        tp1 = round(entry_limit - tp1_dist, 2)
        tp2 = round(entry_limit - tp2_dist, 2)
    else:
        entry_limit = spot
        sl = round(spot - sl_dist, 2) if bias == "BUY" else round(spot + sl_dist, 2)
        tp1 = round(spot + tp1_dist, 2) if bias == "BUY" else round(spot - tp1_dist, 2)
        tp2 = round(spot + tp2_dist, 2) if bias == "BUY" else round(spot - tp2_dist, 2)
    jarak_entry = round(abs(entry_limit - spot), 2)
    last_bar5 = rows5[-1].get("time") or rows5[-1].get("date", "")
    last_bar15 = rows15[-1].get("time") or rows15[-1].get("date", "")
    valid_until = (datetime.now(timezone.utc) + timedelta(hours=SCALP_VALID_HOURS)).astimezone(WIB)

    alasan = (
        f"BIAS {bias} (BUY {skor_bias['BUY']:g} vs SELL {skor_bias['SELL']:g}). "
        + " | ".join(alasan_bias)
        + f" SKENARIO: {skenario} — {narasi}"
        + (" " + " | ".join(catatan) if catatan else "")
    )

    return {
        "symbol": "XAUUSD",
        "mode": "analisa SMC/ICT (bias -> skenario -> order block M5)",
        "spot_price": spot,
        "bias": bias,
        "bias_score": {"BUY": skor_bias["BUY"], "SELL": skor_bias["SELL"]},
        "bias_reasons": alasan_bias,
        "skenario": skenario,
        "skenario_narasi": narasi,
        "liquidity_target": liquidity_target,
        "direction": arah,
        "order_type": "LIMIT" if arah != "TUNGGU" else "WAIT",
        "entry_limit": entry_limit,
        "entry_market": spot,
        "entry_distance_points": jarak_entry,
        "stop_loss": sl,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "sl_pips": sl_pips,
        "tp1_pips": tp1_pips,
        "tp2_pips": tp2_pips,
        "pip_value": pip,
        "risk_points": sl_dist,
        "reward_points_tp1": tp1_dist,
        "reward_points_tp2": tp2_dist,
        "risk_reward_tp1": f"1:{tp1_pips / sl_pips:.2f}",
        "risk_reward_tp2": f"1:{tp2_pips / sl_pips:.2f}",
        "zona_order_block": zona_pakai,
        "order_blocks_bullish_m5": ob_bull,
        "order_blocks_bearish_m5": ob_bear,
        "zona_m15": {"bawah": zona["bawah"], "atas": zona["atas"], "ema21": ema21_15, "atr15": atr15},
        "equal_highs_m5": [round(x, 2) for x in eq_highs[-4:]],
        "equal_lows_m5": [round(x, 2) for x in eq_lows[-4:]],
        "sweep": {"atas": sweep_up, "bawah": sweep_dn},
        "fvg_bullish_m5": fvg_up,
        "fvg_bearish_m5": fvg_dn,
        "displacement_m5": disp5,
        "atr5": atr5,
        "momentum_m5": "UP" if mom5 == "UP" else ("DOWN" if mom5 == "DOWN" else "FLAT"),
        "momentum_m15": "UP" if mom15 == "UP" else ("DOWN" if mom15 == "DOWN" else "FLAT"),
        "struktur_m15": struct,
        "swing_high_m15": last_sh,
        "swing_low_m15": last_sl,
        "tren_harian": htf,
        "sma5": sma5,
        "sma20": sma20,
        "last_bar_m5": last_bar5,
        "last_bar_m15": last_bar15,
        "valid_until": valid_until.strftime("%Y-%m-%d %H:%M WIB"),
        "reason": alasan,
        "disclaimer": "Edukasi/analisis teknis sederhana, bukan saran keuangan.",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

@mcp.tool()
def get_gold_smc_analysis_html() -> str:
    """Analisa SMC XAUUSD (bias -> skenario -> order block) dalam teks siap-kirim Telegram."""
    a = get_gold_smc_analysis()
    tunggu = a["direction"] == "TUNGGU"
    ob = a["zona_order_block"]
    ob_line = (
        f"OB: {ob['bawah']:,.2f}-{ob['atas']:,.2f} ({ob['status']})"
        if ob
        else "OB: belum ada yang selaras bias"
    )
    header = (
        "⏳ *SMC XAUUSD — TUNGGU*\n" if tunggu else "🧭 *SMC XAUUSD (bias → skenario → OB)*\n"
    )
    arah_line = f"➡️ Arah: *{a['direction']}*"
    if not tunggu:
        arah_line += (
            f"  |  Order: *{a['order_type']}*\n"
            f"🎯 Entry limit: {a['entry_limit']:,.2f}"
            f"  ({a['entry_distance_points']:.2f} poin dari spot)"
        )
    else:
        arah_line += f"  |  Bias: {a['bias']}\n🎯 Zona OB terdekat: {a['entry_limit']:,.2f}"
    liq = a["liquidity_target"]
    liq_line = (
        f"🎯 Target likuiditas: {liq['jenis']} @ {liq['harga']:,.2f}\n"
        if liq and liq.get("harga") is not None
        else "🎯 Target likuiditas: —\n"
    )
    eqh = ", ".join(f"{x:,.2f}" for x in a["equal_highs_m5"]) or "—"
    eql = ", ".join(f"{x:,.2f}" for x in a["equal_lows_m5"]) or "—"
    return (
        f"{header}"
        f"💵 Spot: ${a['spot_price']:,.2f}  |  Bias HTF: {a['bias']} "
        f"({a['bias_score']['BUY']:g} vs {a['bias_score']['SELL']:g})\n\n"
        f"{arah_line}\n"
        f"{ob_line}\n"
        f"🛑 SL: {a['stop_loss']:,.2f}  ({a['sl_pips']:.0f} pip = {a['risk_points']:,.2f} poin)\n"
        f"🥇 TP1: {a['take_profit_1']:,.2f}  ({a['tp1_pips']:.0f} pip, R:R {a['risk_reward_tp1']})\n"
        f"🥈 TP2: {a['take_profit_2']:,.2f}  ({a['tp2_pips']:.0f} pip, R:R {a['risk_reward_tp2']})\n"
        f"⏰ Order berlaku s/d: {a['valid_until']}\n\n"
        f"📖 Skenario: *{a['skenario']}*\n{a['skenario_narasi']}\n{liq_line}\n"
        f"📊 M5 → ATR {a['atr5']} | disp {a['displacement_m5']['ratio']} {a['displacement_m5']['arah']} | "
        f"sweep atas/bawah: {a['sweep']['atas']}/{a['sweep']['bawah']}\n"
        f"📊 M15 → {a['struktur_m15']} | tren harian {a['tren_harian']} | "
        f"EQH [{eqh}] / EQL [{eql}]\n"
        f"🕒 Bar M5: {a['last_bar_m5']}  |  M15: {a['last_bar_m15']}\n\n"
        f"🧠 {a['reason']}\n\n⚠️ {a['disclaimer']}"
    )


if __name__ == "__main__":
    # Jalankan server MCP (mode stdio).
    mcp.run()