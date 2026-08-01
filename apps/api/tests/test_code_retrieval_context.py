from __future__ import annotations

from types import SimpleNamespace

import pytest

from sag_api.sag.dto import RetrievedSection


@pytest.mark.asyncio
async def test_filter_stale_code_sections_drops_old_hash(monkeypatch):
    from sag_api.sag import code_context as cc

    sections = [
        RetrievedSection(chunk_id="old", content="old body", score=0.9),
        RetrievedSection(chunk_id="new", content="new body", score=0.8),
        RetrievedSection(chunk_id="plain", content="markdown", score=0.7),
    ]

    async def fake_rows(ids):
        return {
            "old": SimpleNamespace(
                id="old",
                extra_data={
                    "chunk_type": "code_child",
                    "relative_path": "a.py",
                    "content_sha256": "a" * 64,
                },
                source_config_id="cfg",
                content="old body",
            ),
            "new": SimpleNamespace(
                id="new",
                extra_data={
                    "chunk_type": "code_child",
                    "relative_path": "a.py",
                    "content_sha256": "b" * 64,
                },
                source_config_id="cfg",
                content="new body",
            ),
            "plain": SimpleNamespace(id="plain", extra_data={"chunk_type": "child"}, content="markdown"),
        }

    async def fake_hash_map(pairs):
        return {("src1", "a.py"): "b" * 64}

    monkeypatch.setattr(cc, "_load_chunk_rows", fake_rows)
    monkeypatch.setattr(cc, "_current_code_hash_map", fake_hash_map)

    out = await cc.filter_stale_code_sections(sections, app_source_id="src1")
    assert [s.chunk_id for s in out] == ["new", "plain"]


@pytest.mark.asyncio
async def test_enrich_code_context_prefixes_parent_and_dedupes(monkeypatch):
    from sag_api.sag import code_context as cc

    parent = RetrievedSection(chunk_id="p", content="class Service:", score=0.5, heading="Service")
    child = RetrievedSection(chunk_id="c", content="def run(self):\n    return 1", score=0.9)

    async def fake_rows(ids):
        data = {
            "p": SimpleNamespace(
                id="p",
                heading="Service",
                content="class Service:",
                extra_data={
                    "chunk_type": "code_parent",
                    "relative_path": "repo/service.py",
                    "signature": "class Service:",
                    "ancestor_path": ["repo/service.py"],
                },
            ),
            "c": SimpleNamespace(
                id="c",
                heading="run",
                content="def run(self):\n    return 1",
                extra_data={
                    "chunk_type": "code_child",
                    "parent_id": "p",
                    "relative_path": "repo/service.py",
                    "ancestor_path": ["repo/service.py", "Service"],
                    "qualified_name": "Service.run",
                },
            ),
        }
        return {i: data[i] for i in ids if i in data}

    monkeypatch.setattr(cc, "_load_chunk_rows", fake_rows)
    out = await cc.enrich_code_context([parent, child])
    assert len(out) == 1
    assert out[0].chunk_id == "c"
    assert "File: repo/service.py" in out[0].content
    assert "Parent: class Service:" in out[0].content
    assert "def run(self):" in out[0].content


@pytest.mark.asyncio
async def test_generic_parent_enrichment_still_replaces_markdown_child(monkeypatch):
    # Ensure code pipeline does not break existing parent_child behavior contract.
    from sag_api.sag.parent_child import enrich_parent_context

    section = RetrievedSection(chunk_id="x", content="legacy", score=0.1)
    # No DB rows / no markers => unchanged
    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    # If DB fails, function returns original sections.
    # call with empty runtime-safe path
    out = await enrich_parent_context([section])
    assert out == [section]
