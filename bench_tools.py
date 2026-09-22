"""Ukur latensi tool MCP gold-server (membuktikan efek cache TTL).

Jalankan: .\\.venv\\Scripts\\python.exe bench_tools.py
"""

import asyncio
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TOOLS = [
    "get_gold_price_html",
    "get_gold_analysis_html",
    "get_gold_scalping_signal_html",
    "get_gold_scalping_m5_signal_html",
    "get_gold_smc_analysis_html",
    "get_gold_intraday_signal_html",
    "get_gold_swing_signal_html",
]


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["gold_mcp_server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for tool in TOOLS:
                timings = []
                for attempt in (1, 2):
                    start = time.perf_counter()
                    result = await session.call_tool(tool, {})
                    elapsed = time.perf_counter() - start
                    timings.append(elapsed)
                    status = "ERROR" if result.is_error else "OK"
                    snippet = (result.content[0].text.splitlines() or [""])[0][:48]
                    print(f"{tool} panggilan {attempt}: {elapsed:6.2f}s  [{status}] {snippet}")
                print(f"  → percepatan panggilan ke-2: {timings[0] - timings[1]:+.2f}s\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
