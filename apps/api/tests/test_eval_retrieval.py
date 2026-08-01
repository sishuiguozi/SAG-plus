"""A5 检索评估脚本的运行时初始化回归测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_eval_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "eval_retrieval.py"
    spec = importlib.util.spec_from_file_location("eval_retrieval_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Source:
    def __init__(self, source_config_id: str):
        self.sag_source_config_id = source_config_id


class _Manager:
    def __init__(self):
        self.provisioned: list[tuple[str, object]] = []

    async def provision(self, source_config_id: str, source: object) -> None:
        self.provisioned.append((source_config_id, source))


@pytest.mark.asyncio
async def test_warm_read_runtime_provisions_one_source_in_caller_context():
    script = _load_eval_script()
    manager = _Manager()
    sources = [_Source("source-a"), _Source("source-b")]

    await script.warm_read_runtime(manager, sources)

    assert manager.provisioned == [("source-a", sources[0])]


def test_format_case_result_includes_hit_count_and_latency():
    script = _load_eval_script()

    rendered = script.format_case_result(
        {"query": "AFSIM terrain"},
        top_k=5,
        matched_count=2,
        elapsed_ms=123.4,
    )

    assert rendered == "[HIT] AFSIM terrain  (top5 命中 2，123ms)"
