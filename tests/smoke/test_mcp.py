r"""Talk to the health-app MCP server over stdio like Claude Desktop does: list
tools, call every read tool, add and delete a test annotation. Uses your real
data (HEALTH_DATA_DIR or data/).

    uv run python tests/smoke/test_mcp.py [--sync]
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from datetime import date, timedelta


def ago(days):
    """An ISO date `days` before today, so the tests follow the data."""
    return (date.today() - timedelta(days=days)).isoformat()

results = []


async def call(s, name, args, cond=lambda j: True):
    r = await s.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in r.content)
    data = getattr(r, "structured_content", None) or getattr(r, "structuredContent", None)
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]  # list results are wrapped
    if data is None:
        try:
            data = json.loads(text)
        except ValueError:
            data = text
    ok = not getattr(r, "is_error", getattr(r, "isError", False)) and cond(data)
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})")
    if not ok:
        print("   ", text[:600])
    return data


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "health_dashboard", "mcp"],
                                   env=dict(os.environ))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = {t.name for t in (await s.list_tools()).tools}
        print("tools:", ", ".join(sorted(tools)))
        need = {"get_day", "get_trend", "list_runs", "get_run", "list_annotations", "add_annotation",
                "explain_score", "sync_now"}
        results.append(("list_tools", need <= tools))
        anns = await call(s, "list_annotations", {"date_from": ago(53), "date_to": ago(4)},
                          lambda j: {a["kind"] for a in j} >= {"illness", "alcohol"})
        night = next((a["excludes_nights"][0] for a in anns if a["kind"] == "alcohol"
                      and a["status"] == "confirmed"), ago(20))  # the morning after drinking
        await call(s, "get_day", {"date": night},
                   lambda j: j["recovery"] and any(a["kind"] == "alcohol" for a in j["annotations"]))
        await call(s, "get_trend", {"metric": "hrv_ms", "date_from": ago(53), "date_to": ago(4)},
                   lambda j: j["summary"]["n"] > 10)
        runs = await call(s, "list_runs", {"date_from": ago(66), "date_to": ago(4)},
                          lambda j: len(j["runs"]) > 0)
        await call(s, "get_run", {"workout_id": runs["runs"][0]["id"]}, lambda j: "analysis" in j)
        for score in ("recovery", "sleep", "strain"):
            await call(s, "explain_score", {"date": ago(7), "score": score}, lambda j: "value" in j)
        a = await call(s, "add_annotation", {"kind": "note", "start": ago(4), "note": "MCP TEST",
                                             "attrs": {"test": True}},
                       lambda j: j["annotation"]["status"] == "suggested" and j["annotation"]["source"] == "ai"
                       and j["recompute"] is None)
        await call(s, "delete_annotation", {"annotation_id": a["annotation"]["id"]},
                   lambda j: j["deleted"]["note"] == "MCP TEST")
        await call(s, "list_annotations", {"date_from": ago(4), "date_to": ago(4), "kind": "note"},
                   lambda j: not any(x["note"] == "MCP TEST" for x in j))
        await call(s, "get_sync_status", {}, lambda j: "newest_data" in j)
        if "--sync" in sys.argv:
            await call(s, "sync_now", {"wait_seconds": 5},
                       lambda j: j["status"] in ("running", "ok", "partial", "busy"))
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
