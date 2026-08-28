# Bot ICT/SMC XAUUSD M5

Bot Telegram analisa forex XAUUSD menggunakan konsep ICT & SMC (Smart Money Concepts).

## Fitur
- Analisa ICT/SMC profesional: Market Structure, CHoCH, BOS, FVG, Order Blocks, Liquidity
- Bias utama otomatis dari H1
- Zona Entry/SL/TP presisi (~5 pips) dengan confidence level
- Multi-timeframe: M1, M5, M15, M30, H1, H4, D1
- Multi-pair: XAUUSD, US30
- Inline button menu interaktif

## Setup Lokal
```bash
pip install -r requirements.txt
cp .env.example .env
# Isi TELEGRAM_BOT_TOKEN dan TWELVEDATA_API_KEY di .env
python bot.py
```

## Deploy ke Railway.app (24/7 Gratis)

1. **Push ke GitHub**:
   ```bash
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/USERNAME/REPO.git
   git push -u origin main
   ```

2. **Buat project di Railway**:
   - Buka https://railway.app → Sign up dengan GitHub
   - Klik "New Project" → "Deploy from GitHub repo"
   - Pilih repository ini

3. **Set Environment Variables** di Railway dashboard:
   - `TELEGRAM_BOT_TOKEN` = token dari @BotFather
   - `TWELVEDATA_API_KEY` = API key dari twelvedata.com

4. **Tambah worker service** (karena bot bukan web server):
   - Settings → Deploy → Custom Start Command: `python bot.py`
   - Atau biarkan Railway detect dari Procfile (sudah ada)

5. **Tunggu deploy selesai** (~2-3 menit), lalu test di Telegram dengan `/start`

## Catatan Railway
- Free tier dapat $5 credit/bulan (cukup untuk bot ini)
- Bot jalan sebagai worker process (long polling)
- Auto-restart kalau crash
- Logs bisa dilihat di dashboard Railway
