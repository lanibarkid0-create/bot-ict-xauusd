# Gold Price Telegram Bot ✨

Bot Telegram yang mengambil **harga emas XAUUSD secara real-time** melalui
**MCP server** dan menjawab di chat.

```
[User di Telegram]
        │  "harga emas"
        ▼
[bot_telegram.py]  →  MCP client (stdio)  →  [gold_mcp_server.py]  →  gold-api.com
        ▲
        └──────────  balas harga XAUUSD  ──────────────────────────
```

## Struktur
| File | Peran |
|------|-------|
| `gold_mcp_server.py` | MCP server, mengekspos tool `get_gold_price`, `get_gold_price_html`, `get_gold_analysis`, `get_gold_analysis_html`, `get_gold_scalping_signal`, `get_gold_scalping_signal_html`, `get_gold_scalping_m5_signal`, `get_gold_scalping_m5_signal_html`, `get_gold_smc_analysis`, `get_gold_smc_analysis_html`, `get_gold_intraday_signal`, `get_gold_intraday_signal_html`, `get_gold_swing_signal`, `get_gold_swing_signal_html` |
| `bot_telegram.py` | Bridge: bot Telegram → MCP client → MCP server → reply harga/signal/scalp/smc/intraday/swing/vibe/fincept/hedge/fusion |
| `vibe_trading.py` | 🤖 Adapter Vibe-Trading (HKUDS): EMA/RSI/MACD/BB + backtest EMA-cross (pure python) |
| `fincept_terminal.py` | 🏦 Adapter FinceptTerminal: Sharpe/VaR/DCF/opsi Black-Scholes/yield/portofolio min-variance |
| `autohedge.py` | 🐝 Adapter AutoHedge (Swarm): pipeline Director→Quant→Risk→Execution + position sizing |
| `test_server.py` | Test end-to-end memanggil seluruh tool MCP (tanpa Telegram) |
| `test_bridge.py` | Test jalur bridge bot_telegram → MCP (tanpa Telegram, pakai token dummy): teks harga kembali, sesi MCP dipakai ulang, error tool → exception |
| `test_handlers.py` | Test handler bot (`reply_price`/`reply_signal`/`reply_scalp`/`reply_scalp_m5`/`reply_smc`) via Update palsu (tanpa token) |
| `bench_tools.py` | Ukur latensi tiap tool MCP (membuktikan efek cache TTL) |
| `validate_token.py` | Cek validitas `BOT_TOKEN` via Telegram `getMe` sebelum menjalankan bot |
| `requirements.txt` | Dependensi Python |

