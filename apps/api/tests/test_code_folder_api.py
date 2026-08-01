from __future__ import annotations

from sag_api.schemas.code_folder import CodeFolderPlanRequest, CodeFolderUploadOut


def test_plan_schema_defaults():
    req = CodeFolderPlanRequest(root_name="repo", items=[])
    assert req.root_name == "repo"
    assert req.items == []


def test_upload_out_statuses():
    out = CodeFolderUploadOut(
        document_id="d1",
        relative_path="repo/a.py",
        content_sha256="a" * 64,
        status="created",
    )
    assert out.job_id is None
