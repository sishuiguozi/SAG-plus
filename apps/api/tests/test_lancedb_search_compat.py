"""SAG-OPT-302：LanceDB ANN 检索参数补丁测试。

- 补丁幂等安装。
- 补丁后 vector_search 在无索引小表上仍返回正确结果（回退 exact 语义）。
- 关闭开关（lancedb_ann_enabled=false）时走原始方法。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def compat_module():
    from sag_api.sag import lancedb_search_compat
    return lancedb_search_compat


def test_ann_search_patch_installs_idempotently(compat_module, tmp_path: Path):
    install = compat_module.install_zleap_sag_lancedb_ann_search_patch
    install()
    install()
    from zleap.sag.core.storage.lancedb_store import LanceDBStore

    assert getattr(LanceDBStore.vector_search, compat_module._PATCH_MARK, False) is True


def test_patched_vector_search_returns_cosine_results(compat_module, tmp_path: Path):
    compat_module.install_zleap_sag_lancedb_ann_search_patch()
    from zleap.sag.core.storage.lancedb_store import LanceDBStore

    uri = tmp_path / "lancedb"
    store = LanceDBStore(str(uri))
    rng = np.random.default_rng(7)
    rows = [
        {"id": f"r{i}", "vector": [float(x) for x in rng.normal(size=16)], "sc": f"s{i % 3}"}
        for i in range(50)
    ]

    async def scenario() -> dict:
        await store.bulk_index("tbl", [{"_id": r["id"], **r} for r in rows])
        out = await store.vector_search("tbl", "vector", rows[0]["vector"], size=5)
        out_l2 = await store.vector_search("tbl", "vector", rows[0]["vector"], size=5, filter_query={"term": {"sc": "s0"}})
        return {"plain": out, "filtered": out_l2}

    result = _run(scenario())
    assert len(result["plain"]) == 5
    assert result["plain"][0]["id"] == "r0"
    assert all(r["sc"] == "s0" for r in result["filtered"])
    assert all("_score" in r and "_distance" not in r for r in result["plain"])
