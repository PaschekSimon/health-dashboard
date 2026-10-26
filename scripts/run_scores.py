r"""Run all WHOOP-style scores: Strain, Recovery + Sleep Performance, Healthspan.
Spec: docs/scores.md. Results go to the score_* tables in health.db;
frozen calibration (Strain k, Recovery weights) lives in scores_config.json.

    %APPDATA%\uv\tools\google-health-mcp\Scripts\python.exe -I run_scores.py
        [--recalibrate]  re-fit Strain k on the current history
        [--retune]       re-decide the Recovery weights against Google Readiness
        [--full]         rebuild the raw-data cache (score_cache.py) from scratch
                         instead of only the days whose data changed
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir
import healthspan
import recovery
import scores_common as sc
import strain

if __name__ == "__main__":
    h, m = sc.connect()
    sc.FULL = "--full" in sys.argv
    c = sc.cache(h)
    print(f"cache: {c['rebuilt'] or 'no raw data'} rebuild, days refreshed {c['days']}")
    strain.compute(h, m, recalibrate="--recalibrate" in sys.argv)
    print()
    recovery.compute(h, m, retune="--retune" in sys.argv)
    print()
    healthspan.compute(h, m)
