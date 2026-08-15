#!/usr/bin/env python3
# Exact Stage 10 history-extension launcher.
# Credentials come only from the invoking process environment. This wrapper
# does not read dotenv files, accept a credential argument, or widen scope.

from __future__ import annotations

import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant_data.operations.stage10_history_extension import main


raise SystemExit(main(environment=os.environ))
