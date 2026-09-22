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

# Lock mekanisme cross-platform:
#   Windows -> msvcrt.locking (1 byte offset 0)
#   Linux/Mac -> fcntl.flock (exclusive, non-blocking)
# Jika platform tidak mendukung locking (jarang), lewati saja — tidak fatal.
if sys.platform == "win32":
    import msvcrt  # type: ignore[import-not-found]  # noqa: UP031
    _HAS_FCNTL = False
    _LOCK_EX = msvcrt.LK_NBLCK
    _LOCK_UN = msvcrt.LK_UNLCK
else:
    try:
        import fcntl  # type: ignore[import-not-found]  # noqa: UP031
        _HAS_FCNTL = True
        _LOCK_EX = fcntl.LOCK_EX | fcntl.LOCK_NB
        _LOCK_UN = fcntl.LOCK_UN
    except ImportError:  # platform tanpa fcntl (mis. beberapa embedded)
        fcntl = None  # type: ignore[assignment]
        _HAS_FCNTL = False
        _LOCK_EX = None
        _LOCK_UN = None


def _lock_acquire(fh) -> bool:
    """Coba lock eksklusif 1-byte di offset 0. True bila berhasil."""
    try:
        if sys.platform == "win32":
            fh.seek(0)
            msvcrt.locking(fh.fileno(), _LOCK_EX, 1)
            return True
        if _HAS_FCNTL and fcntl is not None:
            fcntl.flock(fh.fileno(), _LOCK_EX)
            return True
    except OSError:
        return False
    return False


def _lock_release(fh) -> None:
    """Lepas lock (harmless bila tidak ter-lock)."""
    try:
        if sys.platform == "win32":
            fh.seek(0)
            msvcrt.locking(fh.fileno(), _LOCK_UN, 1)
        elif _HAS_FCNTL and fcntl is not None:
            fcntl.flock(fh.fileno(), _LOCK_UN)
    except OSError:
        pass


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
        _lock_release(_lock_fh)
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


async def reply_intraday(update: Update, _ctx) -> None:
    """Balas sinyal INTRADAY (H1 bias -> zona M15)."""
    await update.message.chat.send_action("typing")
    try:
        text = await _call_mcp("get_gold_intraday_signal_html")
    except Exception as exc:  # noqa: BLE001 - kirim error ke user
        await _safe_reply(update.message, f"⚠️ Gagal menganalisa intraday: {exc}", parse_mode=None)
        return
    await _safe_reply(update.message, text)


async def reply_swing(update: Update, _ctx) -> None:
    """Balas sinyal SWING (D1 bias -> zona H1)."""
    await update.message.chat.send_action("typing")
    try:
        text = await _call_mcp("get_gold_swing_signal_html")
    except Exception as exc:  # noqa: BLE001 - kirim error ke user
        await _safe_reply(update.message, f"⚠️ Gagal menganalisa swing: {exc}", parse_mode=None)
        return
    await _safe_reply(update.message, text)


async def _reply_tool(update: Update, tool: str, err_label: str) -> None:
    """Helper generik: panggil tool HTML -> balas (dipakai 3 engine + fusion)."""
    await update.message.chat.send_action("typing")
    try:
        text = await _call_mcp(tool)
    except Exception as exc:  # noqa: BLE001
        await _safe_reply(update.message, f"⚠️ Gagal {err_label}: {exc}", parse_mode=None)
        return
    await _safe_reply(update.message, text)


async def reply_all(update: Update, mode: str) -> None:
    """ALL-IN-ONE: 1 command -> semua engine (zona FUSION + core + SMC + 3 repo).

    Hasil panjang dikirim per-seksi (chunk <=3800 char) agar tidak kena limit
    pesan Telegram 4096 char.
    """
    await update.message.chat.send_action("typing")
    try:
        text = await _call_mcp("get_gold_all_in_one_html", {"mode": mode})
    except Exception as exc:  # noqa: BLE001
        await _safe_reply(update.message, f"⚠️ Gagal analisa all-in-one: {exc}", parse_mode=None)
        return
    for chunk in _split_chunks(text):
        await _safe_reply(update.message, chunk)


