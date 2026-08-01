"""Pure file classification and safety rules shared by upload paths."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

IngestContext = Literal["single", "code_folder"]
ParserRoute = Literal["tree_sitter", "markdown", "text", "mineru", "markitdown", "skip"]


@dataclass(frozen=True, slots=True)
class FileRouteDecision:
    route: ParserRoute
    normalized_path: str
    language: str | None = None
    default_selected: bool = True
    reason: str = ""


_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_GENERIC_TEXT_SUFFIXES = {".txt", ".text", ".log", ".rst", ".adoc", ".asciidoc"}
_AFSIM_TEXT_SUFFIXES = {
    ".ag",
    ".ant",
    ".def",
    ".dm",
    ".earth",
    ".fxw",
    ".gnu",
    ".imesh",
    ".psam",
    ".script",
    ".sep",
    ".soar",
    ".vsa",
}
_MARKITDOWN_SUFFIXES = {
    ".csv",
    ".docx",
    ".epub",
    ".htm",
    ".html",
    ".json",
    ".pdf",
    ".pptx",
    ".tsv",
    ".xls",
    ".xlsx",
}
_BULK_DEFAULT_OFF_SUFFIXES = {".csv", ".docx", ".epub", ".pdf", ".pptx", ".tsv", ".xls", ".xlsx"}
_BINARY_SUFFIXES = {
    ".7z",
    ".a",
    ".bin",
    ".class",
    ".dll",
    ".dylib",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lib",
    ".mp3",
    ".mp4",
    ".o",
    ".obj",
    ".png",
    ".pyc",
    ".so",
    ".tar",
    ".webp",
    ".zip",
}
_BLOCKED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
_LOCK_FILES = {
    "cargo.lock",
    "composer.lock",
    "gemfile.lock",
    "go.sum",
    "package-lock.json",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
}
_SENSITIVE_NAMES = {
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
}
_SENSITIVE_SUFFIXES = {".der", ".key", ".p12", ".pem", ".pfx"}
_SPECIAL_LANGUAGE_NAMES = {
    "cmakelists.txt": "cmake",
    "dockerfile": "dockerfile",
    "makefile": "make",
}
_WINDOWS_RESERVED = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def normalize_relative_path(path: str) -> str | None:
    raw = path.replace("\\", "/").strip()
    if not raw or "\0" in raw or re.search(r"[\x00-\x1f]", raw):
        return None
    if PurePosixPath(raw).is_absolute() or PureWindowsPath(path).is_absolute():
        return None
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    if any(part.rstrip(". ").casefold() in _WINDOWS_RESERVED for part in parts):
        return None
    return "/".join(parts)


def route_file(
    path: str,
    *,
    context: IngestContext = "single",
    content_sample: bytes | None = None,
    effective_document_parser: Literal["markitdown", "mineru"] = "markitdown",
) -> FileRouteDecision:
    normalized = normalize_relative_path(path)
    if normalized is None:
        return FileRouteDecision("skip", "", reason="Unsafe relative path")

    parts = PurePosixPath(normalized).parts
    lowered_parts = tuple(part.casefold() for part in parts)
    name = lowered_parts[-1]
    suffix = PurePosixPath(name).suffix

    if any(part in _BLOCKED_DIRECTORIES for part in lowered_parts[:-1]):
        return FileRouteDecision("skip", normalized, reason="Generated or dependency directory")
    if name == ".gitignore":
        return FileRouteDecision("skip", normalized, reason=".gitignore is a filter, not knowledge")
    if name == ".env" or name.startswith(".env.") or name in _SENSITIVE_NAMES or suffix in _SENSITIVE_SUFFIXES:
        return FileRouteDecision("skip", normalized, reason="Sensitive credential or private-key file")
    if name in _LOCK_FILES:
        return FileRouteDecision("skip", normalized, reason="Dependency lock file")
    if suffix == ".ipynb":
        return FileRouteDecision("skip", normalized, reason="Jupyter notebooks are not supported yet")
    if name.endswith((".min.js", ".min.css", ".js.map", ".css.map")) or suffix == ".map":
        return FileRouteDecision("skip", normalized, reason="Generated/minified source")
    if suffix in _BINARY_SUFFIXES or _looks_binary(content_sample):
        return FileRouteDecision("skip", normalized, reason="Binary file")

    if suffix in _MARKDOWN_SUFFIXES:
        return FileRouteDecision("markdown", normalized)
    if suffix in _GENERIC_TEXT_SUFFIXES or suffix in _AFSIM_TEXT_SUFFIXES:
        return FileRouteDecision("text", normalized)
    if suffix == ".pdf":
        route: ParserRoute = "mineru" if effective_document_parser == "mineru" else "markitdown"
        return FileRouteDecision(
            route,
            normalized,
            default_selected=context == "single",
        )
    if suffix in _MARKITDOWN_SUFFIXES and (context == "single" or suffix not in {".html", ".htm", ".json"}):
        return FileRouteDecision(
            "markitdown",
            normalized,
            default_selected=context == "single" or suffix not in _BULK_DEFAULT_OFF_SUFFIXES,
        )

    language = _SPECIAL_LANGUAGE_NAMES.get(name) or _detect_language(normalized)
    if language:
        return FileRouteDecision("tree_sitter", normalized, language=language)
    if _looks_reliable_text(content_sample):
        return FileRouteDecision("text", normalized, reason="Reliable text fallback")
    return FileRouteDecision("skip", normalized, reason="Unsupported or unrecognized file")


def _detect_language(path: str) -> str | None:
    try:
        import tree_sitter_language_pack as pack

        return pack.detect_language_from_path(path)
    except (ImportError, RuntimeError, ValueError):
        return None


def _looks_binary(sample: bytes | None) -> bool:
    if not sample:
        return False
    return b"\0" in sample[:8192]


def _looks_reliable_text(sample: bytes | None) -> bool:
    if not sample or _looks_binary(sample):
        return False
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not text.strip():
        return False
    acceptable = sum(character.isprintable() or character in "\r\n\t" for character in text)
    return acceptable / len(text) >= 0.95
