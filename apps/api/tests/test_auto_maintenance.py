"""SAG-OPT-803：自动维护调度触发逻辑测试。

- 碎片/版本增量/占用比触发条件。
- 队列非空闲时判定为 busy（不执行维护）。
- 状态回填后版本增量按差值计算。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path


def _load_script_module(module_name: str, relative_path: str):
    script_path = Path(__file__).resolve().parents[1] / relative_path
    scripts_dir = str(script_path.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evaluate_triggers_by_fragments_and_ratio(tmp_path: Path):
    auto = _load_script_module("auto_maintenance_test", "sag_api/maintenance/auto_maintenance.py")
    summary = {
        "tables": {
            "event_vectors": {"rows": 10, "fragments": 800, "latest_version": 100,
                              "directory_bytes": 1000, "active_total_bytes": 100},
            "source_chunks": {"rows": 10, "fragments": 3, "latest_version": 100,
                              "directory_bytes": 3000, "active_total_bytes": 100},
            "entity_vectors": {"rows": 10, "fragments": 3, "latest_version": 100,
                               "directory_bytes": 1000, "active_total_bytes": 1000},
        }
    }
    triggers = auto._evaluate_triggers(
        summary, {"tables": {}},
        min_fragments=500, min_version_delta=500, max_ratio=2.5, force=False,
    )
    names = {t["table"] for t in triggers}
    assert names == {"event_vectors", "source_chunks"}
    reasons = {t["table"]: t["reason"] for t in triggers}
    assert "fragments" in reasons["event_vectors"]
    assert "ratio" in reasons["source_chunks"]


def test_version_delta_uses_state_baseline():
    auto = _load_script_module("auto_maintenance_test2", "sag_api/maintenance/auto_maintenance.py")
    summary = {
        "tables": {
            "event_vectors": {"rows": 10, "fragments": 1, "latest_version": 1200,
                              "directory_bytes": 100, "active_total_bytes": 100},
        }
    }
    # 无状态基线：首次运行把累计版本数视为增量 → 触发
    first = auto._evaluate_triggers(
        summary, {"tables": {}}, min_fragments=500, min_version_delta=500, max_ratio=2.5, force=False,
    )
    assert [t["table"] for t in first] == ["event_vectors"]
    # 有状态基线：1200 -> 1250 增量 50 < 500 → 不触发
    second = auto._evaluate_triggers(
        summary, {"tables": {"event_vectors": {"latest_version": 1200}}},
        min_fragments=500, min_version_delta=500, max_ratio=2.5, force=False,
    )
    assert second == []


def test_queue_idle_detects_active_writes(tmp_path: Path):
    auto = _load_script_module("auto_maintenance_test3", "sag_api/maintenance/auto_maintenance.py")
    db = tmp_path / "meta.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE vector_write_jobs (id TEXT PRIMARY KEY, status TEXT);
        CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT);
        INSERT INTO vector_write_jobs VALUES ('v1', 'queued'), ('v2', 'writing');
        INSERT INTO jobs VALUES ('j1', 'SUCCEEDED');
        """
    )
    con.commit()
    con.close()
    idle, counts = auto._queue_idle(db)
    assert idle is False
    assert counts == {"vector:queued": 1, "vector:writing": 1}

    # 清空活跃后应判定为空闲
    con = sqlite3.connect(db)
    con.execute("DELETE FROM vector_write_jobs")
    con.commit()
    con.close()
    idle, counts = auto._queue_idle(db)
    assert idle is True
    assert counts == {}
