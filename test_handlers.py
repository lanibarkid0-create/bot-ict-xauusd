"""
Test handler bot TANPA Telegram/token.

Memanggil langsung handler reply_price, reply_signal, reply_scalp, alur
tombol inline (/start -> zona TF -> gaya -> sinyal MTF) dengan Update
palsu, sehingga membuktikan alur: handler → MCP tool → teks balasan.

    python test_handlers.py
"""

import asyncio
import contextlib
import io
import json
import os
import sys
from datetime import datetime

os.environ.setdefault("BOT_TOKEN", "dummy:token-for-test")
sys.stdout.reconfigure(encoding="utf-8")

import bot_telegram  # noqa: E402
import unified_analysis as ua  # noqa: E402
from telegram import Chat, Message, User  # noqa: E402
from telegram import Update as TgUpdate  # noqa: E402
from telegram.error import BadRequest  # noqa: E402


class FakeMessage:
    """Tiruan Update.message yang menangkap teks + parse_mode + keyboard."""

    def __init__(self, fail_parse: bool = False) -> None:
        self.sent: list[str] = []
        self.parse_modes: list[str | None] = []
        self.markups: list = []
        # Bila True, panggilan dengan parse_mode akan ditolak seperti Telegram.
        self.fail_parse = fail_parse

    async def reply_text(self, text: str, parse_mode: str | None = None,
                         reply_markup=None, **_kw) -> None:
        if self.fail_parse and parse_mode:
            raise BadRequest(
                "Can't parse entities: can't find end of the entity "
                "starting at byte offset 35"
            )
        self.sent.append(text)
        self.parse_modes.append(parse_mode)
        self.markups.append(reply_markup)

    async def send_action(self, *_a, **_kw) -> None:
        pass

    @property
    def chat(self) -> "FakeMessage":
        return self


class FakeCallbackQuery:
    """Tiruan CallbackQuery: answer + edit_message_text terekam di memori."""

    def __init__(self, data: str, message: "FakeMessage | None" = None) -> None:
        self.data = data
        self.message = message if message is not None else FakeMessage()
        self.answered: list[str | None] = []
        self.edits: list[str] = []
        self.edit_markups: list = []

    async def answer(self, text: str | None = None, **_kw) -> None:
        self.answered.append(text)

    async def edit_message_text(self, text: str, parse_mode: str | None = None,
                                reply_markup=None, **_kw) -> None:
        self.edits.append(text)
        self.edit_markups.append(reply_markup)


