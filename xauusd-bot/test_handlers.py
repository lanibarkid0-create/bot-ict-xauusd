"""
Test handler bot TANPA Telegram/token.

Memanggil langsung handler reply_price, reply_signal & reply_scalp dengan Update
palsu, sehingga membuktikan alur: handler → MCP tool → teks balasan.

    python test_handlers.py
"""

import asyncio
import contextlib
import io
import os
import sys
from datetime import datetime

os.environ.setdefault("BOT_TOKEN", "dummy:token-for-test")
sys.stdout.reconfigure(encoding="utf-8")

import bot_telegram  # noqa: E402
from telegram import Chat, Message, User  # noqa: E402
from telegram import Update as TgUpdate  # noqa: E402
from telegram.error import BadRequest  # noqa: E402


class FakeMessage:
    """Tiruan Update.message yang menangkap teks + parse_mode balasan."""

    def __init__(self, fail_parse: bool = False) -> None:
        self.sent: list[str] = []
        self.parse_modes: list[str | None] = []
        # Bila True, panggilan dengan parse_mode akan ditolak seperti Telegram.
        self.fail_parse = fail_parse

    async def reply_text(self, text: str, parse_mode: str | None = None, **_kw) -> None:
        if self.fail_parse and parse_mode:
            raise BadRequest(
                "Can't parse entities: can't find end of the entity "
                "starting at byte offset 35"
            )
        self.sent.append(text)
        self.parse_modes.append(parse_mode)

    async def send_action(self, *_a, **_kw) -> None:
        pass

    @property
    def chat(self) -> "FakeMessage":
        return self


class FakeUpdate:
    def __init__(self, fail_parse: bool = False) -> None:
        self.message = FakeMessage(fail_parse=fail_parse)


_results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    _results.append((name, ok))
    print(f"{'✅' if ok else '❌'} {name}")


