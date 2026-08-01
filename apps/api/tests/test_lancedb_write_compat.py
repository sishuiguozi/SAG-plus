"""SAG-OPT-105 LanceDB 追加/更新分离补丁测试。

覆盖验收：
- 纯新增批次不执行 merge_insert（只 add）。
- 混合批次：新增走 add、更新走一次 merge_insert（单批一次 merge）。
- 纯更新批次不执行 add。
- 重复提交保持幂等（不产生重复行）。
- 补丁可重复安装（幂等）、提供 bulk_append_new 方法面。
- 预查询失败/开关关闭时降级到上游 merge_insert 路径。
"""

from types import SimpleNamespace

import pytest

from zleap.sag.core.storage.lancedb_store import LanceDBStore

from sag_api.sag import lancedb_write_compat as compat


def _row(did: str, text: str = "x", v: float = 1.0) -> tuple[str, dict]:
    return (did, {"text": text, "vector": [v, 0.0, 0.0]})


async def _make_store(tmp_path) -> tuple[LanceDBStore, object]:
    store = LanceDBStore(str(tmp_path))
    tbl = await store._ensure_table("events", [doc for _, doc in [_row("seed")]])
    await tbl.add([{"id": "seed", "text": "seed", "vector": [0.5, 0.0, 0.0]}])
    calls = {"add": 0, "merge": 0, "query": 0}
    added: list[list[dict]] = []
    merged: list[list[dict]] = []

    orig_add = tbl.add
    orig_merge = tbl.merge_insert
    orig_query = tbl.query

    async def counting_add(*args, **kwargs):
        calls["add"] += 1
        added.append(list(args[0]))
        return await orig_add(*args, **kwargs)

    def counting_merge(*args, **kwargs):
        calls["merge"] += 1
        builder = orig_merge(*args, **kwargs)
        orig_execute = builder.execute
        async def counting_execute(records, **kw):
            merged.append(list(records))
            return await orig_execute(records, **kw)
        builder.execute = counting_execute  # type: ignore[method-assign]
        return builder

    def counting_query(*args, **kwargs):
        calls["query"] += 1
        return orig_query(*args, **kwargs)

    tbl.add = counting_add  # type: ignore[method-assign]
    tbl.merge_insert = counting_merge  # type: ignore[method-assign]
    tbl.query = counting_query  # type: ignore[method-assign]
    return store, SimpleNamespace(tbl=tbl, calls=calls, added=added, merged=merged)


@pytest.fixture(autouse=True)
def _installed_patch():
    compat.install_zleap_sag_lancedb_append_vs_merge_patch()
    yield


async def test_patch_install_is_idempotent_and_exposes_bulk_append_new():
    first = LanceDBStore._write
    compat.install_zleap_sag_lancedb_append_vs_merge_patch()
    second = LanceDBStore._write
    assert first is second
    assert getattr(second, "_sag_api_append_vs_merge_patch", False) is True
    assert callable(getattr(LanceDBStore, "bulk_append_new", None))


async def test_pure_new_batch_uses_add_not_merge(tmp_path):
    store, ctx = await _make_store(tmp_path)
    rows = [_row("n1", text="new1"), _row("n2", text="new2")]
    n = await store._write("events", rows)

    assert n == 2
    assert ctx.calls["add"] == 1
    assert ctx.calls["merge"] == 0
    assert {r["id"] for r in ctx.added[0]} == {"n1", "n2"}
    assert await ctx.tbl.count_rows() == 3


async def test_mixed_batch_splits_append_and_merge(tmp_path):
    store, ctx = await _make_store(tmp_path)
    rows = [
        _row("seed", text="updated-seed", v=9.0),
        _row("n1", text="brand-new"),
    ]
    n = await store._write("events", rows)

    assert n == 2
    assert ctx.calls["add"] == 1
    assert ctx.calls["merge"] == 1
    assert [r["id"] for r in ctx.added[0]] == ["n1"]
    assert [r["id"] for r in ctx.merged[0]] == ["seed"]
    assert await ctx.tbl.count_rows() == 2


async def test_pure_update_batch_uses_merge_not_add(tmp_path):
    store, ctx = await _make_store(tmp_path)
    rows = [_row("seed", text="v2", v=5.0)]
    n = await store._write("events", rows)

    assert n == 1
    assert ctx.calls["add"] == 0
    assert ctx.calls["merge"] == 1
    assert await ctx.tbl.count_rows() == 1
    rows_out = await ctx.tbl.query().where("id = 'seed'").to_arrow()
    assert rows_out.to_pylist()[0]["text"] == "v2"


async def test_repeated_submit_stays_idempotent(tmp_path):
    store, ctx = await _make_store(tmp_path)
    batch = [_row("n1"), _row("n2")]

    first = await store._write("events", batch)
    ctx.calls["add"] = 0
    ctx.calls["merge"] = 0
    ctx.added.clear()
    ctx.merged.clear()
    second = await store._write("events", batch)

    assert (first, second) == (2, 2)
    assert ctx.calls["add"] == 0  # 第二次全部命中已存在 → 不追加
    assert ctx.calls["merge"] == 1
    assert await ctx.tbl.count_rows() == 3  # seed + n1 + n2，无重复行


async def test_duplicate_ids_in_one_batch_are_deduped(tmp_path):
    store, ctx = await _make_store(tmp_path)
    n = await store._write("events", [_row("dup"), _row("dup", text="last")])
    assert n == 1
    assert ctx.calls["add"] == 1
    assert await ctx.tbl.count_rows() == 2  # seed + dup(1 条)
    rows_out = await ctx.tbl.query().where("id = 'dup'").to_arrow()
    assert rows_out.to_pylist()[0]["text"] == "last"


async def test_bulk_append_new_appends_without_merge(tmp_path):
    store, ctx = await _make_store(tmp_path)
    docs = [{"id": "b1", "text": "x", "vector": [1.0, 0.0, 0.0]}]
    n = await store.bulk_append_new("events", docs)
    assert n == 1
    assert ctx.calls["add"] == 1
    assert ctx.calls["merge"] == 0
    assert await ctx.tbl.count_rows() == 2


async def test_query_failure_falls_back_to_original_merge(tmp_path, monkeypatch):
    store, ctx = await _make_store(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("query boom")

    monkeypatch.setattr(ctx.tbl, "query", boom)
    n = await store._write("events", [_row("f1")])
    assert n == 1
    assert ctx.calls["merge"] == 1  # 降级走原 merge_insert 路径
    assert await ctx.tbl.count_rows() == 2


async def test_disabled_flag_delegates_to_original(tmp_path, monkeypatch):
    store, ctx = await _make_store(tmp_path)
    fake_settings = SimpleNamespace(vector_append_new_enabled=False, vector_append_lookup_chunk_size=500)
    monkeypatch.setattr(compat, "get_settings", lambda: fake_settings)

    n = await store._write("events", [_row("d1")])
    assert n == 1
    assert ctx.calls["add"] == 0
    assert ctx.calls["merge"] == 1


async def test_lookup_is_chunked(tmp_path, monkeypatch):
    store, ctx = await _make_store(tmp_path)
    fake_settings = SimpleNamespace(vector_append_new_enabled=True, vector_append_lookup_chunk_size=2)
    monkeypatch.setattr(compat, "get_settings", lambda: fake_settings)

    rows = [_row(f"c{i}") for i in range(5)]
    n = await store._write("events", rows)
    assert n == 5
    assert ctx.calls["query"] >= 3  # 5 个 id / 每块 2 → 至少 3 次预查询
    assert await ctx.tbl.count_rows() == 6


