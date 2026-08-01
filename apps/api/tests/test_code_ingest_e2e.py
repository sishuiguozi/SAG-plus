from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from sag_api.sag import code_context as cc
from sag_api.sag.dto import RetrievedSection
from sag_api.sag.incremental_processor import IncrementalDocumentProcessor
from sag_api.schemas.code_folder import CodeFolderPlanItemIn, CodeFolderPlanRequest
from sag_api.services import code_folder_service as folder_svc


@pytest.mark.asyncio
async def test_code_ingest_flow_plan_upload_extract_retrieve(monkeypatch, tmp_path):
    # 1) plan classifies new file
    async def fake_map(session, source_id):
        return {}

    monkeypatch.setattr(folder_svc, "_existing_code_map", fake_map)
    plan = await folder_svc.plan_code_folder(
        SimpleNamespace(),
        SimpleNamespace(id="src"),
        CodeFolderPlanRequest(
            root_name="repo",
            items=[CodeFolderPlanItemIn(relative_path="repo/a.py", sha256="a" * 64, size_bytes=12)],
        ),
    )
    assert plan.counts["new"] == 1

    # 2) upload stages document
    staged = {}

    async def fake_stage(session, source, **kwargs):
        staged.update(kwargs)
        return SimpleNamespace(id="doc1"), SimpleNamespace(id="job1", payload={})

    monkeypatch.setattr(folder_svc, "stage_code_document_upload", fake_stage)
    data = b"def run():\n    return 1\n"
    digest = hashlib.sha256(data).hexdigest()
    out = await folder_svc.upload_code_folder_file(
        SimpleNamespace(),
        SimpleNamespace(id="src"),
        relative_path="repo/a.py",
        root_name="repo",
        declared_sha256=digest,
        data=data,
        filename="a.py",
        content_type="text/x-python",
        job_queue=SimpleNamespace(),
    )
    assert out.status == "created"
    assert staged["relative_path"] == "repo/a.py"

    # 3) comments extraction skips empty extraction text
    processor = IncrementalDocumentProcessor(
        engine=SimpleNamespace(_extractor=SimpleNamespace(prompt_manager=object(), model_config=object())),
        source_config_id="cfg",
        max_concurrency=1,
        code_llm_extraction_mode="comments",
    )
    chunk = SimpleNamespace(
        id="c1",
        extra_data={"chunk_type": "code_child", "code_language": "python", "content_sha256": digest},
        content="def run():\n    return 1\n",
    )
    assert processor._code_extraction_plan(chunk) == (True, None)

    # 4) retrieval filters stale hash and keeps exact child with parent prefix
    sections = [
        RetrievedSection(chunk_id="old", content="old", score=0.2),
        RetrievedSection(chunk_id="child", content="def run():\n    return 1", score=0.9),
    ]

    async def fake_rows(ids):
        return {
            "old": SimpleNamespace(
                id="old",
                content="old",
                extra_data={
                    "chunk_type": "code_child",
                    "relative_path": "repo/a.py",
                    "content_sha256": "0" * 64,
                    "parent_id": "p",
                },
            ),
            "child": SimpleNamespace(
                id="child",
                content="def run():\n    return 1",
                heading="run",
                extra_data={
                    "chunk_type": "code_child",
                    "relative_path": "repo/a.py",
                    "content_sha256": digest,
                    "parent_id": "p",
                    "ancestor_path": ["repo/a.py"],
                },
            ),
            "p": SimpleNamespace(
                id="p",
                content="module",
                heading="a.py",
                extra_data={
                    "chunk_type": "code_parent",
                    "relative_path": "repo/a.py",
                    "signature": "module a.py",
                },
            ),
        }

    async def fake_hash(pairs):
        return {("src", "repo/a.py"): digest}

    monkeypatch.setattr(cc, "_load_chunk_rows", fake_rows)
    monkeypatch.setattr(cc, "_current_code_hash_map", fake_hash)
    filtered = await cc.filter_stale_code_sections(sections, app_source_id="src")
    enriched = await cc.enrich_code_context(filtered)
    assert [s.chunk_id for s in filtered] == ["child"]
    assert "File: repo/a.py" in enriched[0].content
    assert "def run():" in enriched[0].content
