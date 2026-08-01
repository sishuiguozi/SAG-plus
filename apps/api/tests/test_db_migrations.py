from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine


async def test_document_code_metadata_columns_are_added_to_existing_database(tmp_path, monkeypatch):
    from sag_api.core import db

    migration_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}"
    )
    try:
        async with migration_engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                CREATE TABLE documents (
                    id VARCHAR(32) PRIMARY KEY,
                    source_id VARCHAR(32) NOT NULL,
                    filename VARCHAR(512) NOT NULL,
                    storage_path VARCHAR(1024) NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    token_usage BIGINT NOT NULL DEFAULT 0
                )
                """
            )

        monkeypatch.setattr(db, "engine", migration_engine)
        await db._ensure_columns()
        await db._ensure_columns()

        async with migration_engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]: column for column in inspect(sync_connection).get_columns("documents")
                }
            )

        assert columns["relative_path"]["type"].length == 1024
        assert columns["content_sha256"]["type"].length == 64
        assert columns["code_language"]["type"].length == 64
        assert any("ix_documents_source_relative_path" in ddl for ddl in db._INDEX_UPGRADES)
        assert any("ix_documents_source_code_language" in ddl for ddl in db._INDEX_UPGRADES)
    finally:
        await migration_engine.dispose()


def test_document_model_exposes_code_metadata_fields():
    from sag_api.db.models import Document

    assert Document.relative_path.property.columns[0].nullable is True
    assert Document.content_sha256.property.columns[0].nullable is True
    assert Document.code_language.property.columns[0].nullable is True
