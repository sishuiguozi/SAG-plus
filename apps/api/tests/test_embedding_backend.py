"""本地 embedding 后端补丁：api/local 切换与恢复、schema 校验。"""

import sys
import types

import pytest

from sag_api.schemas.system import ModelConfigUpdate


def _original_factory_impl():
    import zleap.sag.core.ai.factory as zl_factory

    return zl_factory.get_embedding_client


def _original_module_funcs():
    import zleap.sag.core.ai.embedding as zl_embedding

    return (
        zl_embedding.get_embedding_client,
        zl_embedding.generate_embedding,
        zl_embedding.batch_generate_embedding,
    )


def test_install_api_mode_keeps_original():
    from sag_api.core.config import settings
    from sag_api.sag.embedding_backend import install_embedding_backend, uninstall_embedding_backend

    original = _original_factory_impl()
    original_provider = settings.embedding_provider
    try:
        settings.embedding_provider = "api"
        install_embedding_backend(settings)
        assert _original_factory_impl() is original  # api 模式不替换
    finally:
        uninstall_embedding_backend()
        settings.embedding_provider = original_provider
    assert _original_factory_impl() is original


def test_install_local_mode_replaces_and_uninstall_restores():
    from sag_api.core.config import settings
    from sag_api.sag.embedding_backend import install_embedding_backend, uninstall_embedding_backend

    original_factory = _original_factory_impl()
    original_module = _original_module_funcs()
    original_provider = settings.embedding_provider
    try:
        settings.embedding_provider = "local"
        install_embedding_backend(settings)
        from sag_api.sag.embedding_backend import _factory_get_embedding_client

        assert _original_factory_impl() is _factory_get_embedding_client  # 已被替换
        assert _original_module_funcs() != original_module  # 便捷函数已替换
    finally:
        uninstall_embedding_backend()
        settings.embedding_provider = original_provider
    assert _original_factory_impl() is original_factory
    assert _original_module_funcs() == original_module


def test_model_config_patch_accepts_provider():
    patch = ModelConfigUpdate(embedding_provider="local")
    assert patch.embedding_provider == "local"
    assert ModelConfigUpdate(embedding_provider="api").embedding_provider == "api"
    with pytest.raises(ValueError):
        ModelConfigUpdate(embedding_provider="remote")


def test_local_embedding_enables_current_llama_cpp_embeddings_mode(tmp_path, monkeypatch):
    from sag_api.sag.embedding_backend import LocalEmbeddingClient

    model = tmp_path / "bge-m3-Q8_0.gguf"
    model.write_bytes(b"model")
    received = {}

    class FakeLlama:
        def __init__(self, **kwargs):
            received.update(kwargs)

    monkeypatch.setitem(sys.modules, "llama_cpp", types.SimpleNamespace(Llama=FakeLlama))

    LocalEmbeddingClient(str(model))._ensure_loaded()

    assert received["embeddings"] is True
