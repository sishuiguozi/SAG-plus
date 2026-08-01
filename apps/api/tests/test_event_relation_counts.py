"""SAG-OPT-603：事件真实实体关联数（relation_count）测试。

- _event_relation_counts 批量统计引擎库 event_entity 关联数（is_delete 过滤）。
- 引擎库缺失/异常时降级为空 dict，不影响图谱主流程。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sag_api.services.insight_service import _event_relation_counts


def test_event_relation_counts(tmp_path: Path, monkeypatch):
    from sag_api.core.config import settings

    engine_dir = tmp_path / "engine"
    engine_dir.mkdir(parents=True)
    db = engine_dir / "sag.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE event_entity (
            id TEXT PRIMARY KEY,
            event_id TEXT,
            entity_id TEXT,
            is_delete INTEGER
        );
        INSERT INTO event_entity VALUES ('r1', 'evt_a', 'ent_1', 0);
        INSERT INTO event_entity VALUES ('r2', 'evt_a', 'ent_2', 0);
        INSERT INTO event_entity VALUES ('r3', 'evt_a', 'ent_3', 1);  -- 已删除不计
        INSERT INTO event_entity VALUES ('r4', 'evt_b', 'ent_1', 0);
        INSERT INTO event_entity VALUES ('r5', 'evt_c', 'ent_1', NULL);
        """
    )
    con.commit()
    con.close()

    monkeypatch.setattr(settings, "data_dir", str(engine_dir))
    counts = _event_relation_counts(["evt_a", "evt_b", "evt_c", "evt_missing"])
    assert counts == {"evt_a": 2, "evt_b": 1, "evt_c": 1}


def test_event_relation_counts_missing_db(tmp_path: Path, monkeypatch):
    from sag_api.core.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "no-such-dir"))
    assert _event_relation_counts(["evt_a"]) == {}
    assert _event_relation_counts([]) == {}
