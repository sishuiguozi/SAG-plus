from sag_api.sag.local_model_catalog import MODEL_SPECS, ModelKind


def test_q8_catalog_contains_three_embedding_and_three_reranker_models():
    embeddings = [spec for spec in MODEL_SPECS if spec.kind is ModelKind.EMBEDDING]
    rerankers = [spec for spec in MODEL_SPECS if spec.kind is ModelKind.RERANKER]

    assert [spec.file_name for spec in embeddings] == [
        "bge-m3-Q8_0.gguf",
        "Qwen3-Embedding-0.6B-Q8_0.gguf",
        "Qwen3-Embedding-4B-Q8_0.gguf",
    ]
    assert [spec.file_name for spec in rerankers] == [
        "bge-reranker-v2-m3-q8_0.gguf",
        "qwen3-reranker-0.6b-q8_0.gguf",
        "Qwen3-Reranker-4B-Q8_0.gguf",
    ]
    assert [spec.dimensions for spec in embeddings] == [1024, 1024, 2560]
