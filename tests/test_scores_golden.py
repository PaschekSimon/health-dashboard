"""Golden test: every score on the synthetic data must stay exactly what it was.

tests/golden/scores.json holds all score_* tables and the fitted calibration
(scores_config.json) computed on tests/fixture_db.py. A refactor that changes
any number fails here, with a list of what moved.

When a change to the scores is intended, regenerate the file and commit it
with the change, so the diff shows what moved:

    UPDATE_GOLDEN=1 python -m pytest tests/test_scores_golden.py
"""

import json
import math
import os
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent / "golden" / "scores.json"
KEYS = {"score_strain_daily": ("date",), "score_strain_workout": ("log_id",), "score_sleep_daily": ("date",),
        "score_recovery_daily": ("date",), "score_healthspan": ("variant", "metric")}
REL_TOL = 1e-9   # only floating-point noise between platforms is allowed
SHOW = 25        # differences listed in a failure


def same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return False
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=1e-12)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


def differences(golden, actual):
    out = []
    for table, keys in KEYS.items():
        g = {tuple(r[k] for k in keys): r for r in golden["tables"].get(table, [])}
        a = {tuple(r[k] for k in keys): r for r in actual["tables"].get(table, [])}
        for key in sorted(g.keys() - a.keys()):
            out.append(f"{table} {key}: row missing")
        for key in sorted(a.keys() - g.keys()):
            out.append(f"{table} {key}: new row")
        for key in sorted(g.keys() & a.keys()):
            for col in sorted(g[key].keys() | a[key].keys()):
                old, new = g[key].get(col, "<no column>"), a[key].get(col, "<no column>")
                if not same(old, new):
                    out.append(f"{table} {key} {col}: {old!r} -> {new!r}")
    for section in sorted(golden["config"].keys() | actual["config"].keys()):
        old, new = golden["config"].get(section), actual["config"].get(section)
        if not same(old, new):
            out.append(f"scores_config.json {section}: {old!r} -> {new!r}")
    return out


def test_scores_match_golden(scored):
    actual = {"tables": scored["tables"], "config": scored["config"]}
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    assert GOLDEN.exists(), "no golden file yet: run with UPDATE_GOLDEN=1 once"
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    diff = differences(golden, actual)
    assert not diff, (f"{len(diff)} score values changed (UPDATE_GOLDEN=1 if intended):\n  "
                      + "\n  ".join(diff[:SHOW]) + ("\n  ..." if len(diff) > SHOW else ""))


def test_differences_reports_a_changed_value(scored):
    """The comparison itself catches a small change, a missing row and config drift."""
    golden = json.loads(json.dumps({"tables": scored["tables"], "config": scored["config"]}))
    changed = json.loads(json.dumps(golden))
    changed["tables"]["score_recovery_daily"][-1]["recovery"] += 0.1
    changed["tables"]["score_strain_daily"].pop()
    changed["config"]["strain"]["k"] *= 1.001
    diff = differences(golden, changed)
    assert any("score_recovery_daily" in d and "recovery:" in d for d in diff)
    assert any("score_strain_daily" in d and "row missing" in d for d in diff)
    assert any(d.startswith("scores_config.json strain") for d in diff)
    assert differences(golden, golden) == []
