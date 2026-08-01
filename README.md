# SAG-plus

SAG-plus is a locally run, personal optimization fork of
[Zleap-AI/SAG](https://github.com/Zleap-AI/SAG). It is maintained as a
desktop development workspace: the supported way to run it is Electron
development mode.

## Start the desktop app

Requirements:

- Node.js 20 or later.
- Python 3.11 or later, available as `python` (or set `SAG_PYTHON` to its path).

In Git Bash, run:

```bash
cd /e/SAG-plus/apps/desktop
npm run dev
```

The desktop script starts or reuses the local API (`127.0.0.1:8000`), Web UI
(`127.0.0.1:3000` or `3001`), and Electron window. On the first run it also
checks dependencies, runs `npm ci` where needed, creates `apps/api/.venv`, and
installs the API package. Stop it with `Ctrl+C`.

If this repository was moved from `E:\sag-dev`, keep using the existing local
data and development dependencies until they have been deliberately migrated.
`.data`, uploaded files, database files, and model caches are local data and
must not be committed.

## What SAG-plus improves

| Area | Optimization in this fork |
| --- | --- |
| Retrieval | Semantic retrieval is complemented by LanceDB FTS/BM25, result caching, and literal-search fallback. Reranking can use a local Q8 cross-encoder, a compatible API, or an LLM, with a safe fallback. |
| Context | Parent-child chunking retrieves precise child chunks while returning parent context, with duplicate suppression. |
| Ingestion | Persistent batch/vector-write coordination gives LanceDB a single writer, retries, recovery, and idempotent work items. |
| Storage | SQLite connection and pragma tuning, disk protection, LanceDB cleanup, index maintenance, and read/write observability reduce local-store pressure. |
| Model calls | Four tool-calling strategies control reasoning around tools; three reasoning-history modes can auto-detect DeepSeek, force support for model aliases, or disable the compatibility field. |
| Evaluation | The repository includes retrieval evaluation cases and runtime timing output for measuring changes against local data. |

Detailed implementation records are in
[the 2026 optimization status](docs/SAG_OPTIMIZATION_2026.md) and
[architecture patches](docs/ARCHITECTURE_PATCHES.md).

## Daily use

1. Start the desktop app with `npm run dev`.
2. Add or configure a knowledge source in **Settings → Knowledge**.
3. Wait for ingestion to complete, then use Search or Chat with source-backed
   answers.
4. Select **Parent-child** chunking for newly ingested documents when you want
   child-level recall with broader parent context. Existing documents are not
   changed until they are processed again.
5. To run embeddings fully locally, open **Settings → Model configuration →
   Local embedding**, click **Download inference backend**, then choose BGE-M3
   Q8, Qwen3-Embedding-0.6B Q8, or Qwen3-Embedding-4B Q8. Weights are never downloaded
   automatically; select a completed model and save the configuration. Click
   **Test local model** to test the model currently selected in the dropdown
   against the current context or thread draft—no save is required. It generates
   one temporary vector and shows its model, dimensions, and latency; it does
   not change saved configuration, the knowledge base, or remote services.
6. To enable reranking, open **Settings → Model configuration → Retrieval
   reranking**. Choose a local BGE/Qwen Q8 cross-encoder, a compatible Rerank
   API using its full URL (including Qwen and vLLM), or legacy LLM numbered
   reranking. Local rerank models download separately from embeddings; their
   native rank runtime is built only after you explicitly install it, and can be
   tested without saving.
7. Under **Settings → Model configuration → Generation parameters → Tool
   calling strategy**, choose how reasoning behaves around tools. The recommended
   mode keeps reasoning for normal answers, disables it only for forced tool
   selection, and restores it for the post-tool answer. Keep-reasoning,
   automatic-tool, and disable-all modes are also available. **Reasoning history
   compatibility** defaults to DeepSeek auto-detection; use **Always enable**
   for aliased compatible models or **Off** when an endpoint rejects
   `reasoning_content`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| API does not start | Confirm Python 3.11+ is available and port 8000 is not already occupied by an unrelated process. The launcher recreates its virtual environment when needed. |
| Web does not start | The launcher installs missing Web dependencies; it can use port 3000 or 3001. |
| Electron closes immediately | Run the command from `E:\SAG-plus\apps\desktop` and read the first failing API or Web process in the terminal. |
| Old knowledge is missing | Local data was not copied by Git. Verify the intended local data directory before creating a new index. |
| Local embeddings are unavailable | In Settings, install the llama-cpp-python backend, download a selected model, choose that file, then save. |
| Local reranking is unavailable | In **Retrieval reranking**, download the selected model and install the native reranker runtime. Retrieval keeps its fused order if either is unavailable. |

## Development references

- [Desktop development](apps/desktop/README.md)
- [API architecture](apps/api/README.md)
- [Optimization status](docs/SAG_OPTIMIZATION_2026.md)
- [Optimization plan](docs/SAG_OPTIMIZATION_PLAN.md)

Licensed under [MIT](LICENSE). Upstream attribution remains with
[Zleap-AI/SAG](https://github.com/Zleap-AI/SAG).
