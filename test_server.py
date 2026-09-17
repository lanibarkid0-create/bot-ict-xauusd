"""
Test end-to-end: panggil tool MCP `get_gold_price` via client.

Menjalankan gold_mcp_server.py sebagai subprocess stdio, lalu memanggil
tool lewat MCP client — persis seperti yang dilakukan bot_telegram.py.

    python test_server.py
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.stdout.reconfigure(encoding="utf-8")


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["gold_mcp_server.py"], env=None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Tools yang tersedia:", [t.name for t in tools.tools])

            res = await session.call_tool("get_gold_price", {})
            print("\n--- get_gold_price ---")
            print(res.content[0].text)

            res = await session.call_tool("get_gold_analysis", {})
            print("\n--- get_gold_analysis ---")
            print(res.content[0].text)

            res = await session.call_tool("get_gold_analysis_html", {})
            print("\n--- get_gold_analysis_html ---")
            print(res.content[0].text)

            res = await session.call_tool("get_gold_scalping_signal", {})
            print("\n--- get_gold_scalping_signal (scalping momentum M15) ---")
            print(res.content[0].text)

            res = await session.call_tool("get_gold_scalping_signal_html", {})
            print("\n--- get_gold_scalping_signal_html ---")
            print(res.content[0].text)

            res = await session.call_tool("get_gold_scalping_m5_signal", {})
            print("\n--- get_gold_scalping_m5_signal (trigger M5 + zona M15) ---")
            print(res.content[0].text)

            res = await session.call_tool("get_gold_scalping_m5_signal_html", {})
            print("\n--- get_gold_scalping_m5_signal_html ---")
            print(res.content[0].text)

            res = await session.call_tool("get_gold_smc_analysis", {})
            print("\n--- get_gold_smc_analysis (SMC/ICT: bias -> skenario -> OB) ---")
            print(res.content[0].text)

            res = await session.call_tool("get_gold_smc_analysis_html", {})
            print("\n--- get_gold_smc_analysis_html ---")
            print(res.content[0].text)

            # Cek cache intraday: dua panggilan scalp beruntun harus identik.
            s1 = json.loads(
                (await session.call_tool("get_gold_scalping_signal", {})).content[0].text
            )
            s2 = json.loads(
                (await session.call_tool("get_gold_scalping_signal", {})).content[0].text
            )
            sama_scalp = (s1["spot_price"], s1["entry_limit"]) == (s2["spot_price"], s2["entry_limit"])
            print("\n--- cek cache intraday (scalping) ---")
            print(
                f"{'OK (cache aktif)' if sama_scalp else 'GAGAL (data berubah)'}: "
                f"{s1['spot_price']} vs {s2['spot_price']}"
            )

            # Cek cache intraday M5+M15: dua panggilan scalping M5 beruntun harus identik.
            m1 = json.loads(
                (await session.call_tool("get_gold_scalping_m5_signal", {})).content[0].text
            )
            m2 = json.loads(
                (await session.call_tool("get_gold_scalping_m5_signal", {})).content[0].text
            )
            sama_scalp_m5 = (
                m1["spot_price"],
                m1["entry_limit"],
                m1["zona_m15"]["bawah"],
            ) == (
                m2["spot_price"],
                m2["entry_limit"],
                m2["zona_m15"]["bawah"],
            )
            print("\n--- cek cache intraday M5 + zona M15 (scalping M5) ---")
            print(
                f"{'OK (cache aktif)' if sama_scalp_m5 else 'GAGAL (data berubah)'}: "
                f"{m1['spot_price']} vs {m2['spot_price']} "
                f"(bias {m1['bias']}, skor {m1['probability_score']}, "
                f"vol_ok {m1['volatility_ok']})"
            )

            # Cek cache: dua panggilan beruntun harus mengembalikan harga identik.
            first = json.loads((await session.call_tool("get_gold_price", {})).content[0].text)
            second = json.loads((await session.call_tool("get_gold_price", {})).content[0].text)
            sama = first["price"] == second["price"]
            print("\n--- cek cache harga ---")
            print(f"{'OK (cache aktif)' if sama else 'GAGAL (harga berubah)'}: "
                  f"{first['price']} vs {second['price']}")


if __name__ == "__main__":
    asyncio.run(main())