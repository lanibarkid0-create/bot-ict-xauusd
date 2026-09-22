"""Cek wiring: build Application beneran lalu pastikan semua command terdaftar."""
import asyncio
import logging
import os
import sys

logging.disable(logging.INFO)  # sunyikan log cache server saat simulasi
os.environ.setdefault("BOT_TOKEN", "dummy:token-for-test")
sys.stdout.reconfigure(encoding="utf-8")

import bot_telegram as b  # noqa: E402
import gold_mcp_server as g  # noqa: E402

ALIAS_MAP = {
    "m5": "m5", "scalp5": "m5", "scalping5": "m5", "scalping": "m5",
    "scalp": "m5", "signal": "m5", "analisa": "m5", "vibe": "m5",
    "intraday": "intraday", "intra": "intraday", "daytrade": "intraday",
    "smc": "intraday", "smct": "intraday", "fincept": "intraday",
    "swing": "swing", "hedge": "swing", "autohedge": "swing",
    "fusion": "swing", "fusi": "swing",
    "start": "start", "harga": "price", "price": "price",
}


class FakeMsg:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.parse_modes: list[str | None] = []

    async def reply_text(self, text, parse_mode=None, **_kw):
        self.sent.append(str(text))
        self.parse_modes.append(parse_mode)

    async def send_action(self, *_a, **_kw):
        pass

    @property
    def chat(self):
        return self


class FakeUpdate:
    def __init__(self) -> None:
        self.message = FakeMsg()


async def main() -> int:
    app = b._build_app()
    registered: dict[str, object] = {}
    for group in app.handlers.values():
        for h in group:
            cmd = getattr(h, "commands", None)
            if cmd:
                for c in cmd:
                    registered[c] = h.callback
    print(f"Command terdaftar: {len(registered)}")
    ok = True
    for name, expected in sorted(ALIAS_MAP.items()):
        cb = registered.get(name)
        if cb is None:
            ok = False
            print(f"  /{name:<10} HILANG!")
            continue
        target = {"cmd_m5": "m5", "cmd_intraday": "intraday",
                  "cmd_swing": "swing"}.get(cb.__name__)
        benar = (target == expected if expected in ("m5", "intraday", "swing")
                 else True)
        if not benar:
            ok = False
        print(f"  /{name:<10} -> {cb.__name__:<18} {'OK' if benar else 'SALAH PAKET'}")

    async def fake_call(tool, args=None):
        # Pakai server MCP sungguhan (via import langsung) supaya tervalidasi
        # end-to-end tanpa Telegram. Jaringan gagal -> pakai contoh statis.
        try:
            return g.get_gold_all_in_one_html((args or {}).get("mode", "m5"))
        except Exception as exc:  # noqa: BLE001
            return f"🧭 ALL-IN-ONE (contoh statis, jaringan gagal: {type(exc).__name__})\n\n" + "x" * 4200
    b._call_mcp = fake_call
    u = FakeUpdate()
    await b.reply_m5(u, None)
    n = len(u.message.sent)
    pendek = all(len(s) <= 4096 for s in u.message.sent)
    print(f"\nSimulasi /m5: {n} pesan, semua <=4096 char: {pendek}")
    if not pendek or n < 1 or not u.message.sent[0].strip():
        ok = False
    await b._shutdown_bridge(None)
    print("\nSEMUA OK" if ok else "\nADA MASALAH")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