class FakeUpdate:
    def __init__(self, fail_parse: bool = False, callback_query=None) -> None:
        self.message = FakeMessage(fail_parse=fail_parse)
        self.callback_query = callback_query


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

    # 5d) Engine 3-repo dipanggil lewat _reply_tool generik (mock _call_mcp).
    _orig_call = bot_telegram._call_mcp

    async def _fake_call(tool: str, _args: dict | None = None) -> str:
        return f"<{tool} OK>"

    bot_telegram._call_mcp = _fake_call
    try:
        for tool in [
            "get_gold_vibe_analysis_html",
            "get_gold_fincept_analytics_html",
            "get_gold_autohedge_plan_html",
            "get_gold_fusion_signal_html",
        ]:
            u = FakeUpdate()
            await bot_telegram._reply_tool(u, tool, "uji engine")
            ok = (
                bool(u.message.sent)
                and tool in u.message.sent[-1]
                and u.message.parse_modes[-1] == "Markdown"
            )
            check(f"_reply_tool {tool} + Markdown", ok)
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

    # 9) Alur interaktif: /start -> tombol zona TF -> tombol gaya -> sinyal MTF.
    peta = {z: ua.ZONE_BIAS_BY_TF[z] for z in ua.ZONE_TF_ORDER}
    check("peta cadangan bot sinkron dengan unified_analysis.ZONE_BIAS_BY_TF",
          dict(bot_telegram._PETA_FALLBACK) == peta)
    gaya_bot = {k: (ik, lb, j) for k, ik, lb, j in bot_telegram._GAYA_FALLBACK}
    check("gaya cadangan bot sinkron dengan unified_analysis.STYLE_PRESETS",
          gaya_bot == {
              k: (ua.STYLE_PRESETS[k]["icon"], ua.STYLE_PRESETS[k]["label"],
                  ua.STYLE_PRESETS[k]["valid_hours"])
              for k in ua.STYLE_ORDER})

    menu_json = json.dumps({
        "timeframes": [
            {"kode": z, "bias": b, "peta": f"{z} → {b}"} for z, b in peta.items()],
        "styles": [
            {"kode": k, "ikon": ua.STYLE_PRESETS[k]["icon"],
             "label": ua.STYLE_PRESETS[k]["label"],
             "deskripsi": ua.STYLE_PRESETS[k]["deskripsi"],
             "valid_hours": ua.STYLE_PRESETS[k]["valid_hours"]}
            for k in ua.STYLE_ORDER],
    })
    _panggilan: list[tuple[str, dict]] = []

    async def _fake_mtf(tool: str, args: dict | None = None) -> str:
        _panggilan.append((tool, args or {}))
        if tool == "get_gold_timeframes":
            return menu_json
        if tool == "get_gold_mtf_signal_html":
            return (f"*SINYAL {args['zone_tf']} {args['style']}*\n\n"
                    "zona & SL/TP contoh untuk pengujian")
        raise RuntimeError(f"tool tak dikenal: {tool}")

    _orig9 = bot_telegram._call_mcp
    bot_telegram._call_mcp = _fake_mtf
    try:
        # /start -> grid 9 zona TF (label M1 <- M15, callback tf:<TF>).
        u = FakeUpdate()
        await bot_telegram.cmd_start(u, None)
        kb = u.message.markups[-1]
        datar = [b.callback_data for row in kb.inline_keyboard for b in row]
        check("/start: 9 tombol zona TF sesuai peta + Markdown",
              datar == [f"tf:{z}" for z in peta]
              and len(kb.inline_keyboard) == 3
              and u.message.parse_modes[-1] == "Markdown"
              and any(b.text == "M1 ← M15"
                      for row in kb.inline_keyboard for b in row))

        # klik zona M5 -> teks zona+bias & 3 tombol gaya + tombol kembali.
        cq = FakeCallbackQuery("tf:M5")
        await bot_telegram.on_callback(FakeUpdate(callback_query=cq), None)
        gaya_kb = cq.edit_markups[-1]
        gaya_datar = [b.callback_data for row in gaya_kb.inline_keyboard for b in row]
        check("klik zona M5 -> 3 tombol gaya + kembali + teks bias H1",
              gaya_datar == ["sty:M5:scalping", "sty:M5:intraday",
                             "sty:M5:swing", "menu"]
              and "ZONA M5" in cq.edits[-1] and "H1" in cq.edits[-1])

        # klik gaya -> get_gold_mtf_signal_html(zone_tf, style) + laporan terkirim.
        cq2 = FakeCallbackQuery("sty:M5:intraday")
        await bot_telegram.on_callback(FakeUpdate(callback_query=cq2), None)
        mtf = [a for t, a in _panggilan if t == "get_gold_mtf_signal_html"]
        laporan = cq2.message.sent[-1] if cq2.message.sent else ""
        ulang_kb = cq2.message.markups[-1]
        ulang_datar = [b.callback_data for row in ulang_kb.inline_keyboard for b in row]
        check("klik gaya -> sinyal MTF terkirim + tombol ulangi/ganti",
              mtf == [{"zone_tf": "M5", "style": "intraday"}]
              and "SINYAL M5 intraday" in laporan
              and ulang_datar == ["sty:M5:intraday", "menu"]
              and bool(cq2.answered))

        # tombol kembali -> grid zona lagi.
        cq3 = FakeCallbackQuery("menu")
        await bot_telegram.on_callback(FakeUpdate(callback_query=cq3), None)
        balik = [b.callback_data
                 for row in cq3.edit_markups[-1].inline_keyboard for b in row]
        check("tombol kembali -> menu 9 zona TF lagi",
              balik == [f"tf:{z}" for z in peta])

        # pilihan tidak valid -> kembali ke menu, tidak crash.
        cq4 = FakeCallbackQuery("sty:M99:scalping")
        await bot_telegram.on_callback(FakeUpdate(callback_query=cq4), None)
        check("zona tak dikenal -> kembali ke menu zona",
              cq4.edit_markups[-1] is not None
              and len(cq4.edit_markups[-1].inline_keyboard) == 3)
    finally:
        bot_telegram._call_mcp = _orig9

    # 9b) Jalur gagal: server mati -> pesan jelas + tombol gaya utk coba lagi.
    async def _gagal_mtf(tool: str, args: dict | None = None) -> str:
        raise RuntimeError("jaringan mati [Errno 11001]")

    bot_telegram._call_mcp = _gagal_mtf
    try:
        cq5 = FakeCallbackQuery("sty:H1:swing")
        await bot_telegram.on_callback(FakeUpdate(callback_query=cq5), None)
        kb_err = cq5.edit_markups[-1]
        retry = [b.callback_data for row in kb_err.inline_keyboard for b in row]
        check("sinyal gagal -> pesan Gagal + tombol gaya utk coba lagi",
              "Gagal" in cq5.edits[-1]
              and retry == ["sty:H1:scalping", "sty:H1:intraday",
                            "sty:H1:swing", "menu"])
    finally:
        bot_telegram._call_mcp = _orig9

    failed = [n for n, ok in _results if not ok]
    print(f"\n=== {len(_results) - len(failed)}/{len(_results)} CEK LULUS ===")
    if failed:
        print("GAGAL: " + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())