"""命令行入口：调用 sag_api.maintenance.sag_maintenance_guard 中同一套逻辑。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sag_api.maintenance.sag_maintenance_guard import *  # noqa: F401,F403,E402
