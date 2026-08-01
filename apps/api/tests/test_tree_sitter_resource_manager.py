from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


class FakeLanguagePackAdapter:
    version = "1.13.7"
    estimated_total_bytes = 360 * 1024 * 1024

    def __init__(self, languages: tuple[str, ...] = ("cpp", "python", "typescript")) -> None:
        self._languages = languages
        self.first_download_started = asyncio.Event()
        self.release_first_download = asyncio.Event()
        self.block_first_download = False
        self.download_calls: list[str] = []

    def manifest_languages(self) -> tuple[str, ...]:
        return self._languages

    def downloaded_languages(self, target_dir: Path) -> set[str]:
        if not target_dir.exists():
            return set()
        return {path.stem for path in target_dir.glob("*.parser")}

    async def download(self, language: str, target_dir: Path) -> None:
        self.download_calls.append(language)
        if self.block_first_download and len(self.download_calls) == 1:
            self.first_download_started.set()
            await self.release_first_download.wait()
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / f"{language}.parser").write_bytes(language.encode())


@pytest.mark.asyncio
async def test_download_promotes_only_a_complete_staging_directory(tmp_path: Path):
    from sag_api.code_ingest.resource_manager import TreeSitterResourceManager

    adapter = FakeLanguagePackAdapter()
    manager = TreeSitterResourceManager(tmp_path, adapter=adapter)

    assert manager.status().state == "missing"
    await manager.start_download()
    await manager.wait()

    status = manager.status()
    assert status.state == "ready"
    assert status.version == "1.13.7"
    assert status.installed_languages == 3
    assert status.total_languages == 3
    assert status.progress == 100
    assert manager.active_dir.is_dir()
    assert not manager.staging_dir.exists()


@pytest.mark.asyncio
async def test_download_pauses_at_a_language_boundary_and_resumes(tmp_path: Path):
    from sag_api.code_ingest.resource_manager import TreeSitterResourceManager

    adapter = FakeLanguagePackAdapter()
    adapter.block_first_download = True
    manager = TreeSitterResourceManager(tmp_path, adapter=adapter)

    await manager.start_download()
    await adapter.first_download_started.wait()
    await manager.pause()
    adapter.release_first_download.set()
    await manager.wait()

    paused = manager.status()
    assert paused.state == "paused"
    assert paused.installed_languages == 1
    assert not manager.active_dir.exists()

    await manager.resume()
    await manager.wait()
    assert manager.status().state == "ready"
    assert adapter.download_calls == ["cpp", "python", "typescript"]


@pytest.mark.asyncio
async def test_repair_downloads_only_missing_active_languages(tmp_path: Path):
    from sag_api.code_ingest.resource_manager import TreeSitterResourceManager

    adapter = FakeLanguagePackAdapter()
    manager = TreeSitterResourceManager(tmp_path, adapter=adapter)
    manager.active_dir.mkdir(parents=True)
    (manager.active_dir / "cpp.parser").write_bytes(b"cpp")
    (manager.active_dir / "python.parser").write_bytes(b"python")

    assert manager.status().state == "failed"
    await manager.repair()
    await manager.wait()

    assert manager.status().state == "ready"
    assert adapter.download_calls == ["typescript"]

