"""Native GGUF reranker adapter behaviour."""

import pytest


def test_local_reranker_uses_native_rank_runtime_and_caches_it(tmp_path):
    from sag_api.sag.local_reranker import LocalReranker

    model = tmp_path / "qwen3-reranker-0.6b-q8_0.gguf"
    model.write_bytes(b"model")
    created = []

    class NativeRuntime:
        def rank(self, query, documents):
            assert query == "find the panda"
            assert documents == ["a bear", "a database"]
            return [0.91, 0.02]

    def factory(model_path, *, n_ctx, n_threads):
        created.append((model_path, n_ctx, n_threads))
        return NativeRuntime()

    reranker = LocalReranker(str(model), n_ctx=4096, n_threads=3, runtime_factory=factory)

    assert reranker.rank("find the panda", ["a bear", "a database"]) == [0.91, 0.02]
    assert reranker.rank("find the panda", ["a bear", "a database"]) == [0.91, 0.02]
    assert created == [(str(model), 4096, 3)]


def test_local_reranker_rejects_non_reranker_model(tmp_path):
    from sag_api.sag.local_reranker import LocalReranker, LocalRerankerUnavailable

    model = tmp_path / "bge-m3-Q8_0.gguf"
    model.write_bytes(b"model")
    reranker = LocalReranker(str(model))

    with pytest.raises(LocalRerankerUnavailable, match="not a supported reranker"):
        reranker.rank("q", ["document"])


def test_local_reranker_reports_missing_native_runtime_without_generating_text(tmp_path):
    from sag_api.sag.local_reranker import LocalReranker, LocalRerankerUnavailable

    model = tmp_path / "qwen3-reranker-0.6b-q8_0.gguf"
    model.write_bytes(b"model")
    reranker = LocalReranker(str(model), runtime_factory=lambda *args, **kwargs: None)

    with pytest.raises(LocalRerankerUnavailable, match="native reranker runtime"):
        reranker.rank("q", ["document"])
