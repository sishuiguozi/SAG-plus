"""命令行入口：调用 sag_api.maintenance 中同一套维护逻辑。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sag_api.maintenance.optimize_lancedb_table import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run())