## Tools MCP
| Tool | Hasil |
|------|-------|
| `get_gold_price` | Harga spot XAUUSD (dict: price, currency, updated_at) |
| `get_gold_price_html` | Harga XAUUSD siap-kirim Telegram (teks + emoji) |
| `get_gold_analysis` | Analisa price action + signal (arah, entry, SL, TP1, TP2, R:R, alasan) |
| `get_gold_analysis_html` | Versi teks siap-kirim Telegram dari analisa di atas |
| `get_gold_scalping_signal` | **Scalping momentum M15**: arah + entry LIMIT + SL 50 pip / TP 100 pip / TP 150 pip, skor confluence 0–6, gate volatilitas (dict) |
| `get_gold_scalping_signal_html` | Versi teks siap-kirim Telegram dari sinyal scalping di atas |
| `get_gold_scalping_m5_signal` | **Scalping M5 + zona M15**: trigger dari M5, entry LIMIT di batas zona M15, SL 5 pip / TP 10 pip (R:R 1:2) / TP 15 pip (R:R 1:3), skor confluence 0–6, gate volatilitas ATR5 (dict) |
| `get_gold_scalping_m5_signal_html` | Versi teks siap-kirim Telegram dari sinyal scalping M5 di atas |
| `get_gold_intraday_signal` | **Intraday HTF→LTF**: bias H1 (+tren D1) → entry LIMIT di zona POI M15 (OB/FVG/EQL), SL 80 pip / TP1 160 / TP2 240 (R:R 1:2/1:3), skor confluence 0–6 (dict) |
| `get_gold_intraday_signal_html` | Versi teks siap-kirim Telegram dari sinyal intraday di atas |
| `get_gold_swing_signal` | **Swing HTF→LTF**: bias D1 → entry LIMIT di zona POI H1 (OB/FVG/EQH), SL 200 pip / TP1 400 / TP2 600 (R:R 1:2/1:3), tahan 2–5 hari (dict) |
| `get_gold_swing_signal_html` | Versi teks siap-kirim Telegram dari sinyal swing di atas |
| `get_gold_vibe_analysis` | 🤖 **Vibe-Trading**: EMA9/21, RSI14, MACD, Bollinger + backtest EMA-cross + skor 0–100 (dict) |
| `get_gold_vibe_analysis_html` | Versi teks siap-kirim Telegram dari analisa Vibe di atas |
| `get_gold_fincept_analytics` | 🏦 **FinceptTerminal**: Sharpe/VaR, DCF value-gap, opsi call Black-Scholes, yield obligasi, portofolio min-variance (dict) |
| `get_gold_fincept_analytics_html` | Versi teks siap-kirim Telegram dari analitik Fincept di atas |
| `get_gold_autohedge_plan` | 🐝 **AutoHedge**: pipeline Director→Quant→Risk→Execution + position sizing lot (dict) |
| `get_gold_autohedge_plan_html` | Versi teks siap-kirim Telegram dari rencana AutoHedge di atas |
| `get_gold_fusion_signal` | 🧬 **FUSION 3-repo**: vote SMC+Vibe+Fincept+AutoHedge → arah final + level scalping M15 (dict) |
| `get_gold_fusion_signal_html` | Versi teks siap-kirim Telegram dari sinyal fusion di atas |

**Command Telegram baru:** `/vibe` (Vibe-Trading), `/fincept` (Fincept), `/hedge` atau `/autohedge` (AutoHedge),
`/fusion` atau `/fusi` (gabungan ketiganya).

**Metode analisa:** tren dari **SMA5 vs SMA20** + posisi harga, level dari
**swing high/low 3 hari**, jarak SL memakai **ATR-14**. Histori harian diambil
dari Yahoo Finance (`GC=F`, ±1 bulan), lalu diselaraskan ke harga spot via offset.

**Cache & retry:** hasil HTTP di-cache in-memory — harga `GOLD_PRICE_CACHE_TTL`
(default 30 dtk), histori harian `GOLD_HISTORY_CACHE_TTL` (default 600 dtk), dan
histori intraday M15 `GOLD_INTRADAY_CACHE_TTL` (default 180 dtk) dan M5
`GOLD_M5_CACHE_TTL` (default 120 dtk) — sehingga
panggilan `/signal` berulang jadi instan dan tidak selalu bergantung jaringan.
Set `=0` untuk menonaktifkan. Bila `query1.finance.yahoo.com` gagal, otomatis
dicoba `query2.finance.yahoo.com` (masing-masing dengan retry 3×).

**Sesi MCP persisten:** bot memakai **satu** sesi stdio ke MCP server dan
memakainya ulang antar pesan (`MCP_PERSISTENT_SESSION=1`, default). Efeknya cache
di MCP server tetap hidup: panggilan `/signal` ke-2 dan seterusnya turun dari
±7 dtk menjadi ±0,0 dtk (lihat `python bench_tools.py`). Bila subprocess MCP mati,
sesi dibangun ulang otomatis. Set `MCP_PERSISTENT_SESSION=0` untuk kembali ke
mode spawn-subprocess-per-pesan. Error dari tool dikonversi menjadi exception
sehingga pengguna menerima pesan `⚠️ Gagal ...` yang rapi, bukan teks mentah.

## Alur interaktif `/start` (zona TF → gaya → sinyal MTF)
Ketik `/start` lalu ikuti 3 langkah lewat tombol inline:

1. **Pilih zona timeframe** — 9 tombol: `M1 M3 M5 M10 M15 M20 M30 H1 H4`,
   label tombol menampilkan pembentuk biasnya (mis. `M1 ← M15`).
