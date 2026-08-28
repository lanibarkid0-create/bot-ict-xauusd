"""Bot Telegram AI BEDAH CHART - ICT/SMC Analysis."""

import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from analysis import full_analysis, parse_user_input, SYMBOL_MAP, TF_MAP

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("ai-bedah-chart")

# Trial counter (in-memory, simple). Production bisa pakai DB.
TRIAL_COUNTER = {}  # user_id -> {"count": int, "first_use": date}


def get_trial_status(user_id: int) -> tuple[bool, str]:
    """Cek trial limit. Returns (allowed, message)."""
    from datetime import date, timedelta
    today = date.today()
    if user_id not in TRIAL_COUNTER:
        TRIAL_COUNTER[user_id] = {"count": 0, "first_use": today}
    info = TRIAL_COUNTER[user_id]
    # Reset setelah 7 hari
    if (today - info["first_use"]).days >= 7:
        info["count"] = 0
        info["first_use"] = today
    if info["count"] >= 3:
        days_left = 7 - (today - info["first_use"]).days
        return False, f"⛔ Trial gratis: 3x/hari. Habis. Reset dalam {days_left} hari. Upgrade VIP untuk unlimited → /register"
    info["count"] += 1
    used = info["count"]
    remaining = 3 - used
    return True, f"Trial {used}/3 hari ini · sisa {remaining} kali"


# ---------- Handlers ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sapaan + instruksi."""
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name or "trader"
    await update.message.reply_text(
        f"Halo {first_name}! 👋\n\n"
        "🩺 <b>AI BEDAH CHART</b> — analisa ICT/SMC otomatis\n\n"
        "Cara pakai: ketik <b>simbol + timeframe</b>\n"
        "Contoh:\n"
        "• <code>XAUUSD M5</code>\n"
        "• <code>GBPJPY M5</code>\n"
        "• <code>EURUSD H1</code>\n"
        "• <code>NAS100 M15</code>\n"
        "• <code>AUDJPY M5</code>\n"
        "• <code>XTIUSD H1</code>\n\n"
        "Bisa: XAU, semua pair FX, silver (XAG), oil (XTI), NAS100, US30. TF: M1 s/d D1\n\n"
        "🕒 Trial gratis: 7 hari · 3x/hari. Upgrade VIP untuk 10x/hari → /register",
        parse_mode="HTML",
    )


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Info upgrade VIP."""
    await update.message.reply_text(
        "💎 <b>VIP Akses</b>\n\n"
        "Trial gratis: 3 analisa/hari · masa aktif 7 hari\n"
        "VIP: 10 analisa/hari · unlimited masa aktif\n\n"
        "Harga: hubungi admin @admin\n\n"
        "<i>Ini analisa AI · alat bantu, BUKAN sinyal resmi analisa financial advice</i>",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bantuan."""
    help_text = "🩺 <b>AI BEDAH CHART</b>\n\n"
    help_text += "<b>Pair didukung:</b>\n"
    help_text += "• XAU, XAG (gold/silver)\n"
    help_text += "• Semua major & cross FX: EURUSD, GBPJPY, AUDJPY, dll\n"
    help_text += "• XTI (oil), XBR (brent)\n"
    help_text += "• NAS100, US30, SPX500, dll\n\n"
    help_text += "<b>Timeframe:</b> M1, M3, M5, M15, M30, H1, H2, H4, D1\n\n"
    help_text += "<b>Cara pakai:</b>\n"
    help_text += "Ketik: <code>SIMBOL TF</code>\n"
    help_text += "Contoh: <code>XAUUSD M5</code> atau <code>GBPJPY H1</code>\n\n"
    help_text += "<b>Perintah:</b>\n"
    help_text += "/start - sapaan\n"
    help_text += "/register - info VIP\n"
    help_text += "/help - bantuan ini"
    await update.message.reply_text(help_text, parse_mode="HTML")


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tentang bot."""
    await update.message.reply_text(
        "🩺 AI BEDAH CHART\n\n"
        "Versi 2.0 (remake)\n"
        "Engine: ICT/SMC analysis\n"
        "Data: Twelve Data\n"
        "Fitur: Struktur (CHoCH, BOS), FVG, Order Block, Liquidity, "
        "session filter, multi-TF bias (H1 internal)\n\n"
        "<i>Ini analisa AI · alat bantu, BUKAN sinyal resmi.</i>",
        parse_mode="HTML",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle natural input: 'XAUUSD M5', 'GBPJPY H1', dll."""
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Parse input
    parsed = parse_user_input(text)
    if not parsed:
        await update.message.reply_text(
            "❌ Format salah. Contoh yang benar:\n"
            "• <code>XAUUSD M5</code>\n"
            "• <code>GBPJPY H1</code>\n"
            "• <code>NAS100 M15</code>\n\n"
            "Atau /help untuk info lengkap.",
            parse_mode="HTML",
        )
        return

    symbol, tf = parsed

    # Cek trial limit
    allowed, trial_msg = get_trial_status(user_id)
    if not allowed:
        await update.message.reply_text(trial_msg, parse_mode=None)
        return

    # Kirim "sedang menganalisa"
    status_msg = await update.message.reply_text(
        f"⏳ Menganalisa {symbol} {tf}...\n({trial_msg})",
        parse_mode=None,
    )

    # Run analysis
    try:
        result_text = full_analysis(TWELVEDATA_API_KEY, symbol, tf)
        # Telegram max 4096 chars per message
        if len(result_text) > 4000:
            # Split jadi beberapa pesan
            chunks = []
            while result_text:
                if len(result_text) <= 4000:
                    chunks.append(result_text)
                    break
                # Cari newline terdekat
                cut = result_text.rfind("\n", 0, 4000)
                if cut == -1:
                    cut = 4000
                chunks.append(result_text[:cut])
                result_text = result_text[cut:].lstrip("\n")
            await status_msg.edit_text(chunks[0], parse_mode=None)
            for chunk in chunks[1:]:
                await update.message.reply_text(chunk, parse_mode=None)
        else:
            await status_msg.edit_text(result_text, parse_mode=None)
    except Exception as e:
        log.error(f"Analysis error: {e}")
        await status_msg.edit_text(f"❌ Error analisa: {e}", parse_mode=None)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error(f"Bot error: {context.error}")


# ---------- Main ----------

def main():
    if not TELEGRAM_BOT_TOKEN or not TWELVEDATA_API_KEY:
        raise SystemExit("Isi TELEGRAM_BOT_TOKEN dan TWELVEDATA_API_KEY di .env!")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))
    # Natural text input
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(on_error)

    log.info("AI BEDAH CHART bot berjalan...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
