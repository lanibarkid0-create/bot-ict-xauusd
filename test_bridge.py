"""
Test jalur bridge (tanpa Telegram): memastikan bot_telegram._call_mcp dapat
memanggil tool di MCP server, teks harga kembali, sesi MCP dipakai ulang, dan
cache membuat panggilan berikutnya jauh lebih cepat.

    python test_bridge.py
"""

import asyncio
import os
import sys
import time

# Token dummy agar bot_telegram bisa di-import tanpa .env.
os.environ.setdefault("BOT_TOKEN", "dummy:token-for-test")

# Emoji harga butuh encoding UTF-8 (konsol Windows default cp1252).
sys.stdout.reconfigure(encoding="utf-8")

import bot_telegram  # noqa: E402

_checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    _checks.append((name, ok))
    print(f"{'✅' if ok else '❌'} {name}")


async def call_with_retry(tool: str, attempts: int = 3, delay: float = 5.0) -> str:
    """Panggil tool, ulangi bila gagal (jaringan di sini sering putus-nyambung)."""
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return await bot_telegram._call_mcp(tool)
        except Exception as exc:  # noqa: BLE001 - dicatat lalu coba lagi
            last = exc
            print(f"  (percobaan {i}/{attempts} gagal: {exc})")
            if i < attempts:
                await asyncio.sleep(delay)
    raise last  # type: ignore[misc]


async def main() -> None:
    start = time.perf_counter()
    text = await call_with_retry("get_gold_price_html")
    first = time.perf_counter() - start
    print("Hasil bridge (get_gold_price_html):")
    print(text)

    bridge = bot_telegram._bridge
    check("bridge dapat memanggil tool MCP (teks harga kembali)",
          "Gold" in text and "Harga" in text)
    check("sesi MCP persisten aktif (worker berjalan)", bool(bridge and bridge.running))

    start = time.perf_counter()
    text2 = await call_with_retry("get_gold_price_html")
    second = time.perf_counter() - start
    print(f"\nPanggilan 1: {first:.2f}s | panggilan 2 (sesi+cache): {second:.2f}s")
    check("panggilan ke-2 memakai sesi/cache yang sama (lebih cepat)",
          bot_telegram._bridge is bridge and second <= first)
    check("hasil panggilan ke-2 tetap valid", "Gold" in text2)

    # Tool yang tidak ada harus jadi exception (bukan teks mentah ke pengguna).
    try:
        await bot_telegram._call_mcp("tool_tidak_ada")
        check("error tool dijadikan exception (pesan ramah)", False)
    except Exception as exc:  # noqa: BLE001 - memang diharapkan
        print(f"\nError tool yang benar: {exc}")
        check("error tool dijadikan exception (pesan ramah)", True)
    check("sesi MCP tetap hidup setelah error tool", bool(bridge and bridge.running))

    # Tutup sesi MCP dengan bersih (seperti saat bot berhenti).
    await bot_telegram._shutdown_bridge(None)
    check("shutdown bridge menutup worker MCP", not (bridge and bridge.running))

    failed = [n for n, ok in _checks if not ok]
    print(f"\n=== {len(_checks) - len(failed)}/{len(_checks)} CEK LULUS ===")
    if failed:
        print("GAGAL: " + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())