2. **Pilih gaya** — ⚡ SCALPING (order 2 jam) · 📈 INTRADAY (12 jam) ·
   🌊 SWING (3 hari), plus tombol ◀️ Ubah timeframe untuk ganti zona.
3. **Terima sinyal** — laporan `get_gold_mtf_signal_html(zone_tf, style)`
   keluar sesuai peta zona → bias (entry di zona pilihan, arah dari TF bias),
   dengan tombol 🔁 Ulangi / ◀️ Ganti TF di bawah laporan.

Peta zona → bias (sumber: `unified_analysis.ZONE_BIAS_BY_TF`, dibacakan lewat
tool `get_gold_timeframes`):

| Zona | M1  | M3  | M5  | M10 | M15 | M20 | M30 | H1  | H4  |
|------|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| Bias | M15 | M30 | H1  | H1  | H4  | H4  | H4  | D1  | D1  |

Menu tombol diambil dari server MCP (`get_gold_timeframes`) supaya selalu
sinkron dengan logika analisa; bila MCP gagal, bot memakai peta cadangan
statis yang sama (dicek `test_handlers.py`). Gaya ditentukan dari pilihan
user — zona tetap dari TF yang ditekan, SL/TP & masa berlaku order dari gaya.

## Mode scalping momentum (`/scalp`)
Dirancang untuk **scalping momentum dengan SL/TP tetap dan order limit** —
bukan analisa harian `/signal`.

* **Timeframe:** data intraday **M15** (Yahoo `GC=F`, 5 hari) diselaraskan ke harga spot.
* **Indikator:** EMA9 vs EMA21 (momentum), RSI14, ATR15 (volatilitas), struktur
  swing (higher low / lower high), dan tren harian (SMA5/SMA20) sebagai filter.
* **SL/TP tetap:** SL **50 pip**, TP1 **100 pip** (R:R 1:2), TP2 **150 pip**
  (R:R 1:3). 1 pip = `GOLD_PIP_VALUE` poin harga (default **1,0** → 50 pip = 50 poin;
  kalau broker Anda memakai 1 pip = 0,1, set `GOLD_PIP_VALUE=0.1`).
* **Entry LIMIT:** harga dipasang di **zona momentum** (EMA21 ± 0,2·ATR15);
  `BUY` → limit di bawah harga, `SELL` → limit di atas harga. Ada juga
  `entry_market` (harga spot) bila ingin entry sekarang.
* **High-probability filter:** skor **confluence 0–6** (momentum M15, sisi zona
  EMA21, RSI sehat, tren harian searah, struktur swing, posisi harga di zona) +
  **gate volatilitas** (ATR15 harus 0,05%–0,60% harga). Sinyal hanya dikeluarkan
  bila skor **≥ `GOLD_SCALP_MIN_SCORE`** (default 4) dan gate lolos; kalau tidak,
  arah = **TUNGGU** beserta alasannya. Jadi bot memang sering menolak entry —
  itu bagian dari filternya.
* **Manajemen:** TP1 (100 pip) untuk tutup 50% + geser SL ke BE, sisa ke TP2
  (trailing). Order limit diberi masa berlaku `GOLD_SCALP_VALID_HOURS` (default 4 jam).
* Tool `get_gold_scalping_signal(sl_pips=None, tp1_pips=None, tp2_pips=None)`
  menerima override SL/TP per panggilan.

Contoh output (ringkas):
```
⚡ Scalping XAUUSD (momentum M15)
💵 Spot: $4,327.90  |  Tren harian: DOWN
➡️ Arah: BUY  |  Order: LIMIT
🎯 Entry limit: 4,305.16
🛑 SL: 4,255.16 (50 pip = 50.00 poin)
🥇 TP1: 4,405.16 (100 pip, R:R 1:2.00) — tutup 50% & geser SL ke BE
📐 Skor confluence: 4/6 — SEDANG-TINGGI (minimal 4)
```

## Mode scalping M5 + zona M15 (`/m5`)
Versi **lebih cepat** dari `/scalp`: **trigger entry dibaca dari M5**, sementara
**zona limit order & filter arah tetap dari M15**. Cocok saat ingin reaksi lebih
cepat tanpa melepas konteks zona M15.

