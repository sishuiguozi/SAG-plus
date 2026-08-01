from pathlib import Path

from sag_api.core.config import Settings


def test_data_root_derives_database_engine_and_upload_paths(tmp_path: Path) -> None:
    root = tmp_path / "kb-data"
    settings = Settings(
        _env_file=None,
        data_root=str(root),
        database_url="sqlite+aiosqlite:///./.data/ignored.db",
        data_dir="./.data/ignored-engine",
        upload_dir="./.data/ignored-uploads",
    )

    resolved = root.resolve()
    assert Path(settings.data_root) == resolved
    assert Path(settings.data_dir) == resolved / "engine"
    assert Path(settings.upload_dir) == resolved / "uploads"
    assert settings.database_url == f"sqlite+aiosqlite:///{(resolved / 'sag.db').as_posix()}"


def test_without_data_root_keeps_explicit_paths() -> None:
    settings = Settings(
        _env_file=None,
        data_root=None,
        database_url="sqlite+aiosqlite:///./.data/custom.db",
        data_dir="./.data/custom-engine",
        upload_dir="./.data/custom-uploads",
    )
    assert settings.database_url == "sqlite+aiosqlite:///./.data/custom.db"
    assert settings.data_dir == "./.data/custom-engine"
    assert settings.upload_dir == "./.data/custom-uploads"
