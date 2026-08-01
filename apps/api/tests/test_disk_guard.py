"""SAG-OPT-802：磁盘分级保护测试。

- protection_level 分级正确。
- DiskGuard 缓存与 allow_* 门禁。
"""

from __future__ import annotations

from pathlib import Path

from sag_api.core.disk_guard import DiskGuard, protection_level


class _FakeSettings:
    disk_guard_enabled = True
    disk_warn_gb = 30.0
    disk_pause_aux_gb = 20.0
    disk_pause_vector_gb = 10.0
    disk_pause_ingest_gb = 5.0
    disk_check_interval_seconds = 300


def test_protection_level_thresholds():
    cfg = dict(warn_gb=30, pause_aux_gb=20, pause_vector_gb=10, pause_ingest_gb=5)
    assert protection_level(50, **cfg).level == "ok"
    assert protection_level(25, **cfg).level == "warn"
    assert protection_level(15, **cfg).level == "pause_aux"
    assert protection_level(8, **cfg).level == "pause_vector"
    assert protection_level(3, **cfg).level == "pause_ingest"
    # 边界：严格小于阈值才降级（恰等于时维持上一级）
    assert protection_level(20, **cfg).level == "warn"
    assert protection_level(19.9, **cfg).level == "pause_aux"


def test_disk_guard_caches_and_allows(tmp_path: Path):
    class AlwaysOk:
        disk_guard_enabled = True
        disk_warn_gb = 0.0
        disk_pause_aux_gb = 0.0
        disk_pause_vector_gb = 0.0
        disk_pause_ingest_gb = 0.0
        disk_check_interval_seconds = 300

    guard = DiskGuard(tmp_path, AlwaysOk())
    first = guard.current(force=True)
    second = guard.current()
    assert first.level == "ok"
    assert second is first  # 缓存复用同一对象
    assert guard.allow_aux() is True
    assert guard.allow_vector() is True
    assert guard.allow_ingest() is True


def test_disk_guard_disabled_settings(tmp_path: Path):
    class Disabled:
        disk_guard_enabled = False
        disk_warn_gb = 30.0
        disk_pause_aux_gb = 20.0
        disk_pause_vector_gb = 10.0
        disk_pause_ingest_gb = 5.0
        disk_check_interval_seconds = 60

    guard = DiskGuard(tmp_path, Disabled())
    # 关闭时阈值归零 → 恒为 ok
    assert guard.current(force=True).level == "ok"
    assert guard.allow_ingest() is True
