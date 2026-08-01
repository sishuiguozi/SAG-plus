# SAG-plus

<p align="center">
  <a href="README.md">English</a> · <strong>简体中文</strong>
</p>

SAG-plus 是基于 [Zleap-AI/SAG](https://github.com/Zleap-AI/SAG) 的本地个人优化分支，把分散的文档与代码变成可搜索、可关联、可追溯的知识。当前仓库定位为桌面开发工作区，同时保留自托管 Docker 部署。

## 目录

- [项目介绍](#项目介绍)
- [技术原理](#技术原理)
- [用户指南](#用户指南)
- [开发者指南](#开发者指南)
- [常见问题](#常见问题)
- [参与贡献与许可](#参与贡献与许可)

---

<a id="项目介绍"></a>

## 项目介绍

### 更新日志

**2026 年 8 月 · SAG-plus optimization branch**

- 检索：加入 LanceDB FTS/BM25 独立召回、异步线程执行、缓存、重排和字面检索回退。
- 上下文：加入父子分块、子块命中回填父块、重复命中抑制与增量启用策略。
- 写入与存储：加入向量写入队列、单写者协调、幂等恢复、SQLite/LanceDB 维护和磁盘保护。
- 评估：加入检索评估样例、运行耗时输出和相关回归测试。
- 桌面：将用户运行入口收敛为 `apps/desktop` 中的 `npm run dev`。
- 对外集成：补充自托管 API、OpenAI 兼容接口、MCP 与 `skills/sag` Agent Skill 文档。

### 一分钟了解 SAG-plus

SAG 通过 event-entity 索引与查询时动态超边，在一个系统中同时实现语义检索与关系推理，不再需要维护两套 RAG 系统。SAG-plus 在此基础上针对本地个人使用做了优化：语义检索叠加 BM25 全文召回与重排，父子分块让子块精确召回、父块提供上下文，单写者向量队列与维护清理控制本地存储压力。

**信源与文档 → 结构化知识 → 检索与原文溯源 → 带引用的 Agent 回答 → 通过 API 或 MCP 复用**

文档只需上传一次。SAG 会自动解析、分块、向量化，抽取事件与实体，并让每一条检索结果都能回到原文。你可以跨信源搜索、查看 event-entity 图谱、进行带引用的问答，也可以把同一份知识开放给其他应用。

| 能力 | 解决的问题 |
| --- | --- |
| 知识导入 | 文件与代码文件夹信源、解析、分块、向量化、事件/实体抽取、后台处理 |
| 检索 | 全局或指定信源检索，支持 `vector`（语义）与 `multi`（精确）两种模式 |
| 原文溯源 | 每条检索结果和引用都能打开对应的原文块 |
| 知识图谱 | 查看事件、实体及其关联，以及可漫游的探索模式 |
| Agent 对话 | 基于指定信源进行多轮问答，并提供可点击引用 |
| 对外集成 | 自托管 REST/OpenAPI、OpenAI 兼容接口、MCP 与 `skills/sag` Agent Skill |

产品默认面向本地单用户场景。它使用 SQLite 与 LanceDB 即可启动，不依赖外部数据库，同时保留迁移至 PostgreSQL/pgvector 的路径。

---

<a id="技术原理"></a>

## 技术原理

### SAG 架构：event-entity 与查询时动态超边

传统稠密 RAG 主要依靠语义相似度召回文本块。GraphRAG 在此基础上引入离线图谱构建，却要承担三元组抽取、实体合并、全局维护和增量更新困难等成本。SAG 不是对这两套系统的封装或组合：

```text
chunk → 一个语义完整的 event
chunk → 多个用于索引的 entities
event ↔ entities → 一条潜在超边
```

- **事件（event）** 承载一个 chunk 的完整语义，不再被拆成彼此独立的三元组。
- **实体（entity）** 只负责索引和扩展，不替代事件所承载的完整含义。
- **查询时动态超边** 只在检索发生时，通过 SQL 将共享实体的事件连接成当前查询需要的局部结构。SAG 不预先构建、也不全局维护这些超边。
- **原文证据** 始终是输出边界。被选中的事件最终映射回原始 chunk，用于生成回答和引用。

论文：[SAG: SQL-Retrieval Augmented Generation with Query-Time Dynamic Hyperedges](https://arxiv.org/abs/2606.15971) · [复现跑分](https://github.com/Zleap-AI/SAG-Benchmark)

### SAG-plus 的重点优化

| 方向 | 本分支的实现 |
| --- | --- |
| 检索 | 语义检索叠加 LanceDB FTS/BM25、结果缓存与字面检索回退；重排可选本地 Q8 Cross-Encoder、兼容 API 或 LLM，失败安全回退。 |
| 上下文 | 父子分块以子块完成精确召回、以父块提供完整上下文，并自动消除重复命中。 |
| 入库 | 持久化批量向量写入协调器为 LanceDB 提供单写者、重试、恢复和幂等工作项。 |
| 代码 | Tree-sitter 符号级父子分块、本地代码文件夹增量同步、三档代码抽取与版本安全发布。 |
| 存储 | SQLite 连接与 PRAGMA 调优、磁盘保护、LanceDB 清理、索引维护和读写可观测性降低本地存储压力。 |
| 模型调用 | 四档工具调用策略可分别控制普通回答、指定工具轮和工具后回答的思考状态；三档推理历史兼容可自动识别 DeepSeek、强制覆盖模型别名或完全关闭。 |
| 评估 | 仓库包含检索评估样例与运行耗时输出，可基于本地数据衡量优化效果。 |

实现细节见 [2026 优化状态](docs/SAG_OPTIMIZATION_2026.md) 与 [架构补丁](docs/ARCHITECTURE_PATCHES.md)。

---

<a id="用户指南"></a>

## 用户指南

### 启动桌面应用（开发模式）

前提条件：

- Node.js 20 或更高版本。
- Python 3.11 或更高版本，可通过 `python` 命令访问（或用 `SAG_PYTHON` 指定路径）。

在 Git Bash 中运行：

```bash
cd /e/SAG-plus/apps/desktop
npm run dev
```

桌面脚本会启动或复用本地 API（`127.0.0.1:8000`）、Web（`127.0.0.1:3000` 或 `3001`）和 Electron 窗口。首次运行会自动检查依赖、在需要时执行 `npm ci`、创建 `apps/api/.venv` 并安装 API 包。按 `Ctrl+C` 停止。

### 快速开始（Docker，自托管）

准备 Docker Desktop，或 Docker Engine 与 Compose v2：

```bash
git clone https://github.com/sishuiguozi/SAG-plus.git
cd SAG-plus
docker compose up -d --build
```

两个服务健康后打开：

- Web 应用：[http://localhost:3000](http://localhost:3000)
- API 文档：[http://localhost:8000/docs](http://localhost:8000/docs)

首次使用：

1. 填写名字，创建或恢复本地身份。
2. 进入 **设置 → 模型**，填写任意 OpenAI 兼容的 LLM 与 Embedding 接口（Docker 默认指向 302.AI）。
3. 创建信源并上传文档，等待状态变为**就绪**。
4. 开始检索、打开原文，或直接进行带引用的对话。

没有模型密钥时，界面和服务仍可启动。Embedding 用于索引与向量检索；LLM 用于事件抽取、查询理解和生成回答。

### 导入知识

创建信源后，可以添加 Markdown、文本、PDF、Office 等文档，也可以导入本地代码文件夹。SAG 会先将文档规范化为 Markdown，再在后台完成分块、向量化、事件抽取和实体抽取。

- PDF 在 MinerU 配置完整时优先使用 MinerU；未配置或解析失败时自动回退本地 MarkItDown。其他 Office 和文本格式默认使用 MarkItDown。
- 代码文件夹入库：选择“导入代码文件夹”，系统会扫描本地目录并只上传新增/变更文件；默认不会因本地文件消失而删除知识库内容。代码抽取策略可按知识库设置：关闭 / 仅注释（推荐）/ 全部子块。
- 对新入库文档选择「父子分块」，即可获得子块精确召回与父块完整上下文；旧文档需要重新处理才会拥有父子关系。
- Tree-sitter 语言包在 **设置 → 模型 → 解析模型** 管理，请预留约 500MB；就绪后不会整包重下。
- 详细说明见 [代码文件夹入库指南](docs/guides/CODE_FOLDER_INGESTION.md)。

### 检索并核对原文

可以跨全部信源检索，也可以只搜索指定信源，支持 `vector`（语义）与 `multi`（精确）两种模式。每一条结果都能打开对应原文块，让 Agent 使用前的召回质量可以被直接核验。

### 进行带引用的问答

默认 Agent 会检索绑定的知识来源、流式生成回答，并附上可点击引用。同一套对话能力也通过 OpenAI 兼容接口开放。

### 探索模式

探索模式会将整个知识库展开为可交互的知识宇宙：搜索事件与实体、沿关联关系漫游，并随时打开事件详情与原文（快捷键可直接进入）。

### 查看 event-entity 图谱

在信源中从列表切换到图谱，可以查看索引生成的事件、实体和关联关系。

### 模型配置

- **本地嵌入**：打开 **设置 → 模型配置 → 本地嵌入**，先点击「下载推理后端」，再选择 BGE-M3 Q8、Qwen3-Embedding-0.6B Q8 或 Qwen3-Embedding-4B Q8 并下载。模型权重绝不自动下载；下载完成后选择所需模型并保存配置。点击「测试本地模型」会使用当前选中的模型基于当前上下文或线程草稿测试，无需先保存；测试只生成一次临时向量，不改变已保存配置或知识库。
- **检索重排**：打开 **设置 → 模型配置 → 检索重排**，可选择本地 BGE/Qwen Q8 Cross-Encoder、完整 URL 的兼容 Rerank API（含 Qwen/vLLM），或旧 LLM 编号重排。本地重排模型与向量模型分开下载；原生 rank 运行时仅在点击安装后构建，测试同样无需先保存。
- **工具调用策略**：在 **设置 → 模型配置 → 生成参数 → 工具调用策略** 选择思考行为。默认“工具轮关闭思考”会保留普通回答的思考，只在指定工具轮关闭，并在工具后的回答恢复；也可选择全程保留、自动工具选择或全程关闭。“推理历史兼容”默认自动识别 DeepSeek；接口使用模型别名时选择“始终启用”，接口不接受 `reasoning_content` 时选择“关闭”。

### 知识库数据位置

- 桌面端默认把知识库数据保存在应用数据目录下的 `data` 文件夹，元数据库、引擎索引、上传文件与本地模型都位于其中。
- 在 **设置 → 系统 → 知识库数据位置** 可选择或填写新的根目录，保存后重启应用生效。
- 切换位置不会自动搬迁旧数据；若要沿用现有知识库，请先把旧根目录内容复制到新位置再重启。
- 开发模式（`npm run dev`）同样生效：界面保存的位置会在下次启动时注入 API；也可直接在 `apps/api/.env` 设置 `SAG_DATA_ROOT`。
- 设置后自动派生 `SAG_DATABASE_URL`（`{root}/sag.db`）、`SAG_DATA_DIR`（`{root}/engine`）与 `SAG_UPLOAD_DIR`（`{root}/uploads`），模型目录为 `{root}/models`。

### MCP 指南

SAG-plus 把整个知识库（或单个信源）暴露为标准只读 MCP server，共 8 个工具，任何支持 MCP 的宿主（Claude Desktop / Cursor / Dify 等）都可以接入。

#### 作为 Agent Skill（Claude Code / Codex 等）

仓库自带官方 Skill（[`skills/sag/`](skills/sag/)），教 Agent 使用 8 个只读 MCP 工具：先通过 `list_sources` 确认可访问范围，再沿 `list_documents → outline → search/grep → get_chunk/read` 的探索漏斗定位并引用知识。复制该目录到 Agent 的 skills 目录即可启用：

```bash
# Claude Code
cp -R skills/sag ~/.claude/skills/sag-knowledge

# Codex
cp -R skills/sag ~/.codex/skills/sag-knowledge
```

Skill 内包含工具参数参考（`references/mcp-tools.md`）与查询策略（`references/search-strategies.md`）。

#### Agent 直接挂载 MCP

不安装 Skill 也可以直接挂载。在 SAG 中打开 **设置 → 集成 → 知识库 MCP**，选择 HTTP 或 stdio 并复制完整配置。复制的 HTTP 配置会自动带入当前 JWT，默认开放全部信源，也可以通过 `?source_id=` 限定范围。

- **HTTP（推荐）**：`http://<host>/mcp/`（全库）或 `http://<host>/mcp/?source_id=<SOURCE_ID>`（单信源），Header `Authorization: Bearer <TOKEN>`
- **stdio**：`python -m sag_api.mcp.server`（默认全库；`SAG_MCP_SOURCE_ID=<SOURCE_ID>` 限定单信源，需 `apps/api` 环境）
- 描述符接口：`GET /api/v1/system/mcp`（全库）、`GET /api/v1/sources/{source_id}/mcp`（单信源）

| 工具 | 参数 | 用途 |
| --- | --- | --- |
| `list_sources` | — | 查看可访问的信源及其 `source_id` |
| `list_documents` | `source_id?` | 列出文档（id / 状态 / 分块数） |
| `outline` | `document_id` | 文档大纲（章节 + `chunk_id`） |
| `search` | `query, top_k=8, source_id?` | 语义检索，返回带编号证据 |
| `grep` | `pattern, limit=20, source_id?` | 原文精确查找（专名 / 编号 / 代码） |
| `get_chunk` | `chunk_id, source_id?` | 读取单个分块的完整原文 |
| `read` | `document_id, offset=1, limit=120` | 按行分页读取原始文件 |
| `get_entity` | `name, source_id?` | 查询实体（人物 / 组织 / 概念） |

### 作为模型被调用（OpenAI 兼容）

任意 Agent 都可以当作“带引用的模型”调用：

```bash
curl -s http://localhost:8000/api/v1/openai/<AGENT_ID>/chat/completions \
  -H "Authorization: Bearer <SAG_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"这份资料讲了什么？"}]}'
```

返回标准 `chat.completion`，并额外提供 `sag.citations` 引用字段；标准客户端会忽略未知字段。设置 `"stream": true` 后以 SSE 分块返回。该端点无状态（不落库）。

### 运行与更新

```bash
docker compose ps                  # api 和 web 应显示 healthy
docker compose logs -f api web     # 持续查看日志
docker compose restart             # 重启服务
docker compose down                # 停止并保留全部数据

git pull --ff-only                 # 更新本地代码
docker compose up -d --build       # 重建服务，不删除数据卷
```

默认持久化方式：

| 运行方式 | 应用元数据 | 知识引擎 | 保存位置 |
| --- | --- | --- | --- |
| Docker 默认 | SQLite | SQLite + LanceDB | Docker 数据卷 `sagdata` |
| 本地开发 | SQLite | SQLite + LanceDB | 应用数据目录 `data` 或设置的知识库数据位置 |
| PostgreSQL 覆盖 | PostgreSQL | PostgreSQL + pgvector | `pgdata` 与 `sagdata` 数据卷 |

`docker compose down` 会保留数据。**`docker compose down -v` 会永久删除数据库、知识索引和已上传文件。**

### 网络与生产安全

默认 Compose 只将 3000 和 8000 端口绑定到 `127.0.0.1`。SAG 当前是本地单用户产品，不要把这两个端口直接暴露到公网。

需要自定义端口或在受信局域网访问时：

```bash
cp .env.example .env
# 修改 BIND_ADDRESS、WEB_PORT、API_PORT、SAG_CORS_ORIGINS 和 NEXT_PUBLIC_API_BASE。
docker compose up -d --build
```

`NEXT_PUBLIC_API_BASE` 会在构建时写入 Web 镜像，因此修改后必须带 `--build`。服务器部署还应配置 HTTPS，以及 VPN、IP 白名单或反向代理认证等外部访问控制。

---

<a id="开发者指南"></a>

## 开发者指南

### 系统边界

SAG-plus 采用 Next.js 前端与 FastAPI 后端分离的架构。后端是基于公开 Python 引擎 `zleap-sag` 制作的参考应用。开发者既可以保留整个后端、制作自己的前端，也可以在自己的 Python 服务中直接嵌入 `zleap-sag`。

### 代码库结构

```text
apps/
├── web/                    Next.js 15 + React 19 产品前端
├── desktop/                Electron 桌面壳、打包与本地运行时生命周期
└── api/
    ├── sag_api/
    │   ├── api/v1/         FastAPI HTTP 路由与序列化
    │   ├── connectors/     文件/网页信源连接器与注册表
    │   ├── parsing/        MarkItDown 与 MinerU 文档规范化
    │   ├── jobs/           后台 ingest → extract 状态机
    │   ├── sag/            应用内唯一导入 zleap-sag 的适配层
    │   ├── generation/     检索证据 → 流式带引用回答
    │   ├── mcp/            知识库 MCP Server 与 HTTP 挂载
    │   ├── services/       应用与领域编排
    │   └── tools/          内置工具与远端 MCP Agent 工具
    └── sag_agent/          与框架无关的 Agent Runtime Core
skills/sag/                 通过 MCP 探索 SAG 的 Agent Skill
scripts/                    仓库级工具脚本
docs/                       优化状态、架构补丁与使用指南
```

核心依赖规则：应用只能通过 `apps/api/sag_api/sag/` 访问知识引擎；引擎不知道 FastAPI、Web UI、用户、对话和引用的存在。

### 本地开发

桌面开发是当前唯一支持的运行入口：

```bash
cd apps/desktop
npm run dev
```

常用检查：

```bash
cd apps/api && .venv/Scripts/python -m pytest
cd apps/api && .venv/Scripts/python -m ruff check .
cd apps/web && npm run typecheck
```

### 桌面客户端

Electron 客户端将同一套 Next.js 应用与本地 FastAPI 后端一起打包。桌面开发、分平台发布构建、签名、更新配置和数据目录见 [`apps/desktop/README.md`](apps/desktop/README.md)。

### 基于 SAG 后端制作自己的前端（自托管 API）

浏览器不能直接导入 Python 包。自定义前端应调用一个持有 `DataEngine` 的 Python HTTP 服务。本仓库的 FastAPI 后端就是参考实现，并且已经与 Next.js 前端分离。

启动 SAG 后即可使用自托管 API：

| 入口 | 地址 |
| --- | --- |
| API Base | `http://localhost:8000/api/v1` |
| 交互式 OpenAPI | [http://localhost:8000/docs](http://localhost:8000/docs) |
| OpenAPI Schema | [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json) |
| MCP Streamable HTTP | `http://localhost:8000/mcp/` |

这是**自托管 API**，不是由项目方托管的公共云 API。大部分接口需要 SAG JWT：

```bash
curl -s http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"name":"Developer"}'
```

从响应中复制 `access_token`，后续请求携带：

```http
Authorization: Bearer <SAG_TOKEN>
```

#### API 地图

| 领域 | 主要路由 | 用途 |
| --- | --- | --- |
| 系统 | `GET /system/health`、`/system/ready`、`/system/capabilities` | 健康状态与当前引擎能力 |
| 身份 | `POST /auth/login`、`GET /auth/me` | 本地身份与 JWT |
| 信源 | `GET/POST /sources`、`GET/PATCH/DELETE /sources/{id}` | 信源生命周期 |
| 文档 | `/sources/{id}/documents` 与 `/sources/{id}/documents/ingest` | 文件上传、持续文本/消息写入、重新处理、删除 |
| 检索 | `POST /search`、`POST /sources/{id}/search` | 全局或指定信源的 `vector`/`multi` 检索 |
| 图谱 | `GET /sources/{id}/entities`、`/sources/{id}/graph` | 查看 event-entity 结构 |
| Agent | `/agents`、`/threads`、`/ask` | Agent 配置、会话、SSE 运行与引用 |
| OpenAI 兼容 | `POST /openai/{agent_id}/chat/completions` | 将任意 SAG Agent 作为带引用模型调用，支持流式 |
| MCP | `/mcp/` 或 `/mcp/?source_id={id}` | 将整个知识库或单个信源开放给 MCP 宿主 |

创建信源、持续写入文本并执行检索：

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
  -d '{"title":"SAG","text":"SAG 使用 event-entity 索引与查询时动态超边。"}'

curl -s -X POST "$BASE/sources/$SOURCE_ID/search" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"SAG 如何检索知识？","strategy":"multi","top_k":5}'
```

文档写入由后台任务队列处理。在期待检索结果前，请检查返回的文档状态或对应任务是否完成。

如果自定义前端与 API 不同源，请将前端地址加入 `SAG_CORS_ORIGINS`。API 地址改变时，还要用对应的 `NEXT_PUBLIC_API_BASE` 重新构建 Web 镜像。

### PostgreSQL/pgvector 部署

可选的生产覆盖会将应用元数据与知识引擎迁移到 PostgreSQL/pgvector：

```bash
cp .env.example .env
openssl rand -hex 32   # 填入 SAG_SECRET_KEY
openssl rand -hex 24   # 填入 POSTGRES_PASSWORD

docker compose -f compose.yaml -f compose.postgres.yaml config
docker compose -f compose.yaml -f compose.postgres.yaml up -d --build
```

服务器部署前应设置真实的 `SAG_CORS_ORIGINS` 与 `NEXT_PUBLIC_API_BASE`。升级前同时备份 `pgdata` 和 `sagdata`。

---

<a id="常见问题"></a>

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| API 没有启动 | 确认 Python 3.11+ 可用，且 8000 端口未被无关进程占用；启动器会在需要时重建虚拟环境。 |
| Web 没有启动 | 启动器会安装缺失的 Web 依赖；桌面启动器会使用 3000 或 3001 端口。 |
| Electron 立即退出 | 确认命令在 `E:\SAG-plus\apps\desktop` 执行，并查看终端中最先失败的 API 或 Web 进程。 |
| 旧知识库没有显示 | Git 不会迁移本地数据；创建新索引前先确认实际使用的数据目录。 |
| 本地嵌入不可用 | 在设置中安装 llama-cpp-python 后端、下载模型，选择对应文件并保存。 |
| 本地重排未就绪 | 在「检索重排」中下载所选模型并安装原生重排运行时；若运行时或模型不可用，检索会保留融合排序。 |

---

<a id="参与贡献与许可"></a>

## 参与贡献与许可

- 贡献流程：[CONTRIBUTING.md](CONTRIBUTING.md)
- Python 引擎：[`zleap-sag` PyPI](https://pypi.org/project/zleap-sag/)
- 论文复现：[Zleap-AI/SAG-Benchmark](https://github.com/Zleap-AI/SAG-Benchmark)

SAG-plus 使用 [MIT License](LICENSE)；上游归属保留给 [Zleap-AI/SAG](https://github.com/Zleap-AI/SAG)。

## 开发参考

- [桌面开发](apps/desktop/README.md)
- [API 架构](apps/api/README.md)
- [优化状态](docs/SAG_OPTIMIZATION_2026.md)
- [优化计划](docs/SAG_OPTIMIZATION_PLAN.md)