* **Trigger (M5):** EMA9 vs EMA21, RSI14, ATR5, struktur swing M5 (higher low /
  lower high), dan momentum 3 bar.
* **Zona & filter (M15):** EMA21 ± 0,5·ATR15 → batas `BAWAH`/`ATAS`; momentum M15
  dipakai sebagai konfirmasi arah.
* **Entry LIMIT:** order dipasang di **batas zona M15 terdekat** — `BUY` di bawah
  harga (batas bawah zona), `SELL` di atas harga (batas atas zona). `entry_market`
  tetap disertakan bila ingin entry sekarang; `entry_distance_points` menunjukkan
  jarak limit dari spot.
* **SL/TP tetap (ketat, khas M5):** SL **5 pip** (= 5.00 poin bila `GOLD_PIP_VALUE=1.0`), TP1 **10 pip** (R:R 1:2), TP2 **15 pip** (R:R 1:3) — terpisah dari `/scalp` lewat `GOLD_SCALP_M5_*`.
* **Filter:** skor **confluence 0–6** (momentum M5 searah, momentum M15 searah,
  sisi harga terhadap zona, RSI sehat, struktur swing M5, kedekatan harga ke zona)
  + **gate volatilitas ATR5** (harus 0,02%–0,30% harga).
  Bila skor < `GOLD_SCALP_M5_MIN_SCORE` (default 4) atau gate gagal → **TUNGGU**.
* Order limit berlaku `GOLD_SCALP_M5_VALID_HOURS` (default **1,5 jam**) karena
  setup M5 cepat kedaluwarsa.
* Tool `get_gold_scalping_m5_signal(sl_pips=None, tp1_pips=None, tp2_pips=None)`
  menerima override SL/TP per panggilan (dict berisi `zona_m15`, `zona_status`,
  `atr5_pct`, `volatility_ok`, `confluences`, dll).

Contoh output (ringkas):
```
⚡ Scalping XAUUSD M5 + zona M15
 Spot: $4,327.90  |  Posisi: DI ATAS ZONA M15
➡️ Arah: BUY  |  Order: LIMIT
🎯 Entry limit: 4,308.40  (19.50 poin dari spot)
📏 Zona M15: 4,308.40 – 4,325.60  (EMA21 4317.0, ATR15 17.2)
📊 M5  → RSI14 56.2 | EMA9 4330.1 | EMA21 4326.9 | ATR 3.1 | Mom3 5.2
📐 Skor confluence: 5/6 — TINGGI (minimal 4)
```

## Mode SMC/ICT: bias → skenario → order block LTF (`/smc`)
Versi paling lengkap: menentukan **BIAS** berbobot, menyusun **naratif
imbalan↔likuiditas**, lalu **memindai order block di low timeframe (M5)**.
Dirancang untuk gaya SMC/ICT (Smart Money Concepts).

* **Bias (skor berbobot):** tren harian SMA5/SMA20 (+2), momentum M15 & M5
  EMA9/EMA21 (+2), struktur break-of-structure M15 (+3), displacement M5
  (body/range >0,6, +1). Bias `BUY`/`SELL`; imbang = `NETRAL` → **TUNGGU**.
* **Skenario naratif** (dari sweep/likuiditas/displacement di M5):
  | Skenario | Syarat | Arti |
  |---|---|---|
  | imbalan → likuiditas | displacement kuat tanpa sweep | harga menuju likuiditas swing terdekat |
  | likuiditas → imbalance | sweep high/low lalu close balik + displacement berlawanan | potensi reversal ke FVG/OB |
  | likuiditas → likuiditas | sweep tanpa displacement balikan | lanjut buru likuiditas searah sweep |
  | imbalance → imbalance | displacement beruntun tanpa sweep | tren kuat, bias mengikuti displacement |
* **Zona yang dipindai di M5:** order block bullish/bearish (bar berlawanan sebelum
  break), status `fresh`/`mitigated`, plus FVG bullish/bearish dan equal
  highs/lows (likuiditas) — semua dikembalikan di output dict.
