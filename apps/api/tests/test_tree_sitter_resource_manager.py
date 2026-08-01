from __future__ import annotations

import asyncio
import subprocess
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


def test_installed_adapter_status_never_predownloads_languages(tmp_path: Path, monkeypatch):
    from sag_api.code_ingest.resource_manager import InstalledLanguagePackAdapter

    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="[]\n", stderr="")

    tmp_path.mkdir(exist_ok=True)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert InstalledLanguagePackAdapter().downloaded_languages(tmp_path) == set()
    assert "languages=[]" in calls[0][2]


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

@pytest.mark.asyncio
async def test_promote_keeps_complete_active_when_cleanup_is_locked(tmp_path: Path, monkeypatch):
    """Windows may lock previous/active DLLs; completed active pack should still become ready."""
    from sag_api.code_ingest import resource_manager as rm

    adapter = FakeLanguagePackAdapter()
    manager = rm.TreeSitterResourceManager(tmp_path, adapter=adapter)

    # First successful install
    await manager.start_download()
    await manager.wait()
    assert manager.status().state == "ready"
    assert manager.active_dir.is_dir()

    # Simulate a second download finishing into staging while Windows locks cleanup.
    manager.staging_dir.mkdir(parents=True, exist_ok=True)
    for language in adapter.manifest_languages():
        (manager.staging_dir / f"{language}.parser").write_bytes(language.encode())

    def locked_rmtree(path):
        raise PermissionError(5, "Access is denied", str(path))

    monkeypatch.setattr(rm.shutil, "rmtree", locked_rmtree)

    # Promote should not fail the whole install when active is already complete.
    manager._promote_staging()
    status = manager.status()
    assert status.state == "ready"
    assert status.installed_languages == 3
    assert manager.active_dir.is_dir()


@pytest.mark.asyncio
async def test_repair_short_circuits_when_active_pack_is_already_complete(tmp_path: Path):
    from sag_api.code_ingest.resource_manager import TreeSitterResourceManager

    adapter = FakeLanguagePackAdapter()
    manager = TreeSitterResourceManager(tmp_path, adapter=adapter)
    manager.active_dir.mkdir(parents=True)
    for language in adapter.manifest_languages():
        (manager.active_dir / f"{language}.parser").write_bytes(language.encode())
    manager._state = "failed"
    manager._error = "stale lock error"

    status = await manager.repair()
    await manager.wait()

    assert status.state == "ready"
    assert manager.status().error is None
    assert adapter.download_calls == []

@pytest.mark.asyncio
async def test_start_download_is_noop_when_active_pack_is_ready(tmp_path: Path):
    from sag_api.code_ingest.resource_manager import TreeSitterResourceManager

    adapter = FakeLanguagePackAdapter()
    manager = TreeSitterResourceManager(tmp_path, adapter=adapter)
    manager.active_dir.mkdir(parents=True)
    for language in adapter.manifest_languages():
        (manager.active_dir / f"{language}.parser").write_bytes(language.encode())

    status = await manager.start_download()
    await manager.wait()

    assert status.state == "ready"
    assert adapter.download_calls == []
    assert manager.status().state == "ready"


@pytest.mark.asyncio
async def test_download_reuses_active_languages_instead_of_refetching(tmp_path: Path):
    from sag_api.code_ingest.resource_manager import TreeSitterResourceManager

    adapter = FakeLanguagePackAdapter()
    manager = TreeSitterResourceManager(tmp_path, adapter=adapter)
    manager.active_dir.mkdir(parents=True)
    # Two languages already active; only typescript is missing.
    (manager.active_dir / "cpp.parser").write_bytes(b"cpp")
    (manager.active_dir / "python.parser").write_bytes(b"python")

    await manager.start_download()
    await manager.wait()

    assert manager.status().state == "ready"
    assert adapter.download_calls == ["typescript"]

