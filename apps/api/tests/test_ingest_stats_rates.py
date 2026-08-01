"""SAG-OPT-501/503：文档服务端搜索 + 实时速率与 ETA 扩展测试。

- list_documents 支持 keyword 搜索（filename ilike）。
- ingest_stats 返回 chunks/events/vector 速率字段与 stalled_reason。
- _derive_stalled_reason 分支正确。
- 引擎库只读速率统计在临时库上正确计数、缺失库降级为 0。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sag_api.services.source_service import _derive_stalled_reason, _recent_engine_counts


def test_derive_stalled_reason_branches():
    # 有待处理但无 worker → no_worker
    assert _derive_stalled_reason(docs_per_minute=0.0, total_files=10, pending_files=5,
                                  paused_files=0, queued_jobs=0, running_jobs=0) == "no_worker"
    # 有 queued 任务 → queued_waiting
    assert _derive_stalled_reason(docs_per_minute=0.0, total_files=10, pending_files=5,
                                  paused_files=0, queued_jobs=3, running_jobs=0) == "queued_waiting"
    # 有 running → running_in_progress
    assert _derive_stalled_reason(docs_per_minute=0.0, total_files=10, pending_files=5,
                                  paused_files=0, queued_jobs=0, running_jobs=1) == "running_in_progress"
    # 全部为 paused（可执行 pending=0）→ idle
    assert _derive_stalled_reason(docs_per_minute=0.0, total_files=10, pending_files=5,
                                  paused_files=5, queued_jobs=0, running_jobs=0) == "idle"
    # 部分 paused 且无可执行 worker → paused
    assert _derive_stalled_reason(docs_per_minute=0.0, total_files=10, pending_files=5,
                                  paused_files=4, queued_jobs=0, running_jobs=0) == "paused"
    # 有速率 → None
    assert _derive_stalled_reason(docs_per_minute=1.5, total_files=10, pending_files=5,
                                  paused_files=0, queued_jobs=0, running_jobs=0) is None
    # 空库 → None
    assert _derive_stalled_reason(docs_per_minute=0.0, total_files=0, pending_files=0,
                                  paused_files=0, queued_jobs=0, running_jobs=0) is None


def test_recent_engine_counts(tmp_path: Path):
    db = tmp_path / "sag.db"
    con = sqlite3.connect(db)
    now = datetime.now(UTC)
    con.executescript(
        f"""
        CREATE TABLE article_section (id TEXT PRIMARY KEY, created_time DATETIME);
        CREATE TABLE source_event (id TEXT PRIMARY KEY, created_time DATETIME);
        INSERT INTO article_section VALUES ('c1', '{now.strftime("%Y-%m-%d %H:%M:%S")}');
        INSERT INTO article_section VALUES ('c2', '{now.strftime("%Y-%m-%d %H:%M:%S")}');
        INSERT INTO article_section VALUES ('c3', '{(now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")}');
        INSERT INTO source_event VALUES ('e1', '{now.strftime("%Y-%m-%d %H:%M:%S")}');
        INSERT INTO source_event VALUES ('e2', '{(now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")}');
        """
    )
    con.commit()
    con.close()
    window = now - timedelta(minutes=10)
    chunks, events = _recent_engine_counts(db, window)
    assert chunks == 2
    assert events == 1


def test_recent_engine_counts_missing_columns_and_files(tmp_path: Path):
    # 缺 created_time 列 → 该表降级为 0
    db = tmp_path / "sag2.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE article_section (id TEXT PRIMARY KEY)")
    con.commit()
    con.close()
    window = datetime.now(UTC) - timedelta(minutes=10)
    chunks, events = _recent_engine_counts(db, window)
    assert (chunks, events) == (0, 0)
    # 不存在文件 → (0, 0)
    assert _recent_engine_counts(tmp_path / "nope.db", window) == (0, 0)
