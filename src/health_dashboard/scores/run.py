"""Run all WHOOP-style scores: Strain, Recovery + Sleep Performance, Healthspan.
Spec: docs/scores.md. Results go to the score_* tables in health.db;
frozen calibration (Strain k, Recovery weights) lives in scores_config.json.

    python -m health_dashboard scores
        [--recalibrate]  re-fit Strain k on the current history
        [--retune]       re-decide the Recovery weights against Google Readiness
        [--full]         rebuild the raw-data cache (scores/cache.py) from scratch
                         instead of only the days whose data changed, and
                         recompute the whole daily Healthspan history
"""

import argparse

from ..context import Context
from . import common as sc
from . import healthspan, recovery, strain


def run(ctx, recalibrate=False, retune=False, full=False):
    c = sc.cache(ctx, full=full)
    print(f"cache: {c['rebuilt'] or 'no raw data'} rebuild, days refreshed {c['days']}")
    strain.compute(ctx, recalibrate=recalibrate)
    print()
    recovery.compute(ctx, retune=retune)
    print()
    healthspan.compute(ctx)
    print(f"healthspan history: {healthspan.backfill(ctx, redo=full)} days computed")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--recalibrate", action="store_true", help="re-fit Strain k on the current history")
    ap.add_argument("--retune", action="store_true", help="re-decide the Recovery weights against Google Readiness")
    ap.add_argument("--full", action="store_true", help="rebuild the raw-data cache from scratch")
    args = ap.parse_args(argv)
    with Context.open() as ctx:
        run(ctx, recalibrate=args.recalibrate, retune=args.retune, full=args.full)


if __name__ == "__main__":
    main()
