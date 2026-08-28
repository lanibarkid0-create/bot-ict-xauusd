"""Bot Telegram ICT/SMC Analisa XAUUSD dengan inline buttons."""

import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

from analysis import full_analysis, fetch_candles, SYMBOL, TIMEFRAME

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("ict-bot")

# --- Keyboard Definitions ---

MAIN_KEYBOARD = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("📊 Analisa Sekarang", callback_data="analisa_default")],
        [InlineKeyboardButton("📉 Pilih Pair", callback_data="pick_pair")],
        [InlineKeyboardButton("⏱️ Pilih Timeframe", callback_data="pick_timeframe")],
        [InlineKeyboardButton("ℹ️ Tentang", callback_data="about")],
    ]
)

PAIR_KEYBOARD = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("XAUUSD M5", callback_data="pair_XAUUSD_M5")],
        [InlineKeyboardButton("US30 M5", callback_data="pair_US30_M5")],
        [InlineKeyboardButton("XAUUSD M15", callback_data="pair_XAUUSD_M15")],
        [InlineKeyboardButton("← Kembali", callback_data="back_to_main")],
    ]
)

TIMEFRAME_KEYBOARD = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("M1", callback_data="tf_M1")],
        [InlineKeyboardButton("M5", callback_data="tf_M5")],
        [InlineKeyboardButton("M15", callback_data="tf_M15")],
        [InlineKeyboardButton("M30", callback_data="tf_M30")],
        [InlineKeyboardButton("H1", callback_data="tf_H1")],
        [InlineKeyboardButton("H4", callback_data="tf_H4")],
        [InlineKeyboardButton("D1", callback_data="tf_D1")],
        [InlineKeyboardButton("← Kembali", callback_data="back_to_main")],
    ]
)

AVAILABLE_PAIRS = {
    "XAUUSD_M5": ("XAU/USD", "5min"),
    "XAUUSD_M15": ("XAU/USD", "15min"),
    "XAUUSD_M30": ("XAU/USD", "30min"),
    "XAUUSD_H1": ("XAU/USD", "1hour"),
    "XAUUSD_H4": ("XAU/USD", "4hour"),
    "XAUUSD_D1": ("XAU/USD", "1day"),
    "US30_M5": ("US30", "5min"),
    "US30_H1": ("US30", "1hour"),
}


# --- Handler Functions ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tampilkan menu utama dengan tombol inline."""
    await update.message.reply_text(
        "👋 <b>Selamat Datang di Bot ICT/SMC</b>\n\n"
        "Pilih opsi di bawah ini:",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="HTML",
    )


async def cmd_analisa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Jalankan analisa default (XAUUSD M5)."""
    await update.message.reply_text("⏳ Sedang menganalisa XAUUSD M5...")
    try:
        text = full_analysis(TWELVEDATA_API_KEY, symbol="XAU/USD", interval="5min")
    except Exception as e:
        text = f"❌ Error: {e}"
    await update.message.reply_text(text, parse_mode=None)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle semua inline button callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data == "analisa_default":
        await query.edit_message_text("⏳ Sedang menganalisa XAUUSD M5...")
        try:
            text = full_analysis(TWELVEDATA_API_KEY, symbol="XAU/USD", interval="5min")
        except Exception as e:
            text = f"❌ Error: {e}"
        await query.message.reply_text(text, parse_mode=None)

    elif data == "pick_pair":
        await query.edit_message_text("Pilih pair:", reply_markup=PAIR_KEYBOARD)

    elif data == "pick_timeframe":
        await query.edit_message_text("Pilih timeframe:", reply_markup=TIMEFRAME_KEYBOARD)

    elif data.startswith("pair_"):
        key = data[len("pair_"):]
        if key in AVAILABLE_PAIRS:
            symbol, interval = AVAILABLE_PAIRS[key]
            await query.edit_message_text(f"⏳ Menganalisa {symbol} {interval}...")
            try:
                text = full_analysis(TWELVEDATA_API_KEY, symbol=symbol, interval=interval)
            except Exception as e:
                text = f"❌ Error: {e}"
            await query.message.reply_text(text, parse_mode=None)
        else:
            await query.message.reply_text("Pair tidak didukung.")

    elif data.startswith("tf_"):
        tf_key = data[len("tf_"):]
        # Simpan timeframe ke context.user_data untuk dipakai bersama pair
        context.user_data["selected_tf"] = tf_key
        # Tampilkan pasangan pair setelah timeframe dipilih
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("XAUUSD", callback_data="pair_XAUUSD_" + tf_key.replace("tf_", ""))],
                [InlineKeyboardButton("US30", callback_data="pair_US30_" + tf_key.replace("tf_", ""))],
                [InlineKeyboardButton("← Kembali", callback_data="pick_timeframe")],
            ]
        )
        await query.edit_message_text("Pilih pair setelah timeframe:", reply_markup=kb)

    elif data == "back_to_main":
        await query.edit_message_text("Menu utama:", reply_markup=MAIN_KEYBOARD)

    elif data == "about":
        await query.edit_message_text(
            "Bot ICT/SMC Analisa\n"
            "Sumber data: Twelve Data\n"
            "Analisa: Structure, CHoCH, FVG, OB, Liquidity\n"
            "Gunakan /analisa untuk cepat",
            parse_mode=None,
        )


# --- Main ---

def main():
    if not TELEGRAM_BOT_TOKEN or not TWELVEDATA_API_KEY:
        raise SystemExit("Isi TELEGRAM_BOT_TOKEN dan TWELVEDATA_API_KEY di .env!")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("analisa", cmd_analisa))
    app.add_handler(CallbackQueryHandler(button_callback))

    log.info("Bot ICT/SMC berjalan...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()