# SAG-plus

<p align="center">
  <strong>English</strong> · <a href="README-CN.md">简体中文</a>
</p>

SAG-plus is a locally run, personal optimization fork of
[Zleap-AI/SAG](https://github.com/Zleap-AI/SAG) that turns scattered documents
and code into searchable, related, traceable knowledge. The repository is
maintained as a desktop development workspace; development mode is the
supported way to run it.

## Table of contents

- [Introduction](#introduction)
- [Technical background](#technical-background)
- [User guide](#user-guide)
- [Developer guide](#developer-guide)
- [Troubleshooting](#troubleshooting)
- [Contributing and license](#contributing-and-license)

---

<a id="introduction"></a>

## Introduction

### Changelog

**August 2026 · SAG-plus optimization branch**

- Retrieval: LanceDB FTS/BM25 independent recall, async thread execution,
  caching, reranking, and literal-search fallback.
- Context: parent-child chunking with parent backfill, duplicate suppression,
  and incremental enablement.
- Ingestion and storage: persistent vector-write queue, single-writer
  coordination, idempotent recovery, SQLite/LanceDB maintenance, and disk
  protection.
- Evaluation: retrieval evaluation cases, runtime timing output, and related
  regression tests.
- Desktop: converged the user-facing entry point to `npm run dev` in
  `apps/desktop`.
- Integration: self-hosted API, OpenAI-compatible endpoint, MCP, and the
  `skills/sag` Agent Skill documentation.

### SAG-plus in one minute

SAG uses an event-entity index with query-time dynamic hyperedges to deliver
semantic retrieval and relational reasoning in one system, without maintaining
two RAG pipelines. SAG-plus optimizes it for local personal use: semantic
retrieval is complemented by BM25 full-text recall and reranking, parent-child
chunking gives precise child recall with parent context, and a single-writer
vector queue plus maintenance cleanup keeps local storage pressure low.

**Sources & documents → structured knowledge → retrieval with source
traceability → cited agent answers → reuse via API or MCP**

Upload a document once. SAG parses, chunks, embeds, and extracts events and
entities automatically, and every retrieval result can be traced back to the
original text. Search across sources, inspect the event-entity graph, ask
questions with citations, or expose the same knowledge to other applications.

| Capability | What it solves |
| --- | --- |
| Ingestion | File and code-folder sources, parsing, chunking, embedding, event/entity extraction, background processing |
| Retrieval | Global or per-source search with `vector` (semantic) and `multi` (precise) strategies |
| Source traceability | Every result and citation can open its original chunk |
| Knowledge graph | Events, entities, their relations, and an explorable universe mode |
| Agent chat | Multi-turn Q&A grounded in selected sources, with clickable citations |
| Integration | Self-hosted REST/OpenAPI, OpenAI-compatible endpoint, MCP, and the `skills/sag` Agent Skill |

The product targets a local, single-user setup by default. It runs on SQLite
and LanceDB with no external database.

---

<a id="technical-background"></a>

## Technical background

### SAG architecture: event-entity with query-time dynamic hyperedges

Dense RAG relies mostly on semantic similarity over text chunks; GraphRAG adds
an offline graph build but pays for triple extraction, entity merging, global
maintenance, and hard incremental updates. SAG is neither a wrapper nor a
combination of the two:

```text
chunk → one semantically complete event
chunk → multiple indexing entities
event ↔ entities → one potential hyperedge
```

- **Events** carry the full semantics of a chunk instead of being split into
  independent triples.
- **Entities** only index and expand; they do not replace the meaning carried
  by events.
- **Query-time dynamic hyperedges** are formed only at retrieval time, when SQL
  connects events sharing entities into the local structure the current query
  needs. SAG neither prebuilds nor globally maintains these hyperedges.
- **Original evidence** is always the output boundary: selected events map back
  to source chunks for answers and citations.

Paper: [SAG: SQL-Retrieval Augmented Generation with Query-Time Dynamic Hyperedges](https://arxiv.org/abs/2606.15971) · [Benchmark reproduction](https://github.com/Zleap-AI/SAG-Benchmark)

### What SAG-plus improves

| Area | Optimization in this fork |
| --- | --- |
| Retrieval | Semantic retrieval is complemented by LanceDB FTS/BM25, result caching, and literal-search fallback. Reranking can use a local Q8 cross-encoder, a compatible API, or an LLM, with a safe fallback. |
| Context | Parent-child chunking retrieves precise child chunks while returning parent context, with duplicate suppression. |
| Ingestion | Persistent batch/vector-write coordination gives LanceDB a single writer, retries, recovery, and idempotent work items. |
| Code | Tree-sitter symbol-level parent-child chunking, incremental local code-folder sync, and three code-extraction modes with safe release. |
| Storage | SQLite connection and pragma tuning, disk protection, LanceDB cleanup, index maintenance, and read/write observability reduce local-store pressure. |
| Model calls | Four tool-calling strategies control reasoning around tools; three reasoning-history modes can auto-detect DeepSeek, force support for model aliases, or disable the compatibility field. |
| Evaluation | The repository includes retrieval evaluation cases and runtime timing output for measuring changes against local data. |

Detailed implementation records are in
[the 2026 optimization status](docs/SAG_OPTIMIZATION_2026.md) and
[architecture patches](docs/ARCHITECTURE_PATCHES.md).

---

<a id="user-guide"></a>

## User guide

### Start the desktop app (development mode)

Requirements:

- Node.js 20 or later.
- Python 3.11 or later, available as `python` (or set `SAG_PYTHON` to its path).

In Git Bash, run:

```bash
git clone https://github.com/sishuiguozi/SAG-plus.git
cd SAG-plus
cd apps/desktop
npm run dev
```

In Command Prompt (cmd), run:

```cmd
cd /d SAG-plus\apps\desktop
npm run dev
```

In PowerShell, run:

```powershell
cd SAG-plus\apps\desktop
npm run dev
```

To update later: `git pull --ff-only`.

The desktop script starts or reuses the local API (`127.0.0.1:8000`), Web UI
(`127.0.0.1:3000` or `3001`), and Electron window. On the first run it also
checks dependencies, runs `npm ci` where needed, creates `apps/api/.venv`, and
installs the API package. Stop it with `Ctrl+C`.

First use: enter a name to create a local identity → configure any
OpenAI-compatible LLM and Embedding endpoints in **Settings → Model** → create
a source and upload documents, wait until the status becomes **ready** →
search, open the original text, or start a conversation with citations.
Without a model key the UI and services still start.

### Import knowledge

After creating a source you can add Markdown, text, PDF, Office, and other
documents, or import a local code folder. SAG normalizes documents to Markdown,
then chunks, embeds, and extracts events and entities in the background.

- PDF uses MinerU when fully configured and falls back to local MarkItDown
  otherwise; other Office and text formats use MarkItDown by default.
- Code folder ingestion scans a local directory and uploads only new/changed
  files; missing local files are never deleted automatically. Per-source code
  extraction modes: off / comments (default) / all child chunks.
- Choose **Parent-child** chunking for newly ingested documents to get
  child-level recall with broader parent context; existing documents are not
  changed until processed again.
- Manage Tree-sitter language packs under **Settings → Model → Parser model**
  (reserve about 500MB). Once ready, Download/Repair are no-ops.
- Details: [Code folder ingestion guide](docs/guides/CODE_FOLDER_INGESTION.md).

### Search and verify the original text

Search across all sources or restrict to one source, with `vector` (semantic)
and `multi` (precise) strategies. Every result can open its original chunk on
the side, so recall quality can be verified before agents use it.

### Ask questions with citations

The default agent retrieves from its bound sources, streams an answer, and
attaches clickable citations. The same conversation capability is exposed
through the OpenAI-compatible endpoint.

### Explore mode

Explore mode unfolds the whole knowledge base into an interactive knowledge
universe: search events and entities, wander along relations, and open event
details and original text at any time (a shortcut enters it directly).

### View the event-entity graph

Switch a source from list to graph view to inspect the events, entities, and
relations generated by indexing.

### Model configuration

- **Local embedding**: open **Settings → Model configuration → Local
  embedding**, install the inference backend, then download BGE-M3 Q8,
  Qwen3-Embedding-0.6B Q8, or Qwen3-Embedding-4B Q8. Weights are never
  downloaded automatically; select a completed model and save. Click **Test
  local model** to test the currently selected model against the current
  context—no save required; it generates one temporary vector only.
- **Retrieval reranking**: open **Settings → Model configuration → Retrieval
  reranking**. Choose a local BGE/Qwen Q8 cross-encoder, a compatible Rerank
  API using its full URL (including Qwen and vLLM), or legacy LLM numbered
  reranking. Local rerank models download separately; their native rank runtime
  is built only after you explicitly install it.
- **Tool calling strategy**: under **Settings → Model configuration → Generation
  parameters → Tool calling strategy**, choose how reasoning behaves around
  tools. The recommended mode keeps reasoning for normal answers, disables it
  only for forced tool selection, and restores it for the post-tool answer.
  **Reasoning history compatibility** defaults to DeepSeek auto-detection; use
  **Always enable** for aliased compatible models or **Off** when an endpoint
  rejects `reasoning_content`.

### Knowledge base data location

- The desktop app stores knowledge-base data under `data` in the application
  data directory by default: metadata DB, engine index, uploads, and local
  models.
- In **Settings → System → Knowledge base data location**, choose or enter a
  new root directory; save and restart for it to take effect.
- Switching does not migrate old data automatically; to keep the existing
  knowledge base, copy the old root to the new location before restarting.
- Development mode (`npm run dev`) honors the same setting, injecting it into
  the API on the next start; you can also set `SAG_DATA_ROOT` directly in
  `apps/api/.env`.
- It derives `SAG_DATABASE_URL` (`{root}/sag.db`), `SAG_DATA_DIR`
  (`{root}/engine`), `SAG_UPLOAD_DIR` (`{root}/uploads`), and the models
  directory (`{root}/models`).

### MCP guide

SAG-plus exposes the whole knowledge base (or a single source) as a standard
read-only MCP server with 8 tools, so any MCP-capable host (Claude Desktop,
Cursor, Dify, …) can query it.

#### As an Agent Skill (Claude Code / Codex, etc.)

The repository ships an official Skill ([`skills/sag/`](skills/sag/)) that
teaches agents the 8 read-only MCP tools: confirm scope with `list_sources`
first, then follow the `list_documents → outline → search/grep →
get_chunk/read` exploration funnel to locate and cite knowledge. Copy the
directory into the agent’s skills directory:

```bash
# Claude Code
cp -R skills/sag ~/.claude/skills/sag-knowledge

# Codex
cp -R skills/sag ~/.codex/skills/sag-knowledge
```

The Skill includes a tool parameter reference
(`references/mcp-tools.md`) and query strategies
(`references/search-strategies.md`).

#### Mount MCP directly

You can also mount without the Skill. Open **Settings → Integrations →
Knowledge base MCP** in SAG, choose HTTP or stdio, and copy the full config.
The copied HTTP config carries the current JWT automatically, covers all
sources by default, and can be narrowed with `?source_id=`.

- **HTTP (recommended)**: `http://<host>/mcp/` (whole KB) or
  `http://<host>/mcp/?source_id=<SOURCE_ID>` (single source), header
  `Authorization: Bearer <TOKEN>`
- **stdio**: `python -m sag_api.mcp.server` (whole KB by default;
  `SAG_MCP_SOURCE_ID=<SOURCE_ID>` limits to one source; requires the `apps/api`
  environment)
- Descriptors: `GET /api/v1/system/mcp` (whole KB),
  `GET /api/v1/sources/{source_id}/mcp` (single source)

| Tool | Parameters | Purpose |
| --- | --- | --- |
| `list_sources` | — | List accessible sources and their `source_id`. |
| `list_documents` | `source_id?` | List documents (id / status / chunk count). |
| `outline` | `document_id` | Document outline (headings + `chunk_id`). |
| `search` | `query, top_k=8, source_id?` | Semantic retrieval, returns numbered evidence. |
| `grep` | `pattern, limit=20, source_id?` | Literal search (names / numbers / code). |
| `get_chunk` | `chunk_id, source_id?` | Read one chunk’s full text. |
| `read` | `document_id, offset=1, limit=120` | Read the original file line by line. |
| `get_entity` | `name, source_id?` | Look up an entity (person / org / concept). |

### Call an agent as a model (OpenAI-compatible)

Any agent can be called like an OpenAI “model with citations”:

```bash
curl -s http://localhost:8000/api/v1/openai/<AGENT_ID>/chat/completions \
  -H "Authorization: Bearer <SAG_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What does this document say?"}]}'
```

It returns a standard `chat.completion` with an extra `sag.citations` field
that standard clients ignore; set `"stream": true` for SSE chunks. This
endpoint is stateless (no thread is persisted).

---

<a id="developer-guide"></a>

## Developer guide

### System boundary

SAG-plus uses a Next.js frontend separated from a FastAPI backend. The backend
is a reference application built on the public Python engine `zleap-sag`.
Developers can keep the whole backend and build their own frontend, or embed
`zleap-sag` directly in their own Python service.

### Repository layout

```text
apps/
├── web/                    Next.js 15 + React 19 product frontend
├── desktop/                Electron shell and local runtime lifecycle
└── api/
    ├── sag_api/
    │   ├── api/v1/         FastAPI HTTP routes and serialization
    │   ├── connectors/     File/web source connectors and registry
    │   ├── parsing/        MarkItDown and MinerU document normalization
    │   ├── jobs/           Background ingest → extract state machine
    │   ├── sag/            The only in-app layer importing zleap-sag
    │   ├── generation/     Retrieved evidence → streamed cited answers
    │   ├── mcp/            Knowledge-base MCP server and HTTP mount
    │   ├── services/       Application and domain orchestration
    │   └── tools/          Built-in tools and remote MCP agent tools
    └── sag_agent/          Framework-agnostic agent runtime core
skills/sag/                 Agent Skill for exploring SAG over MCP
scripts/                    Repository-level tooling scripts
docs/                       Optimization status, architecture patches, and guides
```

The core dependency rule is simple: the application reaches the knowledge
engine only through `apps/api/sag_api/sag/`; the engine knows nothing about
FastAPI, the Web UI, users, conversations, or citations.

### Local development

Desktop development is the supported run entry:

```bash
cd apps/desktop
npm run dev
```

Common checks:

```bash
cd apps/api && .venv/Scripts/python -m pytest
cd apps/api && .venv/Scripts/python -m ruff check .
cd apps/web && npm run typecheck
```

### Desktop client

The Electron client runs the same Next.js app with a local FastAPI backend.
Desktop development, data directories, and run instructions are documented in
[`apps/desktop/README.md`](apps/desktop/README.md).

### Build your own frontend on the SAG backend (self-hosted API)

Browsers cannot import Python packages directly. A custom frontend should call
a Python HTTP service that owns the `DataEngine`; the FastAPI backend in this
repository is the reference implementation and is already separated from the
Next.js frontend.

Once SAG is running, the self-hosted API is available at:

| Entry | URL |
| --- | --- |
| API Base | `http://localhost:8000/api/v1` |
| Interactive OpenAPI | [http://localhost:8000/docs](http://localhost:8000/docs) |
| OpenAPI Schema | [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json) |
| MCP Streamable HTTP | `http://localhost:8000/mcp/` |

This is a **self-hosted API**, not a public cloud API operated by the project.
Most endpoints require a SAG JWT:

```bash
curl -s http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"name":"Developer"}'
```

Copy `access_token` from the response and send it on later requests:

```http
Authorization: Bearer <SAG_TOKEN>
```

#### API map

| Area | Main routes | Purpose |
| --- | --- | --- |
| System | `GET /system/health`, `/system/ready`, `/system/capabilities` | Health status and current engine capabilities |
| Auth | `POST /auth/login`, `GET /auth/me` | Local identity and JWT |
| Sources | `GET/POST /sources`, `GET/PATCH/DELETE /sources/{id}` | Source lifecycle |
| Documents | `/sources/{id}/documents` and `/sources/{id}/documents/ingest` | File upload, continuous text/message writes, reprocessing, deletion |
| Search | `POST /search`, `POST /sources/{id}/search` | `vector` / `multi` retrieval globally or per source |
| Graph | `GET /sources/{id}/entities`, `/sources/{id}/graph` | Inspect the event-entity structure |
| Agent | `/agents`, `/threads`, `/ask` | Agent configuration, threads, SSE runs, and citations |
| OpenAI-compatible | `POST /openai/{agent_id}/chat/completions` | Call any SAG agent as a model with citations; streaming supported |
| MCP | `/mcp/` or `/mcp/?source_id={id}` | Expose the whole KB or a single source to MCP hosts |

Create a source, ingest text, and search:

```bash
BASE=http://localhost:8000/api/v1
TOKEN=<SAG_TOKEN>

SOURCE_ID=$(curl -s -X POST "$BASE/sources" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Product docs"}' | jq -r .id)

curl -s -X POST "$BASE/sources/$SOURCE_ID/documents/ingest" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"SAG","text":"SAG uses an event-entity index and dynamic hyperedges at query time."}'

curl -s -X POST "$BASE/sources/$SOURCE_ID/search" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"How does SAG retrieve knowledge?","strategy":"multi","top_k":5}'
```

Document writes are handled by a background task queue. Check the returned
document status or its job before expecting search results.

If your custom frontend is served from a different origin, add it to
`SAG_CORS_ORIGINS`. When the API address changes, rebuild the Web frontend
with the matching `NEXT_PUBLIC_API_BASE`.

---

<a id="troubleshooting"></a>

## Troubleshooting

| Symptom | Check |
| --- | --- |
| API does not start | Confirm Python 3.11+ is available and port 8000 is not already occupied by an unrelated process. The launcher recreates its virtual environment when needed. |
| Web does not start | The launcher installs missing Web dependencies; it can use port 3000 or 3001. |
| Electron closes immediately | Run the command from the repository's `apps\desktop` directory and read the first failing API or Web process in the terminal. |
| Old knowledge is missing | Local data was not copied by Git. Verify the intended local data directory before creating a new index. |
| Local embeddings are unavailable | In Settings, install the llama-cpp-python backend, download a selected model, choose that file, then save. |
| Local reranking is unavailable | In **Retrieval reranking**, download the selected model and install the native reranker runtime. Retrieval keeps its fused order if either is unavailable. |

---

<a id="contributing-and-license"></a>

## Contributing and license

- Contribution flow: [CONTRIBUTING.md](CONTRIBUTING.md)
- Python engine: [`zleap-sag` on PyPI](https://pypi.org/project/zleap-sag/)
- Benchmark reproduction: [Zleap-AI/SAG-Benchmark](https://github.com/Zleap-AI/SAG-Benchmark)

SAG-plus is licensed under the [MIT License](LICENSE). Upstream attribution
remains with [Zleap-AI/SAG](https://github.com/Zleap-AI/SAG).

## Development references

- [Desktop development](apps/desktop/README.md)
- [API architecture](apps/api/README.md)
- [Optimization status](docs/SAG_OPTIMIZATION_2026.md)
- [Optimization plan](docs/SAG_OPTIMIZATION_PLAN.md)
