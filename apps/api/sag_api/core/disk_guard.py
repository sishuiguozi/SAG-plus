"""SAG-OPT-802：磁盘分级保护。

按可用空间分级限制后台写入，防止磁盘耗尽损坏数据库：

| 剩余空间 | 行为 |
| --- | --- |
| < warn_gb (30) | 警告（维护脚本已拒绝自动 compaction） |
| < pause_aux_gb (20) | 暂停辅助向量写入（entity_vectors / event_entity_vectors） |
| < pause_vector_gb (10) | 暂停全部向量写入并告警 |
| < pause_ingest_gb (5) | 暂停新文档解析，保护数据库 |

检查结果按 ``disk_check_interval_seconds`` 缓存，避免每个请求都做磁盘 stat。
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sag_api.core.logging import get_logger

log = get_logger("disk_guard")

Level = str  # "ok" | "warn" | "pause_aux" | "pause_vector" | "pause_ingest"


@dataclass(frozen=True)
class DiskLevel:
    level: Level
    free_gb: float
    threshold_gb: float | None = None


def _existing_ancestor(path: str | Path) -> Path:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def free_gb(path: str | Path) -> float:
    return shutil.disk_usage(_existing_ancestor(path)).free / 1024**3


def protection_level(
    free: float,
    *,
    warn_gb: float,
    pause_aux_gb: float,
    pause_vector_gb: float,
    pause_ingest_gb: float,
) -> DiskLevel:
    if free < pause_ingest_gb:
        return DiskLevel("pause_ingest", free, pause_ingest_gb)
    if free < pause_vector_gb:
        return DiskLevel("pause_vector", free, pause_vector_gb)
    if free < pause_aux_gb:
        return DiskLevel("pause_aux", free, pause_aux_gb)
    if free < warn_gb:
        return DiskLevel("warn", free, warn_gb)
    return DiskLevel("ok", free, None)


class DiskGuard:
    """带缓存与线程安全的磁盘分级保护。"""

    def __init__(self, path: str | Path, settings: Any | None = None) -> None:
        self._path = str(path)
        self._settings = settings
        self._lock = threading.Lock()
        self._cached: DiskLevel | None = None
        self._checked_at = 0.0

    def _thresholds(self) -> dict[str, float]:
        if self._settings is not None and getattr(self._settings, "disk_guard_enabled", True):
            return {
                "warn_gb": getattr(self._settings, "disk_warn_gb", 30.0),
                "pause_aux_gb": getattr(self._settings, "disk_pause_aux_gb", 20.0),
                "pause_vector_gb": getattr(self._settings, "disk_pause_vector_gb", 10.0),
                "pause_ingest_gb": getattr(self._settings, "disk_pause_ingest_gb", 5.0),
            }
        return {"warn_gb": 0.0, "pause_aux_gb": 0.0, "pause_vector_gb": 0.0, "pause_ingest_gb": 0.0}

    def _interval(self) -> float:
        if self._settings is not None:
            return float(getattr(self._settings, "disk_check_interval_seconds", 300) or 300)
        return 300.0

    def current(self, *, force: bool = False) -> DiskLevel:
        now = time.monotonic()
        with self._lock:
            if (
                not force
                and self._cached is not None
                and (now - self._checked_at) < self._interval()
            ):
                return self._cached
            try:
                free = free_gb(self._path)
            except OSError as error:  # noqa: BLE001
                log.warning("磁盘空间探测失败，按 ok 处理：%s", error)
                self._cached = DiskLevel("ok", 0.0, None)
                self._checked_at = now
                return self._cached
            thresholds = self._thresholds()
            level = protection_level(free, **thresholds)
            if level.level != "ok":
                log.warning(
                    "磁盘保护：free=%.1fGB level=%s threshold=%.1fGB",
                    level.free_gb, level.level, level.threshold_gb,
                )
            self._cached = level
            self._checked_at = now
            return level

    def allow_aux(self) -> bool:
        return self.current().level not in ("pause_aux", "pause_vector", "pause_ingest")

    def allow_vector(self) -> bool:
        return self.current().level not in ("pause_vector", "pause_ingest")

    def allow_ingest(self) -> bool:
        return self.current().level != "pause_ingest"

    def warn_level(self) -> bool:
        return self.current().level == "warn"
