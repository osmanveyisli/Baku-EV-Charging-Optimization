"""Command-line entry point for the Baku EV charging analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ev_placement.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Baku EV charger accessibility and site-selection analysis."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Redownload source data and rebuild the road graph instead of using caches.",
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Skip PDF/Markdown/HTML report generation.",
    )
    args = parser.parse_args()
    result = run_pipeline(
        PROJECT_ROOT, refresh=args.refresh, build_report=not args.skip_report
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