* **Entry LIMIT:** di **OB M5 terdekat yang selaras bias** (OB `fresh` diprioritaskan);
  SL/TP tetap **50 / 100 / 150 pip** (sama seperti `/scalp`).
* **High-probability filter:** bila bias NETRAL atau tidak ada OB selaras → **TUNGGU**.
* Tool `get_gold_smc_analysis(sl_pips=None, tp1_pips=None, tp2_pips=None)`.

Contoh output (ringkas):
```
🧭 SMC XAUUSD (bias → skenario → OB)
💵 Spot: $4,323.10 | Bias HTF: SELL (4 vs 4)
➡️ Arah: BUY  |  Order: LIMIT
🎯 Entry limit: 4,309.10
🧱 OB: 4,309.10–4,317.69 (fresh)
📖 Skenario: likuiditas → imbalance
```

## Mode INTRADAY HTF→LTF (`/intraday`, `/intra`, `/daytrade`)
Prinsip **HTF bias, LTF area**: timeframe tinggi (H1, dengan filter tren D1)
menentukan **arah** — jangan melawan — lalu timeframe rendah (M15) dipindai
mencari **area entry (POI) terbaik** tempat harga kemungkinan besar memantul:
order block (OB), fair value gap (FVG/imbalance), atau equal lows/highs
(zona likuiditas). Entry = **LIMIT di zona POI**, bukan entry pasar.

* **Bias HTF:** skor berbobot dari EMA9/EMA21, RSI, momentum 3-bar, struktur
  BOS/CHOCH H1, displacement H1, plus tren harian (SMA5 vs SMA20).
  Bias `NETRAL` → **TUNGGU** (jangan entry).
* **POI LTF (M15):** dipilih otomatis — prioritas **OB fresh (3) > FVG (2) >
  zona likuiditas EQ (1)**; skor sama → zona terdekat dari spot.
  BUY dicari di bawah harga (discount), SELL di atas (premium).
* **SL/TP:** SL **80 pip**, TP1 **160 pip** (R:R 1:2), TP2 **240 pip** (R:R 1:3);
  berlaku **12 jam** (tahan satu sesi London/New York).
* **Filter:** skor confluence LTF 0–6 (OB fresh, FVG, EMA, RSI, momentum,
  displacement) + gate volatilitas ATR M15. Skor < `GOLD_INTRA_MIN_SCORE`
  (default 4) atau tidak ada POI valid → **TUNGGU** + alasan jelas.
  Bot tidak pernah mengklaim zona "pasti" didatangi — zona hanya probabilistik.

Contoh output (ringkas):
```
📈 INTRADAY XAUUSD (H1 bias → M15 zona)
💵 Spot: $4,311.40 | Bias H1: BUY (7 vs 2)
➡️ Arah: BUY | Order: LIMIT
🎯 Entry limit: 4,292.30 (19.10 poin dari spot)
📦 Zona POI: Order Block bullish 4,289.40–4,292.30 (fresh, 3.20x ATR)
🛑 SL: 4,232.30 (80 pip) | 🥇 TP1: 4,372.30 (160 pip, R:R 1:2.00)
```

## Mode SWING HTF→LTF (`/swing`)
Versi lambat dari intraday: **bias dari D1**, zona POI dipindai di **H1** —
cocok untuk posisi yang tahan **2–5 hari**.

* **Bias HTF:** analisa harian penuh (EMA/RSI/momentum/struktur/displacement D1).
* **POI LTF (H1):** pilihan zona sama (OB fresh > FVG > EQH/EQH likuiditas).
* **SL/TP:** SL **200 pip**, TP1 **400 pip** (R:R 1:2), TP2 **600 pip** (R:R 1:3);
  order berlaku **72 jam** (`GOLD_SWING_VALID_HOURS`).
* Filter & aturan TUNGGU identik dengan mode intraday.

## Setup
1. **Pasang dependensi**
   ```bash
   pip install -r requirements.txt
   ```
   *(Sudah tersedia `.venv` siap pakai — aktifkan dengan `.venv\Scripts\activate`.)*
