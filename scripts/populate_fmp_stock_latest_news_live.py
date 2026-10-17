#!/usr/bin/env python3
"""Run only the exact, manually authorized FMP stock-latest news command."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant_data.operations.fmp_stock_latest_news_backfill import main


if __name__ == "__main__":
    raise SystemExit(main())
