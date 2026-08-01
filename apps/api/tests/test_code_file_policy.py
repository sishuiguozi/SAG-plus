from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("path", "context", "expected_route", "expected_language", "default_selected"),
    [
        ("repo/src/main.py", "single", "tree_sitter", "python", True),
        ("repo/src/main.cpp", "code_folder", "tree_sitter", "cpp", True),
        ("repo/Dockerfile", "code_folder", "tree_sitter", "dockerfile", True),
        ("repo/Makefile", "code_folder", "tree_sitter", "make", True),
        ("repo/page.html", "single", "markitdown", None, True),
        ("repo/page.html", "code_folder", "tree_sitter", "html", True),
        ("repo/data.json", "single", "markitdown", None, True),
        ("repo/data.json", "code_folder", "tree_sitter", "json", True),
        ("repo/README.md", "code_folder", "markdown", None, True),
        ("repo/notes.txt", "code_folder", "text", None, True),
        ("repo/scenario.fxw", "code_folder", "text", None, True),
        ("repo/report.pdf", "code_folder", "markitdown", None, False),
        ("repo/slides.pptx", "code_folder", "markitdown", None, False),
        ("repo/table.csv", "code_folder", "markitdown", None, False),
    ],
)
def test_parser_route_matrix(path, context, expected_route, expected_language, default_selected):
    from sag_api.code_ingest.file_policy import route_file

    decision = route_file(path, context=context, content_sample=b"print('hello')\n")

    assert decision.route == expected_route
    assert decision.language == expected_language
    assert decision.default_selected is default_selected


@pytest.mark.parametrize(
    "path",
    [
        "repo/.env",
        "repo/.env.production",
        "repo/id_rsa",
        "repo/server.pem",
        "repo/credentials.json",
        "repo/package-lock.json",
        "repo/Cargo.lock",
        "repo/app.min.js",
        "repo/app.js.map",
        "repo/.gitignore",
        "repo/notebook.ipynb",
        "repo/node_modules/pkg/index.js",
        "../escape.py",
        "C:\\absolute\\main.py",
    ],
)
def test_sensitive_generated_and_unsafe_paths_are_rejected(path):
    from sag_api.code_ingest.file_policy import route_file

    decision = route_file(path, context="code_folder", content_sample=b"safe text")

    assert decision.route == "skip"
    assert decision.reason


def test_binary_is_rejected_and_unknown_reliable_text_falls_back():
    from sag_api.code_ingest.file_policy import route_file

    binary = route_file("repo/blob.unknown", context="code_folder", content_sample=b"abc\0def")
    text = route_file(
        "repo/scenario.custom",
        context="code_folder",
        content_sample="仿真参数 alpha = 1\n".encode(),
    )

    assert binary.route == "skip"
    assert "binary" in binary.reason.lower()
    assert text.route == "text"


@pytest.mark.asyncio
async def test_prepare_code_document_preserves_original_and_metadata(tmp_path: Path):
    from sag_api.core.config import Settings
    from sag_api.parsing.service import prepare_document

    source = tmp_path / "service.py"
    content = b"class Service:\n    def run(self):\n        return 1\n"
    source.write_bytes(content)

    prepared = await prepare_document(
        str(source),
        Settings(_env_file=None),
        ingest_context="code_folder",
        relative_path="repo/src/service.py",
    )

    assert prepared.path == str(source)
    assert prepared.provider == "tree_sitter"
    assert prepared.relative_path == "repo/src/service.py"
    assert prepared.content_sha256 == hashlib.sha256(content).hexdigest()
    assert prepared.code_language == "python"
    assert prepared.ingest_context == "code_folder"


def test_single_code_upload_is_allowed_but_sensitive_file_is_not():
    from sag_api.api.v1.documents import _check_extension
    from sag_api.core.errors import ValidationError

    _check_extension("main.py", b"print('ok')")
    with pytest.raises(ValidationError):
        _check_extension(".env", b"TOKEN=secret")
