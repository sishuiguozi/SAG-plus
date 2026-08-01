# SAG-plus

SAG-plus is a locally run, personal optimization fork of
[Zleap-AI/SAG](https://github.com/Zleap-AI/SAG). It is maintained as a
desktop development workspace: the supported way to run it is Electron
development mode.

## Start the desktop app

Requirements:

- Node.js 20 or later.
- Desktop, Web, and API dependencies are already installed. In particular,
  `apps/api/.venv` and `apps/web/node_modules` must exist.

In Git Bash, run:

```bash
cd /e/SAG-plus/apps/desktop
npm run dev
```

The desktop script starts or reuses the local API (`127.0.0.1:8000`), Web UI
(`127.0.0.1:3000` or `3001`), and Electron window. Stop it with `Ctrl+C`.

If this repository was moved from `E:\sag-dev`, keep using the existing local
data and development dependencies until they have been deliberately migrated.
`.data`, uploaded files, database files, and model caches are local data and
must not be committed.

## What SAG-plus improves

| Area | Optimization in this fork |
| --- | --- |
| Retrieval | Semantic retrieval is complemented by LanceDB FTS/BM25, reranking, result caching, and literal-search fallback. FTS work runs outside the async event loop. |
| Context | Parent-child chunking retrieves precise child chunks while returning parent context, with duplicate suppression. |
| Ingestion | Persistent batch/vector-write coordination gives LanceDB a single writer, retries, recovery, and idempotent work items. |
| Storage | SQLite connection and pragma tuning, disk protection, LanceDB cleanup, index maintenance, and read/write observability reduce local-store pressure. |
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

## Troubleshooting

| Symptom | Check |
| --- | --- |
| API does not start | Confirm `apps/api/.venv` exists and port 8000 is not already occupied by an unrelated process. |
| Web does not start | Confirm `apps/web/node_modules` exists; the desktop launcher can use port 3000 or 3001. |
| Electron closes immediately | Run the command from `E:\SAG-plus\apps\desktop` and read the first failing API or Web process in the terminal. |
| Old knowledge is missing | Local data was not copied by Git. Verify the intended local data directory before creating a new index. |

## Development references

- [Desktop development](apps/desktop/README.md)
- [API architecture](apps/api/README.md)
- [Optimization status](docs/SAG_OPTIMIZATION_2026.md)
- [Optimization plan](docs/SAG_OPTIMIZATION_PLAN.md)

Licensed under [MIT](LICENSE). Upstream attribution remains with
[Zleap-AI/SAG](https://github.com/Zleap-AI/SAG).