2. **Buat bot Telegram** via [@BotFather](https://t.me/BotFather) → `/newbot` → salin token.
3. **Set token**
   ```bash
   copy .env.example .env      # Windows
   # lalu isi BOT_TOKEN=... di .env
   ```

## Menjalankan
```bash
# 1) Uji MCP server dulu (tanpa Telegram) — menampilkan harga + analisa XAUUSD
python test_server.py

# 2) Uji jalur bridge (tanpa Telegram) — memanggil tool via MCP client
python test_bridge.py

# 3) Uji handler bot tanpa token (Update palsu, termasuk jalur error)
python test_handlers.py

# 4) Cek validitas BOT_TOKEN sebelum jalan
python validate_token.py

# 5) (Opsional) ukur latensi tool MCP — lihat efek cache pada panggilan ke-2
python bench_tools.py

# 6) Jalankan bridge bot Telegram (long-polling)
python bot_telegram.py
```

Perintah di Telegram:
| Perintah | Fungsi |
|----------|--------|
| `/start` | Bantuan |
| `/harga` atau `/price` | Harga XAUUSD saat ini |
| `/signal` atau `/analisa` | Analisa price action + signal trading |
| `/scalp` atau `/scalping` | Scalping momentum M15: SL 50 pip, TP 100 pip, entry LIMIT + skor high-probability |
| `/m5`, `/scalp5`, atau `/scalping5` | Scalping momentum M5 (trigger lebih cepat) dengan zona limit dari M15 |
| `/smc` atau `/smct` | SMC/ICT: bias → skenario imbalan↔likuiditas → order block M5 |
| `/intraday`, `/intra`, atau `/daytrade` | Intraday: bias H1 → entry LIMIT di zona POI M15 (OB/FVG/EQL), SL 80 pip, tahan 1 sesi |
| `/swing` | Swing: bias D1 → entry LIMIT di zona POI H1 (OB/FVG/EQH), SL 200 pip, tahan 2–5 hari |
| teks apa pun | Dibalas harga XAUUSD |

## Deployment Railway

Konfigurasi deployment tersedia di folder `xauusd-bot/` (bot emas, bukan echo bot).
Jika memakai repository ini, set **Root Directory** layanan ke `/xauusd-bot`.
Konfigurasi saat ini memakai `Dockerfile` dan `railway.json`; perintah startup
adalah `python -u bot_telegram.py`.

- Set `BOT_TOKEN` melalui **Variables** Railway, bukan di Git atau chat.
- Set `BOT_MODE=polling` dan gunakan **satu replika**.
- Bot polling tidak membutuhkan domain publik atau HTTP health-check path.
- Hentikan instance lokal atau deployment lama dengan token yang sama sebelum
  mengaktifkan layanan untuk menghindari konflik `getUpdates`.
- Periksa log deployment, lalu uji `/start` dan `/harga` di Telegram.
- PC boleh dimatikan setelah bot berjalan di Railway. Ketersediaan tetap bergantung
  pada layanan, kuota, dan saldo akun; hosting gratis tanpa batas tidak dijamin.

Dokumentasi ini bukan konfirmasi bahwa deployment sudah berhasil.


## Mode deploy (webhook)
Selain long-polling, bot bisa jalan sebagai **webhook** (cocok untuk VPS/server):

```bash
# set di .env
BOT_MODE=webhook
WEBHOOK_URL=https://domain-anda.com   # HARUS HTTPS publik
WEBHOOK_PATH=telegram
WEBHOOK_PORT=8443
```
Lalu `python bot_telegram.py` → bot mendaftarkan webhook ke Telegram dan
mendengarkan di `https://domain-anda.com/telegram`.

## Variabel env (file `.env`)
| Var | Wajib | Default |
|-----|-------|---------|
| `BOT_TOKEN` | ✅ | – |
| `BOT_MODE` | ❌ | `polling` (`polling`/`webhook`) |
| `WEBHOOK_URL` | ✅ bila webhook | – |
| `WEBHOOK_PATH` | ❌ | `telegram` |
| `WEBHOOK_PORT` | ❌ | `8443` |
| `MCP_SERVER_CMD` |  | `sys.executable` |
| `MCP_SERVER_ARGS` | ❌ | `gold_mcp_server.py` |
| `MCP_PERSISTENT_SESSION` |  | `1` (sesi MCP dipakai ulang; `0` = spawn tiap pesan) |
| `MCP_CALL_TIMEOUT` | ❌ | `120` (timeout satu panggilan tool MCP, detik) |
| `BOT_BOOTSTRAP_RETRIES` | ❌ | `10` (retry saat connect pertama ke Telegram) |
| `BOT_MAX_RESTARTS` | ❌ | `5` (restart otomatis bila polling error) |
| `BOT_REQUEST_TIMEOUT` |  | `30` (timeout request ke Telegram, detik) |
| `GOLD_PRICE_CACHE_TTL` | ❌ | `30` (cache harga spot, detik; `0` = nonaktif) |
| `GOLD_HISTORY_CACHE_TTL` | ❌ | `600` (cache histori harian untuk analisa, detik) |
| `GOLD_INTRADAY_CACHE_TTL` | ❌ | `180` (cache histori M15 untuk scalping, detik) |
| `GOLD_M5_CACHE_TTL` | ❌ | `120` (cache histori M5 untuk trigger scalping cepat, detik) |
| `GOLD_PIP_VALUE` | ❌ | `1.0` (nilai 1 pip XAUUSD dalam poin harga) |
| `GOLD_SCALP_SL_PIPS` | ❌ | `50` (stop loss tetap, pip) |
| `GOLD_SCALP_TP1_PIPS` | ❌ | `100` (target utama, pip → R:R 1:2) |
| `GOLD_SCALP_TP2_PIPS` | ❌ | `150` (target runner, pip → R:R 1:3) |
| `GOLD_SCALP_MIN_SCORE` | ❌ | `4` (ambang skor confluence 0–6; naikkan = lebih selektif) |
| `GOLD_SCALP_VALID_HOURS` | ❌ | `4` (masa berlaku order limit, jam) |
| `GOLD_SCALP_M5_SL_PIPS` | ❌ | `5` (SL ketat mode M5, pip = 5.00 poin bila `GOLD_PIP_VALUE=1.0`) |
| `GOLD_SCALP_M5_TP1_PIPS` | ❌ | `10` (TP1 mode M5, pip → R:R 1:2) |
| `GOLD_SCALP_M5_TP2_PIPS` | ❌ | `15` (TP2 mode M5, pip → R:R 1:3) |
| `GOLD_SCALP_M5_MIN_SCORE` | ❌ | `4` (ambang skor confluence 0–6 mode M5) |
| `GOLD_SCALP_M5_VALID_HOURS` | ❌ | `1.5` (masa berlaku order limit M5, jam — scalping cepat) |
| `GOLD_INTRA_SL_PIPS` | ❌ | `80` (SL mode intraday, pip) |
| `GOLD_INTRA_TP1_PIPS` | ❌ | `160` (TP1 intraday, pip → R:R 1:2) |
| `GOLD_INTRA_TP2_PIPS` | ❌ | `240` (TP2 intraday, pip → R:R 1:3) |
| `GOLD_INTRA_MIN_SCORE` | ❌ | `4` (ambang skor confluence 0–6 intraday) |
| `GOLD_INTRA_VALID_HOURS` | ❌ | `12` (masa berlaku order intraday, jam — 1 sesi) |
| `GOLD_H1_CACHE_TTL` | ❌ | `300` (cache histori H1 untuk bias intraday, detik) |
| `GOLD_SWING_SL_PIPS` | ❌ | `200` (SL mode swing, pip) |
| `GOLD_SWING_TP1_PIPS` | ❌ | `400` (TP1 swing, pip → R:R 1:2) |
| `GOLD_SWING_TP2_PIPS` | ❌ | `600` (TP2 swing, pip → R:R 1:3) |
| `GOLD_SWING_MIN_SCORE` | ❌ | `4` (ambang skor confluence 0–6 swing) |
| `GOLD_SWING_VALID_HOURS` | ❌ | `72` (masa berlaku order swing, jam — 2–5 hari) |
| `GOLD_SMC_BIAS_W_HTF` | ❌ | `2` (bobot tren harian pada skor bias SMC) |
| `GOLD_SMC_BIAS_W_MOM` | ❌ | `2` (bobot momentum M15/M5) |
| `GOLD_SMC_BIAS_W_STRUCT` | ❌ | `3` (bobot struktur break-of-structure) |
| `GOLD_SMC_BIAS_W_DISP` | ❌ | `1` (bobot displacement) |

## Troubleshooting
| Gejala | Penyebab & solusi |
|--------|-------------------|
| `401 Unauthorized` saat start | `BOT_TOKEN` salah/revoked → perbaiki di `.env`, cek dengan `python validate_token.py` |
| `Can't parse entities: can't find end of the entity` | Pesan mengandung karakter Markdown (mis. `[Errno ...]`) sedangkan dikirim dengan `parse_mode="Markdown"`. Sudah ditangani: pesan error dikirim **tanpa** Markdown + `_safe_reply()` otomatis mengirim ulang sebagai teks biasa |
| `socket.gaierror [Errno 11001] getaddrinfo failed` / `ConnectTimeout` | DNS/jaringan flaky. Sudah ditangani: HTTP GET di MCP server di-retry 3× (backoff 1,5 dtk) + bot retry bootstrap `BOT_BOOTSTRAP_RETRIES` dan restart otomatis `BOT_MAX_RESTARTS` |
| Bot tiba-tiba berhenti (`Alive=False`) | Cek `bot.err.log`. Kalau hanya timeout jaringan, jalankan ulang — atau naikkan `BOT_REQUEST_TIMEOUT` |
| Harga/signal tidak berubah saat dites cepat | Cache TTL masih berlaku (harga 30 dtk, histori 10 menit). Turunkan `GOLD_PRICE_CACHE_TTL`/`GOLD_HISTORY_CACHE_TTL` atau set `0` untuk selalu ambil data baru |
| Balasan `⚠️ Gagal mengambil harga: ... getaddrinfo failed` | Sumber data (gold-api.com/Yahoo) tidak bisa di-resolve dari jaringan Anda. Sudah di-retry 3× + fallback `query2`; perbaiki DNS/koneksi lalu kirim ulang perintah |
| `/scalp` selalu menjawab TUNGGU | Itu perilaku filter: skor confluence < `GOLD_SCALP_MIN_SCORE` (4) atau gate volatilitas gagal. Turunkan `GOLD_SCALP_MIN_SCORE` (mis. `3`) agar lebih sering keluar sinyal, atau tunggu harga masuk zona momentum |
| `/m5` selalu menjawab TUNGGU | Sama seperti `/scalp`, tapi gate-nya ATR5: bila volatilitas M5 terlalu kecil (<0,02% harga) atau terlalu liar (>0,30%) entry dibatalkan. Turunkan `GOLD_SCALP_M5_MIN_SCORE` (mis. `3`) untuk lebih sering keluar sinyal |
| SL/TP scalping terasa terlalu kecil/besar | Sesuaikan definisi pip: `GOLD_PIP_VALUE` (default `1.0` poin). Broker dengan kuotasi 2 desimal biasanya memakai `0.1`. Atur juga `GOLD_SCALP_SL_PIPS`/`GOLD_SCALP_TP1_PIPS` |

Menjalankan bot di background (Windows):
```powershell
$p = Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList '-u','bot_telegram.py' `
     -WorkingDirectory "$PWD" -RedirectStandardOutput bot.out.log `
     -RedirectStandardError bot.err.log -PassThru
$p.Id | Out-File bot.pid      # simpan PID
Get-Content bot.out.log       # status + "📩 Update dari @user (chat ...): /signal" tiap pesan masuk
Stop-Process -Id (Get-Content bot.pid)   # hentikan bot
```

---
⚠️ Proyek ini untuk **edukasi**, bukan saran keuangan. Token bot bersifat rahasia — jangan di-share.