def _split_chunks(text: str, limit: int = 3800) -> list[str]:
    """Pecah teks jadi beberapa pesan di batas seksi (baris kosong)."""
    parts: list[str] = []
    cur = ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 <= limit:
            cur = f"{cur}\n\n{block}" if cur else block
            continue
        if cur:
            parts.append(cur)
            cur = ""
        while len(block) > limit:
            parts.append(block[:limit])
            block = block[limit:]
        cur = block
    if cur:
        parts.append(cur)
    return parts or [""]


async def reply_m5(update: Update, _ctx) -> None:
    """/m5 (dan /signal, /scalp, /vibe, dst): paket scalping lengkap."""
    await reply_all(update, "m5")


async def reply_intraday_all(update: Update, _ctx) -> None:
    """/intraday (dan /smc, /fincept, dst): paket intraday lengkap."""
    await reply_all(update, "intraday")


async def reply_swing_all(update: Update, _ctx) -> None:
    """/swing (dan /hedge, /fusion, dst): paket swing lengkap."""
    await reply_all(update, "swing")


async def cmd_start(update: Update, _ctx) -> None:
    await update.message.reply_text(
        "🤖 Halo! Analisa XAUUSD gabungan 3 engine (Vibe-Trading, Fincept, AutoHedge + SMC).\n\n"
        "⚡ /m5 — SCALPING lengkap: zona konfluensi 3 repo + trigger M5 + momentum M15\n"
        " + vibe/fincept/hedge (gabungan /signal, /scalp, /vibe)\n\n"
        "📈 /intraday — INTRADAY lengkap: zona konfluensi + H1→M15 (OB/FVG/sweep) + SMC\n"
        " + vibe/fincept/hedge (gabungan /smc)\n\n"
        "🌊 /swing — SWING lengkap: zona konfluensi + D1→H1 + SMC + vibe/fincept/hedge\n\n"
        "💵 /harga — harga XAUUSD saat ini\n\n"
        "Alias lama masih jalan: /signal /analisa /scalp /scalping /scalp5 /smc /smct\n"
        "/intra /daytrade /fincept /vibe /hedge /autohedge /fusion /fusi\n"
        "Atau cukup ketik 'harga emas'."
    )


async def cmd_price(update: Update, _ctx) -> None:
    await reply_price(update, _ctx)


async def cmd_m5(update: Update, _ctx) -> None:
    """/m5 dan aliasnya (signal/scalp/vibe/...) -> paket scalping lengkap."""
    await reply_m5(update, _ctx)


async def cmd_intraday(update: Update, _ctx) -> None:
    """/intraday dan aliasnya (smc/fincept/...) -> paket intraday lengkap."""
    await reply_intraday_all(update, _ctx)


async def cmd_swing(update: Update, _ctx) -> None:
    """/swing dan aliasnya (hedge/fusion/...) -> paket swing lengkap."""
    await reply_swing_all(update, _ctx)


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
    # Buka a+b (bukan w) agar tidak truncate file yang sedang dikunci proses lain.
    fh = _LOCK_PATH.open("a+b")
    if not _lock_acquire(fh):
        fh.close()
        raise SystemExit(
            f"Bot lain masih berjalan atau lock tidak tersedia: {_LOCK_PATH}. "
            "Jangan jalankan polling kedua; periksa proses pemilik terlebih dahulu."
        )
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
    # Paket lengkap: /m5 (scalping), /intraday, /swing — semua alias lama
    # diarahkan ke paket yang sesuai, jadi cukup 3 pilihan utama.
    for _alias in ("m5", "scalp5", "scalping5", "scalping", "scalp", "signal",
                   "analisa", "vibe"):
        app.add_handler(CommandHandler(_alias, cmd_m5))
    for _alias in ("intraday", "intra", "daytrade", "smc", "smct", "fincept"):
        app.add_handler(CommandHandler(_alias, cmd_intraday))
    for _alias in ("swing", "hedge", "autohedge", "fusion", "fusi"):
        app.add_handler(CommandHandler(_alias, cmd_swing))
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