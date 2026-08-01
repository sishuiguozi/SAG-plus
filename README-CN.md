# SAG-plus

SAG-plus 是基于 [Zleap-AI/SAG](https://github.com/Zleap-AI/SAG) 的本地个人优化分支。
当前仓库定位为桌面开发工作区，唯一支持的启动方式是 Electron 开发模式。

## 启动桌面应用

前提条件：

- Node.js 20 或更高版本。
- Python 3.11 或更高版本，可通过 `python` 命令访问（或用 `SAG_PYTHON` 指定路径）。

在 Git Bash 中运行：

```bash
cd /e/SAG-plus/apps/desktop
npm run dev
```

桌面脚本会启动或复用本地 API（`127.0.0.1:8000`）、Web（`127.0.0.1:3000`
或 `3001`）和 Electron 窗口。首次运行会自动检查依赖、在需要时执行 `npm ci`、
创建 `apps/api/.venv` 并安装 API 包。按 `Ctrl+C` 停止。

如果项目从 `E:\sag-dev` 迁移而来，请先继续使用已有的本地数据和开发依赖，
再有计划地迁移。`.data`、上传文件、数据库与模型缓存均为本地数据，不能提交到 Git。

## SAG-plus 的重点优化

| 方向 | 本分支的实现 |
| --- | --- |
| 检索 | 语义检索叠加 LanceDB FTS/BM25、结果缓存与字面检索回退；重排可选本地 Q8 Cross-Encoder、兼容 API 或 LLM，失败安全回退。 |
| 上下文 | 父子分块以子块完成精确召回、以父块提供完整上下文，并自动消除重复命中。 |
| 入库 | 持久化批量向量写入协调器为 LanceDB 提供单写者、重试、恢复和幂等工作项。 |
| 代码 | Tree-sitter 符号级父子分块、本地代码文件夹增量同步、三档代码抽取与版本安全发布。 |
| 存储 | SQLite 连接与 PRAGMA 调优、磁盘保护、LanceDB 清理、索引维护和读写可观测性降低本地存储压力。 |
| 模型调用 | 四档工具调用策略可分别控制普通回答、指定工具轮和工具后回答的思考状态；三档推理历史兼容可自动识别 DeepSeek、强制覆盖模型别名或完全关闭。 |
| 评估 | 仓库包含检索评估样例与运行耗时输出，可基于本地数据衡量优化效果。 |

实现细节见 [2026 优化状态](docs/SAG_OPTIMIZATION_2026.md) 与
[架构补丁](docs/ARCHITECTURE_PATCHES.md)。

## 代码文件夹入库

- 在知识库中选择“导入代码文件夹”，系统会扫描本地目录并只上传新增/变更文件。
- 默认不会因本地文件消失而删除知识库内容。
- 代码抽取策略可按知识库设置：关闭 / 仅注释（推荐）/ 全部子块。
- Tree-sitter 语言包在 **设置 → 模型 → 解析模型** 管理，请预留约 500MB；就绪后不会整包重下。
- 详细说明见 [代码文件夹入库指南](docs/guides/CODE_FOLDER_INGESTION.md)。

## 知识库数据位置

- 桌面端默认把知识库数据保存在应用数据目录下的 `data` 文件夹，元数据库、
  引擎索引、上传文件与本地模型都位于其中。
- 在 **设置 → 系统 → 知识库数据位置** 可选择或填写新的根目录，保存后
  重启应用生效。
- 切换位置不会自动搬迁旧数据；若要沿用现有知识库，请先把旧根目录内容复制
  到新位置再重启。
- 开发模式（`npm run dev`）同样生效：界面保存的位置会在下次启动时注入
  API；也可直接在 `apps/api/.env` 设置 `SAG_DATA_ROOT`。
- 设置后自动派生 `SAG_DATABASE_URL`（`{root}/sag.db`）、`SAG_DATA_DIR`
  （`{root}/engine`）与 `SAG_UPLOAD_DIR`（`{root}/uploads`），模型目录
  为 `{root}/models`。

## 日常使用

1. 使用 `npm run dev` 启动桌面应用。
2. 在 **设置 → 知识库** 中新增或配置知识源。
3. 等待入库完成，再在搜索或对话中获得可溯源的回答。
4. 对新入库文档选择「父子分块」，即可获得子块精确召回与父块完整上下文；
   旧文档需要重新处理才会拥有父子关系。
5. 要完全在本机生成向量，打开 **设置 → 模型配置 → 本地嵌入**，先点击
   「下载推理后端」，再选择 BGE-M3 Q8、Qwen3-Embedding-0.6B Q8 或
   Qwen3-Embedding-4B Q8 并下载。模型权重绝不自动下载；
   下载完成后选择所需模型并保存配置。点击「测试本地模型」会使用下拉菜单当前选中的模型，
   基于当前上下文或线程草稿进行测试，无需先保存配置。测试会生成一次临时向量并显示模型、
   维度和耗时；不会更改已保存的配置、知识库或远程服务。
6. 要启用重排，打开 **设置 → 模型配置 → 检索重排**。可选择本地 BGE/Qwen Q8
   Cross-Encoder、完整 URL 的兼容 Rerank API（含 Qwen/vLLM），或旧 LLM 编号重排。
   本地重排模型与向量模型分开下载；原生 rank 运行时仅在点击安装后构建，测试同样无需先保存。
7. 在 **设置 → 模型配置 → 生成参数 → 工具调用策略** 选择思考行为。默认“工具轮关闭思考”
   会保留普通回答的思考，只在指定工具轮关闭，并在工具后的回答恢复；也可选择全程保留、
   自动工具选择或全程关闭。“推理历史兼容”默认自动识别 DeepSeek；接口使用模型别名时选择
   “始终启用”，接口不接受 `reasoning_content` 时选择“关闭”。

## 自托管 API

浏览器不能直接导入 Python 包。自定义前端应调用一个持有 `DataEngine` 的 Python HTTP 服务。
本仓库的 FastAPI 后端就是参考实现，并且已经与 Next.js 前端分离。

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

### API 地图

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

如果自定义前端与 API 不同源，请将前端地址加入 `SAG_CORS_ORIGINS`。API 地址改变时，
还要用对应的 `NEXT_PUBLIC_API_BASE` 重新构建 Web 镜像。

### PostgreSQL/pgvector 部署

可选的生产覆盖会将应用元数据与知识引擎迁移到 PostgreSQL/pgvector：

```bash
cp .env.example .env
openssl rand -hex 32   # 填入 SAG_SECRET_KEY
openssl rand -hex 24   # 填入 POSTGRES_PASSWORD

docker compose -f compose.yaml -f compose.postgres.yaml config
docker compose -f compose.yaml -f compose.postgres.yaml up -d --build
```

服务器部署前应设置真实的 `SAG_CORS_ORIGINS` 与 `NEXT_PUBLIC_API_BASE`。
升级前同时备份 `pgdata` 和 `sagdata`。

## MCP 与 Skill 接口

SAG-plus 把整个知识库（或单个信源）暴露为标准只读 MCP server，共 8 个工具，
任何支持 MCP 的宿主（Claude Desktop / Cursor / Dify 等）都可以接入。

### 接入方式

- 在 Web 界面 **设置 → 集成** 复制现成配置（HTTP / stdio 两种，已带鉴权头）。
- 或通过描述符接口获取：
  - 全库：`GET /api/v1/system/mcp`
  - 单信源：`GET /api/v1/sources/{source_id}/mcp`
- **HTTP（推荐）**：`http://<host>/mcp/`（全库）或
  `http://<host>/mcp/?source_id=<SOURCE_ID>`（单信源），请求头
  `Authorization: Bearer <SAG_TOKEN>`。
- **stdio**：`python -m sag_api.mcp.server`（默认全库；
  `SAG_MCP_SOURCE_ID=<SOURCE_ID>` 限定单信源；需在 `apps/api` 的 Python 环境运行）。

### MCP 工具（全部只读）

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

推荐调用顺序（探索漏斗）：`list_sources` → `list_documents` →
`outline` → `search`/`grep` → `get_chunk`/`read`。

### Agent 绑定接口（挂载信源 / 外部 MCP）

Agent 可以把知识库信源或外部 MCP server 挂成工具来源（`sag_api/mcp/` 客户端 +
Web 端「设置 → Agent」）。REST 接口与界面一一对应：

| 端点 | 用途 |
| --- | --- |
| `GET /api/v1/agents` | 列出 Agent |
| `POST /api/v1/agents` | 新建 Agent |
| `GET /api/v1/agents/default` | 获取默认 Agent |
| `GET /api/v1/agents/{agent_id}` | 获取单个 Agent |
| `PATCH /api/v1/agents/{agent_id}` | 更新 Agent |
| `DELETE /api/v1/agents/{agent_id}` | 删除 Agent |
| `GET /api/v1/agents/{agent_id}/bindings` | 列出绑定 |
| `POST /api/v1/agents/{agent_id}/bindings` | 新增绑定 |
| `DELETE /api/v1/agents/{agent_id}/bindings/{binding_id}` | 解除绑定 |

新增绑定的请求体（`POST /api/v1/agents/{agent_id}/bindings`）：

```json
{
  "target_type": "source",            // 或 "mcp_server"
  "target_id": "<source_id>",         // source 填信源 id；mcp_server 可填显示名
  "config": {}
}
```

- `target_type: "source"`：绑定知识库信源（config 留空）。
- `target_type: "mcp_server"`：把外部 MCP server 挂成工具来源；`config` 必须提供
  `"url"`（Streamable HTTP）或 `"command"`（stdio），可选 `"args"`、`"env"`。

### OpenAI 兼容对话接口

任意 Agent 都可以当作「带引用的模型」调用：

```
POST /api/v1/openai/{agent_id}/chat/completions
Authorization: Bearer <SAG_TOKEN>
```

请求体沿用 OpenAI Chat Completions 结构（`messages`、`model?`、`stream?`、
`temperature?`、`max_tokens?`），检索、系统提示与防幻觉短路和站内对话完全一致。
`stream: true` 时返回 SSE 的 `chat.completion.chunk`；否则返回标准
`chat.completion` 对象，并附 SAG 扩展字段 `sag.citations` 用于引用溯源。
该端点无状态（不落库）。

### 自带技能

仓库自带 `skills/sag` 技能（名称 `sag-knowledge`）：`SKILL.md` 教会 Agent
通过上述 MCP 工具搜索、浏览、引用和阅读知识库文档，包含漏斗流程、工具参数
参考（`references/mcp-tools.md`）与查询策略（`references/search-strategies.md`）。
把 `skills/sag` 加入 Agent 的技能目录即可启用。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| API 没有启动 | 确认 Python 3.11+ 可用，且 8000 端口未被无关进程占用；启动器会在需要时重建虚拟环境。 |
| Web 没有启动 | 启动器会安装缺失的 Web 依赖；桌面启动器会使用 3000 或 3001 端口。 |
| Electron 立即退出 | 确认命令在 `E:\SAG-plus\apps\desktop` 执行，并查看终端中最先失败的 API 或 Web 进程。 |
| 旧知识库没有显示 | Git 不会迁移本地数据；创建新索引前先确认实际使用的数据目录。 |
| 本地嵌入不可用 | 在设置中安装 llama-cpp-python 后端、下载模型，选择对应文件并保存。 |
| 本地重排未就绪 | 在「检索重排」中下载所选模型并安装原生重排运行时；若运行时或模型不可用，检索会保留融合排序。 |

## 开发参考

- [桌面开发](apps/desktop/README.md)
- [API 架构](apps/api/README.md)
- [优化状态](docs/SAG_OPTIMIZATION_2026.md)
- [优化计划](docs/SAG_OPTIMIZATION_PLAN.md)

本项目使用 [MIT](LICENSE) 协议；上游归属保留给
[Zleap-AI/SAG](https://github.com/Zleap-AI/SAG)。
