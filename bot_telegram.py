"""
Bridge Telegram bot -@- MCP gold server
========================================
Bot Telegram yang memanggil MCP server `gold_mcp_server.py` (via MCP client
stdio) untuk menjawab pertanyaan harga emas XAUUSD.

Cara pakai:
    1. Set BOT_TOKEN di file `.env` (lihat .env.example).
    2. pip install -r requirements.txt
    3. python bot_telegram.py
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import os
import platform
from pathlib import Path
import sys
import time

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

# ------------------------------------------------------------------- konfig
# Windows: paksa stdout/stderr UTF-8 agar emoji di log (bot.out.log) tidak
# memicu UnicodeEncodeError 'charmap' saat console memakai codepage non-UTF8.
if sys.platform == "win32":  # pragma: no cover - spesifik Windows
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name, None)
        try:
            if _stream is not None and hasattr(_stream, "reconfigure"):
                _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001, S110 - best effort saja
            pass
    # Paksa UTF-8 ke subprocess MCP (gold_mcp_server.py) agar emoji di
    # log-nya tidak memicu UnicodeEncodeError 'charmap' juga.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")

load_dotenv()  # baca .env (BOT_TOKEN)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SERVER_CMD = os.getenv("MCP_SERVER_CMD", sys.executable)  # python yang dipakai
SERVER_ARGS = os.getenv("MCP_SERVER_ARGS", "gold_mcp_server.py")

# Mode jalan: "polling" (default, lokal) atau "webhook" (untuk deploy).
BOT_MODE = os.getenv("BOT_MODE", "polling").lower()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")          # mis. https://domain-anda.com
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "telegram").strip("/")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
# Ketahanan terhadap jaringan flaky: retry bootstrap & restart otomatis.
BOOTSTRAP_RETRIES = int(os.getenv("BOT_BOOTSTRAP_RETRIES", "10"))
MAX_RESTARTS = int(os.getenv("BOT_MAX_RESTARTS", "5"))
REQUEST_TIMEOUT = float(os.getenv("BOT_REQUEST_TIMEOUT", "30"))
# Bridge MCP: pakai satu sesi stdio yang dipakai ulang antar pesan, sehingga
# cache di MCP server tetap hidup (respons /signal jauh lebih cepat).
# Set MCP_PERSISTENT_SESSION=0 untuk kembali ke mode subprocess-per-panggilan.
PERSISTENT_SESSION = os.getenv("MCP_PERSISTENT_SESSION", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
MCP_CALL_TIMEOUT = float(os.getenv("MCP_CALL_TIMEOUT", "120"))

if not BOT_TOKEN:
    raise SystemExit(
        "BOT_TOKEN belum di-set. Salin .env.example jadi .env lalu isi token dari @BotFather."
    )

_lock_fh = None  # hanya diisi setelah lock berhasil diperoleh
_BASE_DIR = Path(__file__).resolve().parent
_LOCK_PATH = _BASE_DIR / "bot.lock"
_PID_PATH = _BASE_DIR / "bot.pid"


def _cleanup_lock() -> None:
    """Hapus PID milik sendiri sebelum melepas lock; sentinel tetap ada."""
    global _lock_fh
    if _lock_fh is None:
        return
    try:
        if _PID_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
            _PID_PATH.unlink()
    except OSError:
        pass
    # Jangan unlink bot.lock: proses baru mungkin sudah membuka inode/file ini.
    try:
        _lock_fh.seek(0)
        msvcrt.locking(_lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        _lock_fh.close()
        _lock_fh = None


# Exit normal saja. Saat force-kill, OS melepas lock; PID basi diganti saat start.
atexit.register(_cleanup_lock)


def _extract_text(result) -> str:
    """Ambil teks dari hasil call_tool (fallback: dump JSON)."""
    texts = [item.text for item in (result.content or []) if hasattr(item, "text")]
    if not texts:
        return json.dumps(result.content or result, indent=2, default=str)
    return "\n".join(texts)


def _tool_error(text: str, tool: str) -> str:
    """Rapikan pesan error tool MCP (buang prefix 'Error executing tool ...')."""
    prefix = f"Error executing tool {tool}: "
    return text[len(prefix):].strip() if text.startswith(prefix) else text


class _McpWorker:
    """Satu sesi MCP stdio yang dipakai ulang antar pesan Telegram.

    Sesi MCP harus dibuat dan ditutup di task yang sama (kebutuhan anyio), maka
    semua pemanggilan tool dialirkan lewat queue ke worker ini. Bila subprocess
    MCP mati, sesi dibuat ulang otomatis pada percobaan kedua.
    """

    def __init__(self) -> None:
        self._params = StdioServerParameters(
            command=SERVER_CMD,
            args=[SERVER_ARGS] if SERVER_ARGS else [],
            env=None,
        )
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def call(self, tool: str, args: dict | None = None) -> str:
        """Kirim permintaan tool ke worker lalu tunggu hasilnya."""
        if not self.running:
            self._queue = asyncio.Queue()
            self._task = asyncio.create_task(self._run(), name="mcp-worker")
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put((future, tool, args or {}))
        return await asyncio.wait_for(future, timeout=MCP_CALL_TIMEOUT)

    async def _run(self) -> None:
        stack = contextlib.AsyncExitStack()
        session: ClientSession | None = None
        try:
            while True:
                future, tool, args = await self._queue.get()
                if tool is None:  # sentinel: berhenti
                    break
                for attempt in (1, 2):
                    try:
                        if session is None:
                            read, write = await stack.enter_async_context(
                                stdio_client(self._params)
                            )
                            session = await stack.enter_async_context(ClientSession(read, write))
                            await session.initialize()
                        result = await session.call_tool(tool, args)
                        if getattr(result, "is_error", False):
                            # Error dari tool (mis. sumber data tak terjangkau) datang
                            # sebagai hasil is_error -> jadikan exception supaya handler
                            # bot membalas "⚠️ Gagal ..." yang ramah, bukan teks mentah.
                            if not future.done():
                                future.set_exception(
                                    RuntimeError(_tool_error(_extract_text(result), tool))
                                )
                            break
                        if not future.done():
                            future.set_result(_extract_text(result))
                        break
                    except Exception as exc:  # noqa: BLE001 - bangun ulang sesi, coba lagi
                        print(
                            f"⚠️ MCP {tool} gagal (percobaan {attempt}/2): {exc}",
                            file=sys.stderr,
                        )
                        with contextlib.suppress(Exception):
                            await stack.aclose()
                        stack = contextlib.AsyncExitStack()
                        session = None
                        if attempt == 2 and not future.done():
                            future.set_exception(exc)
        finally:
            # BaseException agar subprocess MCP tetap ditutup walau task dibatalkan.
            with contextlib.suppress(BaseException):
                await stack.aclose()

    async def stop(self) -> None:
        """Hentikan worker dan tutup sesi MCP."""
        if not self.running:
            return
        await self._queue.put((None, None, None))
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()


_bridge: _McpWorker | None = None


async def _call_mcp_once(tool: str, args: dict | None = None) -> str:
    """Mode lama: spawn MCP server baru untuk setiap panggilan."""
    params = StdioServerParameters(
        command=SERVER_CMD,
        args=[SERVER_ARGS] if SERVER_ARGS else [],
        env=None,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args or {})
    text = _extract_text(result)
    if getattr(result, "is_error", False):
        raise RuntimeError(_tool_error(text, tool))
    return text


async def _call_mcp(tool: str, args: dict | None = None) -> str:
    """Panggil tool di MCP server gold_mcp_server.py, kembalikan teks hasil."""
    global _bridge
    if not PERSISTENT_SESSION:
        return await _call_mcp_once(tool, args)
    if _bridge is None:
        _bridge = _McpWorker()
    return await _bridge.call(tool, args)


async def _shutdown_bridge(_app) -> None:
    """Tutup sesi MCP saat bot berhenti (hook post_shutdown)."""
    if _bridge is not None:
        await _bridge.stop()


async def _safe_reply(message, text: str, parse_mode: str | None = "Markdown") -> None:
    """Kirim balasan; kalau Telegram menolak formatting, kirim ulang sebagai teks biasa."""
    try:
        await message.reply_text(text, parse_mode=parse_mode)
    except BadRequest as exc:
        if "parse" in str(exc).lower() or "entit" in str(exc).lower():
            print(
                f"Format {parse_mode} ditolak Telegram, kirim sebagai teks biasa.",
                file=sys.stderr,
            )
            await message.reply_text(text)
        else:
            raise


async def reply_price(update: Update, _ctx) -> None:
    """Balas harga emas (via tool get_gold_price_html)."""
    try:
        text = await _call_mcp("get_gold_price_html")
    except Exception as exc:  # noqa: BLE001 - kirim error ke user
        # Pesan error sering mengandung karakter Markdown ([Errno ...]) -> kirim polos.
        await _safe_reply(update.message, f"⚠️ Gagal mengambil harga: {exc}", parse_mode=None)
        return
    await _safe_reply(update.message, text)


async def reply_signal(update: Update, _ctx) -> None:
    """Balas signal trading XAUUSD (via tool get_gold_analysis_html)."""
    await update.message.chat.send_action("typing")
    try:
        text = await _call_mcp("get_gold_analysis_html")
    except Exception as exc:  # noqa: BLE001 - kirim error ke user
        await _safe_reply(update.message, f"⚠️ Gagal menganalisa: {exc}", parse_mode=None)
        return
    await _safe_reply(update.message, text)


async def reply_scalp(update: Update, _ctx) -> None:
    """Balas sinyal scalping momentum XAUUSD (via tool get_gold_scalping_signal_html)."""
    await update.message.chat.send_action("typing")
    try:
        text = await _call_mcp("get_gold_scalping_signal_html")
    except Exception as exc:  # noqa: BLE001 - kirim error ke user
        await _safe_reply(update.message, f"⚠️ Gagal menganalisa scalp: {exc}", parse_mode=None)
        return
    await _safe_reply(update.message, text)


async def reply_scalp_m5(update: Update, _ctx) -> None:
    """Balas sinyal scalping M5 + zona M15 (via tool get_gold_scalping_m5_signal_html)."""
    await update.message.chat.send_action("typing")
    try:
        text = await _call_mcp("get_gold_scalping_m5_signal_html")
    except Exception as exc:  # noqa: BLE001 - kirim error ke user
        await _safe_reply(update.message, f"⚠️ Gagal menganalisa scalp M5: {exc}", parse_mode=None)
        return
    await _safe_reply(update.message, text)


async def reply_smc(update: Update, _ctx) -> None:
    """Balas analisa SMC/ICT XAUUSD (via tool get_gold_smc_analysis_html)."""
    await update.message.chat.send_action("typing")
    try:
        text = await _call_mcp("get_gold_smc_analysis_html")
    except Exception as exc:  # noqa: BLE001 - kirim error ke user
        await _safe_reply(update.message, f"⚠️ Gagal menganalisa SMC: {exc}", parse_mode=None)
        return
    await _safe_reply(update.message, text)


async def cmd_start(update: Update, _ctx) -> None:
    await update.message.reply_text(
        "🤖 Halo!\n"
        "• /harga — harga XAUUSD saat ini\n"
        "• /signal — analisa price action + signal trading (BUY/SELL, entry, SL, TP)\n"
        "• /scalp — scalping momentum M15: SL 50 pip, TP 100 pip, entry LIMIT + skor high-probability\n"
        "• /m5 — scalping momentum M5 (trigger lebih cepat) dengan zona M15\n"
        "• /smc — SMC/ICT: bias → skenario imbalan↔likuiditas → order block M5\n"
        "Atau cukup ketik 'harga emas'."
    )


async def cmd_price(update: Update, _ctx) -> None:
    await reply_price(update, _ctx)


async def cmd_signal(update: Update, _ctx) -> None:
    await reply_signal(update, _ctx)


async def cmd_smc(update: Update, _ctx) -> None:
    await reply_smc(update, _ctx)


async def cmd_scalp(update: Update, _ctx) -> None:
    await reply_scalp(update, _ctx)


async def cmd_scalp_m5(update: Update, _ctx) -> None:
    await reply_scalp_m5(update, _ctx)


async def on_error(_update, context) -> None:
    """Error handler: catat error agar exception tidak mematikan bot."""
    err = context.error
    msg = str(err)
    # Konflik getUpdates (409) = PASTI ada bot kedua dengan token sama.
    # Matikan polling instance ini agar tidak berebut update; user harus
    # mematikan bot ganda (lihat pesan di stderr) lalu start ulang satu saja.
    if "Conflict" in type(err).__name__ or "terminated by other getUpdates" in msg:
        try:
            print(
                "Konflik polling: token dipakai bot lain "
                "(terminated by other getUpdates). Matikan bot ganda lalu "
                "jalankan satu saja. Instance ini berhenti agar tidak berebut update.",
                file=sys.stderr,
            )
        except UnicodeEncodeError:
            print(
                "[konflik polling] token dipakai bot lain; "
                "matikan bot ganda lalu jalankan satu saja.",
                file=sys.stderr,
            )
        app = context.application
        if app is not None:
            app.stop_running()
        return
    try:
        print(f"Handler error: {err}", file=sys.stderr)
    except UnicodeEncodeError:
        print("Handler error: <non-ascii>", file=sys.stderr)


async def log_update(update: Update, _ctx) -> None:
    """Catat update yang masuk supaya bot mudah diverifikasi lewat bot.out.log."""
    user = update.effective_user
    siapa = f"@{user.username}" if user and user.username else getattr(user, "first_name", "?")
    chat = update.effective_chat.id if update.effective_chat else "?"
    pesan = ((update.effective_message.text if update.effective_message else "") or "").strip()
    # Hindari UnicodeEncodeError di console Windows (cp1252): tulis repr aman.
    pesan_log = pesan.encode("ascii", "backslashreplace").decode("ascii")
    siapa_log = str(siapa).encode("ascii", "backslashreplace").decode("ascii")
    print(f"[update] dari {siapa_log} (chat {chat}): {pesan_log or type(update.effective_message).__name__}")


def _ensure_single_instance() -> None:
    """Cegah dua bot polling bersamaan (penyebab konflik getUpdates/409).

    Mengunci file `bot.lock` secara eksklusif selama proses hidup dan menulis
    PID aktual ke `bot.pid` (menimpa file basi dari run sebelumnya). Bila file
    terkunci proses lain, langsung berhenti dengan pesan jelas — bukan
    men-start polling kedua yang berebut update Telegram.

    Harus dipanggil paling awal di main(), sebelum ApplicationBuilder agar
    tidak ada koneksi Telegram yang dibuka sia-sia.
    """
    global _lock_fh
    if _lock_fh is not None:
        return
    # Tanpa truncate dan selalu byte ke-0, tidak bergantung cwd/panjang PID.
    fh = _LOCK_PATH.open("a+b")
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        fh.close()
        raise SystemExit(
            f"Bot lain masih berjalan atau lock tidak tersedia: {_LOCK_PATH}. "
            "Jangan jalankan polling kedua; periksa proses pemilik terlebih dahulu."
        ) from exc
    _lock_fh = fh
    try:
        _PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        _cleanup_lock()
        raise


def _build_app() -> "object":
    """Bangun Application baru lengkap dengan semua handler.

    Harus dipanggil di dalam setiap retry loop — Setiap Application butuh
    event loop fresh; memakai kembali object yang sama setelah run_polling()
    berhenti justru nyebabkan 'RuntimeError: Event loop is closed'.
    """
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(REQUEST_TIMEOUT)
        .read_timeout(REQUEST_TIMEOUT)
        .write_timeout(REQUEST_TIMEOUT)
        .pool_timeout(REQUEST_TIMEOUT)
        .get_updates_connect_timeout(REQUEST_TIMEOUT)
        .get_updates_read_timeout(REQUEST_TIMEOUT)
        .post_shutdown(_shutdown_bridge)
        .build()
    )
    app.add_error_handler(on_error)
    app.add_handler(TypeHandler(Update, log_update), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("harga", cmd_price))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("analisa", cmd_signal))
    app.add_handler(CommandHandler("scalp", cmd_scalp))
    app.add_handler(CommandHandler("scalping", cmd_scalp))
    app.add_handler(CommandHandler("m5", cmd_scalp_m5))
    app.add_handler(CommandHandler("scalp5", cmd_scalp_m5))
    app.add_handler(CommandHandler("scalping5", cmd_scalp_m5))
    app.add_handler(CommandHandler("smc", cmd_smc))
    app.add_handler(CommandHandler("smct", cmd_smc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_price))
    return app


def main() -> None:
    _ensure_single_instance()
    if BOT_MODE == "webhook":
        app = _build_app()
        if not WEBHOOK_URL:
            raise SystemExit("BOT_MODE=webhook butuh WEBHOOK_URL (mis. https://domain-anda.com).")
        full_url = f"{WEBHOOK_URL.rstrip('/')}/{WEBHOOK_PATH}"
        print(f"Telegram bot berjalan (webhook) di {full_url} : port {WEBHOOK_PORT}")
        app.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=full_url,
            bootstrap_retries=BOOTSTRAP_RETRIES,
        )
    else:
        print("Telegram bot berjalan (long-polling)… Tekan Ctrl+C untuk stop.")
        for attempt in range(1, MAX_RESTARTS + 1):
            # Bangun Application baru setiap retry → event loop fresh, tidak
            # menyebabkan 'RuntimeError: Event loop is closed'.
            app = _build_app()
            try:
                app.run_polling(bootstrap_retries=BOOTSTRAP_RETRIES)
                break  # keluar normal (termasuk saat dihentikan user)
            except KeyboardInterrupt:
                print("Dihentikan oleh pengguna.")
                break
            except Exception as exc:  # noqa: BLE001 - restart saat error jaringan
                print(
                    f"⚠️ Polling berhenti (percobaan {attempt}/{MAX_RESTARTS}): {exc}",
                    file=sys.stderr,
                )
                # Lock tetap dipelihara — ini masih proses yang sama,
                # hanya Application yang dibangun ulang. atexit akan
                # melepasnya saat proses benar-benar keluar.
                if attempt == MAX_RESTARTS:
                    raise
                time.sleep(5)


if __name__ == "__main__":
    main()