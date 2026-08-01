from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from sag_api.schemas.code_folder import CodeFolderPlanItemIn, CodeFolderPlanRequest


@pytest.mark.asyncio
async def test_plan_classifies_new_changed_unchanged_rejected(monkeypatch):
    from sag_api.services import code_folder_service as svc

    existing = {
        "repo/a.py": ("a" * 64, "doc-a"),
        "repo/b.py": ("b" * 64, "doc-b"),
    }

    async def fake_map(session, source_id):
        return existing

    monkeypatch.setattr(svc, "_existing_code_map", fake_map)

    req = CodeFolderPlanRequest(
        root_name="repo",
        items=[
            CodeFolderPlanItemIn(relative_path="repo/a.py", sha256="a" * 64, size_bytes=10),  # unchanged
            CodeFolderPlanItemIn(relative_path="repo/b.py", sha256="c" * 64, size_bytes=10),  # changed
            CodeFolderPlanItemIn(relative_path="repo/c.py", sha256="d" * 64, size_bytes=10),  # new
            CodeFolderPlanItemIn(relative_path="repo/.env", sha256="e" * 64, size_bytes=10),  # rejected
            CodeFolderPlanItemIn(relative_path="other/x.py", sha256="f" * 64, size_bytes=10),  # rejected root
        ],
    )
    out = await svc.plan_code_folder(SimpleNamespace(), SimpleNamespace(id="src"), req)
    by_path = {i.relative_path: i for i in out.items}
    assert by_path["repo/a.py"].status == "unchanged"
    assert by_path["repo/b.py"].status == "changed"
    assert by_path["repo/c.py"].status == "new"
    assert by_path["repo/.env"].status == "rejected"
    assert any(i.status == "rejected" and "根目录" in i.reason for i in out.items)
    # missing local paths must not appear as delete
    assert all(i.status != "delete" for i in out.items)
    assert out.counts["new"] == 1
    assert out.counts["changed"] == 1
    assert out.counts["unchanged"] == 1
    assert out.counts["rejected"] >= 2


@pytest.mark.asyncio
async def test_upload_recomputes_hash_and_rejects_mismatch(monkeypatch):
    from sag_api.core.errors import ValidationError
    from sag_api.services import code_folder_service as svc

    with pytest.raises(ValidationError):
        await svc.upload_code_folder_file(
            SimpleNamespace(),
            SimpleNamespace(id="src"),
            relative_path="repo/a.py",
            root_name="repo",
            declared_sha256="0" * 64,
            data=b"print(1)\n",
            filename="a.py",
            content_type="text/x-python",
            job_queue=SimpleNamespace(enqueue=lambda *_: None),
        )


@pytest.mark.asyncio
async def test_upload_idempotent_unchanged(monkeypatch):
    from sag_api.core.errors import ConflictError
    from sag_api.services import code_folder_service as svc

    data = b"print(1)\n"
    digest = hashlib.sha256(data).hexdigest()

    async def fake_stage(*args, **kwargs):
        raise ConflictError("代码文件内容未变化，无需重新入库")

    async def fake_find(*args, **kwargs):
        return SimpleNamespace(id="doc1")

    monkeypatch.setattr(svc, "stage_code_document_upload", fake_stage)
    monkeypatch.setattr(svc, "find_document_by_relative_path", fake_find)

    out = await svc.upload_code_folder_file(
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
    assert out.status == "unchanged"
    assert out.document_id == "doc1"
    assert out.content_sha256 == digest
