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

## Code folder ingestion

- Use **Import code folder** in a knowledge source to scan a local directory and upload only new/changed files.
- Missing local files are never deleted automatically.
- Per-source code extraction modes: off / comments (default) / all child chunks.
- Manage Tree-sitter language packs under **Settings → Model → Parser model** (reserve about 500MB). Once ready, Download/Repair are no-ops.
- Details: [Code folder ingestion guide](docs/guides/CODE_FOLDER_INGESTION.md).

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

## Self-hosted API

Browsers cannot import Python packages directly. A custom frontend should call a
Python HTTP service that owns the `DataEngine`; the FastAPI backend in this
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

### API map

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
`SAG_CORS_ORIGINS`. When the API address changes, rebuild the Web image with the
matching `NEXT_PUBLIC_API_BASE`.

### PostgreSQL / pgvector deployment

An optional production overlay migrates application metadata and the knowledge
engine to PostgreSQL/pgvector:

```bash
cp .env.example .env
openssl rand -hex 32   # fill SAG_SECRET_KEY
openssl rand -hex 24   # fill POSTGRES_PASSWORD

docker compose -f compose.yaml -f compose.postgres.yaml config
docker compose -f compose.yaml -f compose.postgres.yaml up -d --build
```

Before serving, set real `SAG_CORS_ORIGINS` and `NEXT_PUBLIC_API_BASE` values.
Back up both `pgdata` and `sagdata` before upgrading.

## MCP & Skill interface

SAG-plus exposes the whole knowledge base (or a single source) as a standard
read-only MCP server with 8 tools, so any MCP-capable host (Claude Desktop,
Cursor, Dify, …) can query it.

### Connect

- In the Web UI, open **Settings → Integrations** and copy the ready-made
  config (HTTP or stdio, auth header included).
- Or fetch the descriptor programmatically:
  - Whole knowledge base: `GET /api/v1/system/mcp`
  - Single source: `GET /api/v1/sources/{source_id}/mcp`
- **HTTP (recommended)**: `http://<host>/mcp/` (whole KB) or
  `http://<host>/mcp/?source_id=<SOURCE_ID>` (single source), header
  `Authorization: Bearer <SAG_TOKEN>`.
- **stdio**: `python -m sag_api.mcp.server` (whole KB by default; set
  `SAG_MCP_SOURCE_ID=<SOURCE_ID>` to limit to one source; requires the
  `apps/api` Python environment).

### MCP tools (all read-only)

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

Recommended call order (exploration funnel): `list_sources` →
`list_documents` → `outline` → `search`/`grep` → `get_chunk`/`read`.

### Agent tool bindings (mount sources / external MCP servers)

Agents can mount knowledge sources or external MCP servers as tool sources
(`sag_api/mcp/` client + `apps/web` “Settings → Agents”). The REST
interface mirrors the UI:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/agents` | List agents |
| `POST /api/v1/agents` | Create an agent |
| `GET /api/v1/agents/default` | Get the default agent |
| `GET /api/v1/agents/{agent_id}` | Get an agent |
| `PATCH /api/v1/agents/{agent_id}` | Update an agent |
| `DELETE /api/v1/agents/{agent_id}` | Delete an agent |
| `GET /api/v1/agents/{agent_id}/bindings` | List bindings |
| `POST /api/v1/agents/{agent_id}/bindings` | Add a binding |
| `DELETE /api/v1/agents/{agent_id}/bindings/{binding_id}` | Remove a binding |

Binding body (`POST /api/v1/agents/{agent_id}/bindings`):

```json
{
  "target_type": "source",            // or "mcp_server"
  "target_id": "<source_id>",         // source id, or a display name for mcp_server
  "config": {}
}
```

- `target_type: "source"` binds a knowledge-base source (config stays empty).
- `target_type: "mcp_server"` connects an external MCP server as a tool source;
  `config` must provide `"url"` (streamable HTTP) or `"command"` (stdio), plus
  optional `"args"` and `"env"`.

### OpenAI-compatible chat endpoint

Any agent can be called like an OpenAI “model with citations”:

```
POST /api/v1/openai/{agent_id}/chat/completions
Authorization: Bearer <SAG_TOKEN>
```

Request body follows OpenAI Chat Completions (`messages`, `model?`, `stream?`,
`temperature?`, `max_tokens?`). It runs the same retrieval, system prompt, and
anti-hallucination short-circuit as in-app chat. `stream: true` returns SSE
`chat.completion.chunk` events; otherwise it returns a standard
`chat.completion` object with a SAG extension field `sag.citations` for
traceable sources. This endpoint is stateless (no thread is persisted).

### Built-in skill

The repository ships the `skills/sag` skill (name: `sag-knowledge`). Its
`SKILL.md` teaches an agent how to search, browse, cite, and read knowledge-base
documents through the MCP tools above, with a funnel workflow, a tool parameter
reference (`references/mcp-tools.md`), and query strategies
(`references/search-strategies.md`). Point your agent’s skills directory at
`skills/sag` to enable it.

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