async def main() -> None:
    # 1) Alur normal: harga.
    u = FakeUpdate()
    await bot_telegram.reply_price(u, None)
    print("--- reply_price (via get_gold_price_html) ---")
    print(u.message.sent[-1])
    check("reply_price kirim teks + Markdown",
          bool(u.message.sent) and u.message.parse_modes[-1] == "Markdown")

    # 2) Alur normal: signal.
    u = FakeUpdate()
    await bot_telegram.reply_signal(u, None)
    print("\n--- reply_signal (via get_gold_analysis_html) ---")
    print(u.message.sent[-1])
    check("reply_signal kirim teks + Markdown",
          bool(u.message.sent) and u.message.parse_modes[-1] == "Markdown")

    # 3) Alur normal: scalping momentum (SL 50 pip / TP 100 pip + limit order).
    u = FakeUpdate()
    await bot_telegram.reply_scalp(u, None)
    print("\n--- reply_scalp (via get_gold_scalping_signal_html) ---")
    print(u.message.sent[-1])
    scalp_ok = (
        bool(u.message.sent)
        and u.message.parse_modes[-1] == "Markdown"
        and "Scalping XAUUSD" in u.message.sent[-1]
        and "pip" in u.message.sent[-1]
    )
    check("reply_scalp kirim teks scalping + Markdown", scalp_ok)

    # 4) Alur normal: scalping M5 (trigger M5 + zona M15).
    u = FakeUpdate()
    await bot_telegram.reply_scalp_m5(u, None)
    print("\n--- reply_scalp_m5 (via get_gold_scalping_m5_signal_html) ---")
    print(u.message.sent[-1])
    scalp_m5_ok = (
        bool(u.message.sent)
        and u.message.parse_modes[-1] == "Markdown"
        and "M5" in u.message.sent[-1]
        and "Zona M15" in u.message.sent[-1]
    )
    check("reply_scalp_m5 kirim teks M5 + zona M15", scalp_m5_ok)

    # 5) Alur normal: SMC/ICT (bias -> skenario -> order block M5).
    u = FakeUpdate()
    await bot_telegram.reply_smc(u, None)
    print("\n--- reply_smc (via get_gold_smc_analysis_html) ---")
    print(u.message.sent[-1])
    smc_ok = (
        bool(u.message.sent)
        and u.message.parse_modes[-1] == "Markdown"
        and "SMC XAUUSD" in u.message.sent[-1]
        and "Skenario" in u.message.sent[-1]
    )
    check("reply_smc kirim teks SMC + skenario + Markdown", smc_ok)

    # 5b) Alur normal: intraday (bias H1 -> zona POI M15).
    u = FakeUpdate()
    await bot_telegram.reply_intraday(u, None)
    print("\n--- reply_intraday (via get_gold_intraday_signal_html) ---")
    print(u.message.sent[-1])
    intra_ok = (
        bool(u.message.sent)
        and u.message.parse_modes[-1] == "Markdown"
        and "INTRADAY XAUUSD" in u.message.sent[-1]
        and "Zona POI" in u.message.sent[-1]
    )
    check("reply_intraday kirim teks INTRADAY + zona POI + Markdown", intra_ok)

    # 5c) Alur normal: swing (bias D1 -> zona POI H1).
    u = FakeUpdate()
    await bot_telegram.reply_swing(u, None)
    print("\n--- reply_swing (via get_gold_swing_signal_html) ---")
    print(u.message.sent[-1])
    swing_ok = (
        bool(u.message.sent)
        and u.message.parse_modes[-1] == "Markdown"
        and "SWING XAUUSD" in u.message.sent[-1]
        and "Zona POI" in u.message.sent[-1]
    )
    check("reply_swing kirim teks SWING + zona POI + Markdown", swing_ok)

    # 5d) Engine baru 3-repo: vibe / fincept / hedge / fusion (mock _call_mcp).
    _orig_call = bot_telegram._call_mcp

    async def _fake_call(tool: str, _args: dict | None = None) -> str:
        return f"<{tool} OK>"

    bot_telegram._call_mcp = _fake_call
    try:
        for fn_name, tool, key in [
            ("reply_vibe", "get_gold_vibe_analysis_html", "VIBE"),
            ("reply_fincept", "get_gold_fincept_analytics_html", "FINCEPT"),
            ("reply_hedge", "get_gold_autohedge_plan_html", "HEDGE"),
            ("reply_fusion", "get_gold_fusion_signal_html", "FUSION"),
        ]:
            u = FakeUpdate()
            await getattr(bot_telegram, fn_name)(u, None)
            ok = bool(u.message.sent) and tool in u.message.sent[-1]
            check(f"{fn_name} panggil {tool} + Markdown", ok)
    finally:
        bot_telegram._call_mcp = _orig_call

    # 6) Jalur error: pesan error berisi '[Errno ...]' harus dikirim POLOS.
    original = bot_telegram._call_mcp

    async def boom(*_a, **_kw):
        raise RuntimeError(
            "Gagal menghubungi api.gold-api.com: HTTPSConnectionPool "
            "([Errno 11001] getaddrinfo failed)"
        )

    bot_telegram._call_mcp = boom
    try:
        u = FakeUpdate()
        await bot_telegram.reply_price(u, None)
        print("\n--- reply_price (jalur error) ---")
        print(u.message.sent[-1])
        check("error dikirim tanpa Markdown (anti BadRequest)",
              bool(u.message.sent) and u.message.parse_modes[-1] is None)

        # 7) Fallback _safe_reply saat Telegram tetap menolak Markdown.
        u = FakeUpdate(fail_parse=True)
        await bot_telegram._safe_reply(u.message, "🥇 *Gold* [test]")
        check("_safe_reply fallback ke teks biasa",
              len(u.message.sent) == 1 and u.message.parse_modes[-1] is None)
    finally:
        bot_telegram._call_mcp = original

    # 8) log_update: update yang masuk tercatat jelas (untuk cek bot.out.log).
    fake = TgUpdate(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=987654, type="private"),
            from_user=User(id=1, first_name="Tester", is_bot=False, username="tester"),
            text="/harga",
        ),
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        await bot_telegram.log_update(fake, None)
    logged = buf.getvalue().strip()
    print("\n--- log_update ---")
    print(logged)
    check("log_update mencatat pengirim, chat, dan pesan",
          "@tester" in logged and "987654" in logged and "/harga" in logged)

    # Tutup sesi MCP (subprocess) agar tidak ada proses menggantung.
    await bot_telegram._shutdown_bridge(None)

    # 7) on_error: konflik getUpdates (bot ganda) -> stop polling, bukan crash.
    from telegram.error import Conflict  # noqa: E402

    class FakeApp:
        def __init__(self) -> None:
            self.stopped = False

        def stop_running(self) -> None:
            self.stopped = True

    class FakeCtx:
        def __init__(self, error, application) -> None:
            self.error = error
            self.application = application

    fake_app = FakeApp()
    await bot_telegram.on_error(
        None, FakeCtx(Conflict("terminated by other getUpdates"), fake_app)
    )
    check("on_error konflik getUpdates -> stop polling", fake_app.stopped)

    # 8) on_error: error biasa -> hanya dicatat, polling tetap jalan.
    fake_app2 = FakeApp()
    err_buf = io.StringIO()
    with contextlib.redirect_stderr(err_buf):
        await bot_telegram.on_error(
            None, FakeCtx(RuntimeError("boom biasa"), fake_app2)
        )
    check("on_error biasa -> tidak stop polling",
          not fake_app2.stopped and "boom biasa" in err_buf.getvalue())

    failed = [n for n, ok in _results if not ok]
    print(f"\n=== {len(_results) - len(failed)}/{len(_results)} CEK LULUS ===")
    if failed:
        print("GAGAL: " + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())