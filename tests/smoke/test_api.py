r"""Hit every endpoint of a running backend (`health serve` first). Uses your
real data.

    uv run python tests/smoke/test_api.py [--sync] [--recompute]
--sync       also run a real sync (reason=button) and an immediate reason=open
--recompute  add a confirmed test alcohol annotation, wait for the recompute,
             check the night got excluded, delete it and recompute again
"""

import sqlite3
import sys
import time
from health_dashboard.config import get_paths

import httpx
from datetime import date, timedelta


def ago(days):
    """An ISO date `days` before today, so the tests follow the data."""
    return (date.today() - timedelta(days=days)).isoformat()

BASE = "http://127.0.0.1:8765"
c = httpx.Client(base_url=BASE, timeout=60)
results = []


def check(name, resp, expect=200, cond=None):
    ok = resp.status_code == expect and (cond is None or cond(resp.json()))
    results.append((name, ok, resp.status_code))
    print(f"{'PASS' if ok else 'FAIL'} {resp.status_code} {name}")
    if not ok:
        print("   ", resp.text[:500])
    return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None


def wait_run(run_id, limit=1800):
    t0 = time.time()
    while time.time() - t0 < limit:
        r = c.get(f"/sync/{run_id}").json()
        if r["status"] not in ("running", "queued"):
            return r
        time.sleep(5)
    return r


check("GET /status", c.get("/status"), cond=lambda j: "newest_data" in j)
d = check("GET /dashboard", c.get("/dashboard"), cond=lambda j: "recovery" in j)
d = check("GET /dashboard?date=...", c.get("/dashboard", params={"date": ago(20)}),
          cond=lambda j: j["recovery"] and any(a["kind"] == "alcohol" for a in j["annotations"]))
check("GET /recovery", c.get("/recovery", params={"from": ago(29), "to": ago(14)}),
      cond=lambda j: len(j["days"]) > 10 and any(x["annotation_ids"] for x in j["days"]))
check("GET /sleep", c.get("/sleep", params={"from": ago(23), "to": ago(7)}),
      cond=lambda j: len(j["nights"]) > 10)
runs = check("GET /runs", c.get("/runs", params={"from": ago(66), "to": ago(4)}),
             cond=lambda j: len(j["runs"]) > 0)
rid = runs["runs"][-1]["id"]
check("GET /runs/{id}", c.get(f"/runs/{rid}"), cond=lambda j: j["analysis"]["type"] == "RUNNING")
check("GET /runs/{bad}", c.get("/runs/nope"), expect=404)
check("GET /trend/recovery", c.get("/trend/recovery", params={"from": ago(53), "to": ago(4)}),
      cond=lambda j: j["summary"]["n"] > 10)
check("GET /trend/bad", c.get("/trend/bogus"), expect=400)
for s in ("recovery", "sleep", "strain"):
    check(f"GET /explain/{s}", c.get(f"/explain/{s}", params={"date": ago(7)}),
          cond=lambda j: "value" in j)
check("GET /annotations", c.get("/annotations", params={"from": ago(23), "to": ago(19)}),
      cond=lambda j: {a["kind"] for a in j} >= {"illness", "alcohol"})
check("GET /annotations?kind=alcohol", c.get("/annotations", params={"kind": "alcohol"}),
      cond=lambda j: all(a["kind"] == "alcohol" for a in j) and len(j) >= 1)

# annotation lifecycle with a kind that doesn't change scores (no recompute)
check("POST /annotations bad kind", c.post("/annotations", json={"kind": "party", "start": ago(5)}),
      expect=400)
a = check("POST /annotations", c.post("/annotations", json={
    "kind": "travel", "start": ago(6), "end": ago(5), "attrs": {"where": "TEST"},
    "source": "ai", "confidence": 0.4, "note": "TEST annotation"}), expect=201,
    cond=lambda j: j["annotation"]["status"] == "suggested" and j["recompute"] is None)
aid = a["annotation"]["id"]
check("POST /annotations/{id}/confirm", c.post(f"/annotations/{aid}/confirm"),
      cond=lambda j: j["annotation"]["status"] == "confirmed" and j["recompute"] is None)
check("POST /annotations/{id}/reject", c.post(f"/annotations/{aid}/reject"),
      cond=lambda j: j["annotation"]["status"] == "rejected")
check("DELETE /annotations/{id}", c.delete(f"/annotations/{aid}"), cond=lambda j: j["annotation"]["id"] == aid)
check("DELETE /annotations/{id} again", c.delete(f"/annotations/{aid}"), expect=404)

if "--recompute" in sys.argv:
    db = get_paths().health_db
    excl = lambda n: sqlite3.connect(db).execute(
        "SELECT excluded_event FROM analysis_daily WHERE night=?", (n,)).fetchone()[0]
    print(f"night {ago(13)} excluded before:", excl(ago(13)))
    a = check("POST confirmed alcohol (TEST)", c.post("/annotations", json={
        "kind": "alcohol", "start": ago(14), "status": "confirmed", "note": "TEST, delete me"}),
        expect=201, cond=lambda j: j["recompute"] is not None)
    r = wait_run(a["recompute"]["id"])
    print("   recompute", r["id"], r["status"], [(s["name"], s["status"]) for s in r["steps"]])
    results.append(("recompute after confirm", r["status"] == "ok" and excl(ago(13)) == 1, 0))
    print(f"night {ago(13)} excluded after confirm:", excl(ago(13)))
    d = check("DELETE TEST alcohol", c.delete(f"/annotations/{a['annotation']['id']}"),
              cond=lambda j: j["recompute"] is not None)
    r = wait_run(d["recompute"]["id"])
    print("   recompute", r["id"], r["status"])
    results.append(("recompute after delete", r["status"] == "ok" and excl(ago(13)) == 0, 0))

if "--sync" in sys.argv:
    s = check("POST /sync button", c.post("/sync", json={"reason": "button"}),
              cond=lambda j: j["status"] in ("running", "busy"))
    if s["status"] == "running":
        r = wait_run(s["id"])
        print("   sync", r["id"], r["status"], r["error"])
        for st in r["steps"]:
            print(f"     {st['name']:15} {st['status']:7} {st.get('seconds')} s")
        results.append(("sync button finished", r["status"] == "ok", 0))
    o = check("POST /sync open (right after)", c.post("/sync", json={"reason": "open"}),
              cond=lambda j: j["status"] == "skipped")
    print("   ", o["error"])
    check("GET /status after sync", c.get("/status"), cond=lambda j: j["recent_runs"])

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
