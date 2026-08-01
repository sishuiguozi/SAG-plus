# Tree-sitter 全语言代码入库与符号级父子分块实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [x]`）语法来跟踪进度。

**目标：** 在 SAG-plus 中增加 Tree-sitter 全语言代码解析、符号级父子分块、本地代码文件夹递归增量入库、每知识库三级 LLM 抽取策略，以及可恢复的解析器资源管理；不改变 Markdown/PDF/Office 等现有通用入库行为。

**架构：** 先由文件路由器把明确的代码文件送入独立的 Tree-sitter 管线，再将标准化符号树转换为 zleap-sag 可持久化的预计算 `ChunkDraft`。代码块用 `content_sha256` 作为可见版本，更新时先写暂存文件和新版本块，成功后原子发布元数据，再通过独立清理作业删除旧版本。前端 Web Worker 扫描文件夹、应用安全排除规则并计算摘要，后端返回增量计划后只上传新增/变化文件。

**技术栈：** Python 3.11、FastAPI、SQLAlchemy async、zleap-sag、tree-sitter-language-pack 1.13.7、pytest；Next.js 15、React 19、TypeScript、Web Worker、Vitest、`ignore`。

---


## 完成状态（2026-08-02 复核）

**结论：计划内功能已落地，并完成后续稳定性修复。**

已实现并验证：
- Document 字段 `relative_path` / `content_sha256` / `code_language` 与幂等迁移
- 每知识库代码抽取配置 `off | comments | all`（默认 comments）
- Tree-sitter 资源管理：下载 / 暂停 / 继续 / 修复；ready 后不再整包重下
- Windows DLL 占用（WinError 5）下的安全 promote
- 文件路由、符号级父子分块、代码文件夹 plan/upload 增量同步
- 版本发布与旧 revision 检索过滤 / 清理
- 知识库详情页：代码抽取策略 + 导入代码文件夹
- 设置页：Tree-sitter 资源卡位于 **设置 → 模型 → 解析模型**
- Console Go 抽取兼容：`json_schema` 可配置降级为 `json_object`

文档入口：
- 使用指南：`docs/guides/CODE_FOLDER_INGESTION.md`
- 设计：`docs/superpowers/specs/2026-08-01-tree-sitter-code-ingestion-design.md`


## 约束与完成标准

- 只在 `E:\SAG-plus` 当前 `main` 内联执行，不创建 worktree，不启用子代理。
- 不删除或覆盖用户已有未提交改动；每个任务开始和提交前检查 `git status --short`。
- 单文件上传中明确识别的源码走 Tree-sitter；文件夹导入中的 HTML/JSON 走 Tree-sitter；普通单文件 HTML/JSON 保持 MarkItDown。
- Markdown、普通文本、PDF、Office、CSV/TSV 的现有路由和行为必须有回归测试。
- `.env`、私钥、凭据、锁文件、生成文件、压缩代码、source map、二进制文件和 `.gitignore` 命中文件不得入库。
- 本地缺失文件不自动删除；增量同步只新增和更新。
- 代码更新失败时旧版本仍然可检索；新版本发布后旧块即使尚未物理清理，也必须被检索层过滤。
- Tree-sitter 资源安装中断后可继续，校验失败可修复；测试环境禁止真实联网下载。
- 最终通过后端相关测试、全量后端测试、前端单测、typecheck、lint、i18n 检查与桌面启动冒烟。

## 任务 1：持久化字段与每知识库代码配置

**文件：**

- 修改：`apps/api/sag_api/db/models/document.py`
- 修改：`apps/api/sag_api/core/db.py`
- 修改：`apps/api/sag_api/schemas/source.py`
- 修改：`apps/api/sag_api/services/source_service.py`
- 修改：`apps/api/sag_api/api/v1/sources.py`
- 测试：`apps/api/tests/test_source_code_config.py`
- 测试：`apps/api/tests/test_db_migrations.py`

- [x] **步骤 1：写出字段和配置 API 的失败测试**

  覆盖以下断言：

  ```python
  assert document.relative_path == "repo/src/main.py"
  assert document.content_sha256 == "a" * 64
  assert document.code_language == "python"

  response = await client.get(f"/api/v1/sources/{source_id}/code-config")
  assert response.json() == {"llm_extraction_mode": "comments"}
  ```

  PATCH 只接受 `off | comments | all`，缺字段的旧知识库返回 `comments`；API 不返回 `Source.config` 中其他连接器配置或密钥。

- [x] **步骤 2：运行测试，确认因字段/API 缺失而失败**

  ```powershell
  cd E:\SAG-plus\apps\api
  .\.venv\Scripts\python.exe -m pytest tests\test_source_code_config.py tests\test_db_migrations.py -q
  ```

- [x] **步骤 3：实现幂等数据库升级**

  在 `Document` 添加：

  ```python
  relative_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
  content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
  code_language: Mapped[str | None] = mapped_column(String(64), nullable=True)
  ```

  将三个列加入 `_COLUMN_UPGRADES`，并在 `_INDEX_UPGRADES` 添加：

  ```python
  ("ix_documents_source_relative_path", "documents", "source_id, relative_path"),
  ("ix_documents_source_code_language", "documents", "source_id, code_language"),
  ```

- [x] **步骤 4：实现只暴露代码配置的 schema/service/API**

  ```python
  class SourceCodeConfig(BaseModel):
      llm_extraction_mode: Literal["off", "comments", "all"] = "comments"
  ```

  `GET/PATCH /sources/{source_id}/code-config` 只读取/合并 `Source.config["code_ingest"]`；PATCH 用复制后的 dict 回写 JSON 字段，避免原地修改未被 SQLAlchemy 检测。

- [x] **步骤 5：运行测试并提交**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_source_code_config.py tests\test_db_migrations.py -q
  git add apps/api/sag_api/db/models/document.py apps/api/sag_api/core/db.py apps/api/sag_api/schemas/source.py apps/api/sag_api/services/source_service.py apps/api/sag_api/api/v1/sources.py apps/api/tests/test_source_code_config.py apps/api/tests/test_db_migrations.py
  git commit -m "feat: add code ingestion metadata"
  ```

## 任务 2：Tree-sitter 依赖、资源管理器和系统 API

**文件：**

- 修改：`apps/api/pyproject.toml`
- 修改：`apps/api/sag_api/core/config.py`
- 新建：`apps/api/sag_api/code_ingest/__init__.py`
- 新建：`apps/api/sag_api/code_ingest/resource_manager.py`
- 新建：`apps/api/sag_api/schemas/tree_sitter.py`
- 新建：`apps/api/sag_api/api/v1/tree_sitter.py`
- 修改：`apps/api/sag_api/api/v1/__init__.py`
- 修改：`apps/api/sag_api/main.py`
- 修改：`apps/api/tests/conftest.py`
- 测试：`apps/api/tests/test_tree_sitter_resource_manager.py`
- 测试：`apps/api/tests/test_tree_sitter_api.py`

- [x] **步骤 1：添加资源状态机失败测试**

  用临时目录和 fake language-pack adapter 验证状态：`missing -> downloading -> ready`、暂停、继续、校验错误、repair；断言 staging 未完成时不替换 active，版本目录为 `{data_dir}/tree-sitter/1.13.7/{active,staging}`。

- [x] **步骤 2：添加 API 失败测试**

  覆盖：

  ```text
  GET  /api/v1/system/tree-sitter
  POST /api/v1/system/tree-sitter/download
  POST /api/v1/system/tree-sitter/pause
  POST /api/v1/system/tree-sitter/resume
  POST /api/v1/system/tree-sitter/repair
  ```

  状态响应包含 `version`、`state`、`installed_languages`、`total_languages`、`downloaded_bytes`、`total_bytes`、`disk_bytes`、`error`。

- [x] **步骤 3：运行测试确认失败，然后固定依赖与配置**

  在 `pyproject.toml` 添加：

  ```toml
  "tree-sitter-language-pack==1.13.7",
  ```

  在 Settings 添加 `tree_sitter_auto_download: bool = True`。测试 `conftest.py` 在导入 app 前设置 `SAG_TREE_SITTER_AUTO_DOWNLOAD=false`，保证测试不联网。

- [x] **步骤 4：实现资源管理器**

  - 用 `asyncio.Lock` 保证同一时刻只有一个变更操作。
  - 下载在线程池执行；每批语言后持久化 JSON checkpoint，暂停只在批次边界生效。
  - 通过 `manifest_languages()` 与 `downloaded_languages()` 比较完整性；repair 只下载缺失项。
  - 完成校验后用同一磁盘上的目录重命名切换 staging/active。
  - `main.py` 把实例放入 `app.state.tree_sitter_manager`；仅配置开启时创建后台下载任务，shutdown 时取消并等待。

- [x] **步骤 5：运行测试并提交**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_tree_sitter_resource_manager.py tests\test_tree_sitter_api.py -q
  git add apps/api/pyproject.toml apps/api/sag_api/core/config.py apps/api/sag_api/code_ingest apps/api/sag_api/schemas/tree_sitter.py apps/api/sag_api/api/v1/tree_sitter.py apps/api/sag_api/api/v1/__init__.py apps/api/sag_api/main.py apps/api/tests/conftest.py apps/api/tests/test_tree_sitter_resource_manager.py apps/api/tests/test_tree_sitter_api.py
  git commit -m "feat: manage Tree-sitter resources"
  ```

## 任务 3：文件安全策略和解析路由

**文件：**

- 新建：`apps/api/sag_api/code_ingest/file_policy.py`
- 修改：`apps/api/sag_api/parsing/service.py`
- 修改：`apps/api/sag_api/parsing/text.py`
- 测试：`apps/api/tests/test_code_file_policy.py`
- 修改测试：`apps/api/tests/test_document_parsing.py`

- [x] **步骤 1：写路由矩阵参数化测试**

  至少覆盖：

  | 文件 | 单文件上传 | 代码文件夹 |
  |---|---|---|
  | `.py/.cpp/.java` | Tree-sitter | Tree-sitter |
  | `.html/.json` | MarkItDown | Tree-sitter |
  | `.md` | Markdown 原路由 | Markdown 原路由 |
  | `.txt/.log/.rst/.adoc` | 普通文本 | 普通文本 |
  | `.pdf/.docx/.pptx/.xlsx/.csv` | 现有路由 | 默认不选，手动选后现有路由 |
  | AFSIM 自定义文本扩展 | 普通文本 | 普通文本 |
  | `.ipynb` | 不支持 | 不支持 |

  同时验证 `.env`、`id_rsa`、`*.pem`、`credentials*`、lock 文件、`*.min.js`、`*.map`、二进制/NUL 文件被拒绝。

- [x] **步骤 2：运行测试确认失败**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_file_policy.py tests\test_document_parsing.py -q
  ```

- [x] **步骤 3：实现纯函数文件策略**

  定义 `IngestContext = Literal["single", "code_folder"]`、`ParserRoute = Literal["tree_sitter", "markdown", "text", "mineru", "markitdown", "skip"]`。路径先统一 `/`，拒绝绝对路径、`..`、控制字符和 Windows 保留名；语言识别依次使用路径、扩展名、内容探测，失败后可靠文本回退，否则跳过。

- [x] **步骤 4：扩展 PreparedDocument**

  将 provider 类型增加 `tree_sitter`，并携带 `relative_path`、`content_sha256`、`code_language`、`ingest_context`。现有调用不传 context 时默认 `single`，确保通用路径不变。

- [x] **步骤 5：通过测试并提交**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_file_policy.py tests\test_document_parsing.py -q
  git add apps/api/sag_api/code_ingest/file_policy.py apps/api/sag_api/parsing/service.py apps/api/sag_api/parsing/text.py apps/api/tests/test_code_file_policy.py apps/api/tests/test_document_parsing.py
  git commit -m "feat: route source files safely"
  ```

## 任务 4：Tree-sitter 标准化符号树与符号级父子分块

**文件：**

- 新建：`apps/api/sag_api/code_ingest/types.py`
- 新建：`apps/api/sag_api/code_ingest/parser.py`
- 新建：`apps/api/sag_api/code_ingest/chunk_builder.py`
- 测试夹具：`apps/api/tests/fixtures/code_ingest/sample.py`
- 测试夹具：`apps/api/tests/fixtures/code_ingest/sample.cpp`
- 测试夹具：`apps/api/tests/fixtures/code_ingest/sample.ts`
- 测试：`apps/api/tests/test_tree_sitter_parser.py`
- 测试：`apps/api/tests/test_symbol_chunk_builder.py`

- [x] **步骤 1：写跨语言标准化失败测试**

  断言 Python/C++/TypeScript 均产出稳定 `CodeSymbol`：

  ```python
  assert symbol.identity == f"{source_id}:{relative_path}:{kind}:{qualified_name}"
  assert symbol.start_line > 0
  assert symbol.end_line >= symbol.start_line
  ```

  身份不包含行号；类方法的 `ancestor_path` 含文件与类；注释和 docstring 可关联到最近符号。

- [x] **步骤 2：写父子块失败测试**

  断言：

  - 文件/模块是父块；class/struct/interface/namespace 是父块。
  - function/method/constructor 和独立 enum/type/macro 是子块。
  - 超长函数变父块，按 AST statement 边界产生子块，不按字符硬切。
  - metadata 含 `chunk_type=code_parent|code_child`、`parent_group`、`symbol_id`、`symbol_kind`、`qualified_name`、`ancestor_path`、`relative_path`、`content_sha256`、`code_language`、行范围。
  - 只有注释模式需要的块带 `llm_extraction_text`，值只含 docstring/块注释/连续行注释。

- [x] **步骤 3：运行测试确认失败**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_tree_sitter_parser.py tests\test_symbol_chunk_builder.py -q
  ```

- [x] **步骤 4：实现标准化 parser adapter**

  用 `ProcessConfig` 开启 structure/comments/docstrings/symbols/diagnostics；将 language-pack 类型隔离在 adapter 内，业务层只使用本地 dataclass。解析诊断含 fatal error 时返回明确错误，不悄悄制造空知识。

- [x] **步骤 5：实现 `SymbolChunkBuilder`**

  输出 zleap `ChunkDraft` 需要的标题、内容和 metadata；父块内容是签名/祖先/紧凑上下文，子块内容保留精确源码。父子关系先用稳定 `parent_group` 表示，由持久化后处理回填真实 `parent_id`。

- [x] **步骤 6：通过测试并提交**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_tree_sitter_parser.py tests\test_symbol_chunk_builder.py -q
  git add apps/api/sag_api/code_ingest/types.py apps/api/sag_api/code_ingest/parser.py apps/api/sag_api/code_ingest/chunk_builder.py apps/api/tests/fixtures/code_ingest apps/api/tests/test_tree_sitter_parser.py apps/api/tests/test_symbol_chunk_builder.py
  git commit -m "feat: build symbol-aware code chunks"
  ```

## 任务 5：预计算代码 Loader 与现有增量处理器集成

**文件：**

- 新建：`apps/api/sag_api/code_ingest/loader.py`
- 修改：`apps/api/sag_api/sag/incremental_processor.py`
- 修改：`apps/api/sag_api/sag/engine_manager.py`
- 修改：`apps/api/sag_api/sag/parent_child.py`
- 测试：`apps/api/tests/test_code_document_loader.py`
- 测试：`apps/api/tests/test_code_ingest_pipeline.py`

- [x] **步骤 1：写真实 zleap 入库失败测试**

  用临时 zleap 数据目录和 sample.py 验证：Article、父块、子块均入库；`ChunkDraft.metadata` 原样进入 `SourceChunk.extra_data`；子块 `parent_id` 指向对应父块；代码父块是否向量化遵循现有 `parent_chunk_vectorize` 配置。

- [x] **步骤 2：运行测试确认失败**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_document_loader.py tests\test_code_ingest_pipeline.py -q
  ```

- [x] **步骤 3：实现预计算 parser/loader**

  `PrecomputedCodeParser` 直接返回构建好的 `ChunkingResult`。`CodeDocumentLoader` 继承 zleap `DocumentLoader`，覆写 `load` 并调用 `load_file(max_tokens=None, chunk_mode=None)`，避免父类重新构造 MarkdownParser。

- [x] **步骤 4：按 provider 接入处理器**

  `EngineManager.process_document` 把 PreparedDocument 的代码上下文传给 `IncrementalDocumentProcessor`；后者在 provider 为 `tree_sitter` 时使用 `CodeDocumentLoader`，其他 provider 仍走原有 `DocumentLoader`。

- [x] **步骤 5：扩展父子回填**

  `parent_child.py` 同时识别通用 `parent/child` 和代码 `code_parent/code_child`，但不改变通用块的检索替换行为。

- [x] **步骤 6：通过测试并提交**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_document_loader.py tests\test_code_ingest_pipeline.py tests\test_parent_child.py -q
  git add apps/api/sag_api/code_ingest/loader.py apps/api/sag_api/sag/incremental_processor.py apps/api/sag_api/sag/engine_manager.py apps/api/sag_api/sag/parent_child.py apps/api/tests/test_code_document_loader.py apps/api/tests/test_code_ingest_pipeline.py
  git commit -m "feat: ingest precomputed code chunks"
  ```

## 任务 6：三级 LLM 实体/事件抽取策略

**文件：**

- 修改：`apps/api/sag_api/sag/incremental_processor.py`
- 修改：`apps/api/sag_api/jobs/tasks.py`
- 测试：`apps/api/tests/test_code_extraction_modes.py`

- [x] **步骤 1：写三种模式失败测试**

  使用 fake EventExtractor 记录输入：

  - `off`：所有代码块直接标记 processed/eventless，LLM 调用 0 次。
  - `comments`：默认，只对 `llm_extraction_text` 非空的块调用；传给 LLM 的 ArticleSection 内容不含源码正文。
  - `all`：只对子块调用；父块跳过；传入完整子块源码。
  - 非代码文档仍保持原行为。

- [x] **步骤 2：运行测试确认失败**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_extraction_modes.py -q
  ```

- [x] **步骤 3：实现抽取输入隔离**

  每次 `_extract_chunk` 创建 EventExtractor 后，仅对该实例包装 `_load_chunk_content`：复制返回的 ArticleSection 并替换 content，不修改数据库块内容。无须 LLM 的代码块也要写入与正常完成相同的 processed checkpoint，保证暂停/恢复计数正确。

- [x] **步骤 4：从 Source 配置传递模式**

  作业开始读取 `Source.config.code_ingest.llm_extraction_mode` 并传给 processor；旧 Source 缺字段使用 `comments`。配置只影响后续处理/重处理，不追溯修改已有事件。

- [x] **步骤 5：通过测试并提交**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_extraction_modes.py tests\test_incremental_processor.py -q
  git add apps/api/sag_api/sag/incremental_processor.py apps/api/sag_api/jobs/tasks.py apps/api/tests/test_code_extraction_modes.py
  git commit -m "feat: scope LLM extraction for code"
  ```

## 任务 7：内容版本发布与可恢复的更新清理

**文件：**

- 修改：`apps/api/sag_api/enums.py`
- 修改：`apps/api/sag_api/services/document_service.py`
- 修改：`apps/api/sag_api/jobs/tasks.py`
- 修改：`apps/api/sag_api/jobs/queue.py`
- 测试：`apps/api/tests/test_code_revision_publish.py`
- 测试：`apps/api/tests/test_code_revision_cleanup.py`

- [x] **步骤 1：写失败安全测试**

  场景：已有 READY 文档 hash A。上传 hash B 到同一 `source_id + relative_path` 后：

  - 处理过程中 Document 仍指向 A；新文件位于同盘 pending 路径。
  - 解析/向量/抽取失败时删除 pending，恢复 READY/A/旧计数。
  - 成功时 `os.replace` 发布文件，单事务更新 hash B、语言、大小、sag_source_id、块/事件计数，并按差量更新 Source。
  - 发布后创建 `CLEANUP_DOCUMENT_REVISION` 作业清理旧 sag_source_id；清理失败可重试且不回滚 B。

- [x] **步骤 2：运行测试确认失败**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_revision_publish.py tests\test_code_revision_cleanup.py -q
  ```

- [x] **步骤 3：增加补偿清理作业**

  在 `JobType` 添加 `CLEANUP_DOCUMENT_REVISION`，处理器 payload 只保存 `source_id/document_id/old_sag_source_id/new_content_sha256`。处理前再次确认 Document 当前 hash 等于 new hash，避免延迟任务删错新版本；调用 `engine_manager.delete_document_data(old_sag_source_id)` 必须幂等。

- [x] **步骤 4：实现 replacement payload 和发布边界**

  `PROCESS_DOCUMENT` 的 `Job.payload["code_replacement"]` 保存 pending/new 与全部 old authoritative 字段。checkpoint 只写 Job.payload，不在 replacement 过程中改变 Document 当前版本。成功发布后再提交 Document/Source；然后排队 cleanup。

- [x] **步骤 5：通过测试并提交**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_revision_publish.py tests\test_code_revision_cleanup.py tests\test_jobs.py -q
  git add apps/api/sag_api/enums.py apps/api/sag_api/services/document_service.py apps/api/sag_api/jobs/tasks.py apps/api/sag_api/jobs/queue.py apps/api/tests/test_code_revision_publish.py apps/api/tests/test_code_revision_cleanup.py
  git commit -m "feat: publish code revisions safely"
  ```

## 任务 8：代码检索版本过滤与精确父子上下文

**文件：**

- 新建：`apps/api/sag_api/sag/code_context.py`
- 修改：`apps/api/sag_api/sag/engine_manager.py`
- 修改：`apps/api/sag_api/services/retrieval_service.py`
- 测试：`apps/api/tests/test_code_retrieval_context.py`
- 修改测试：`apps/api/tests/test_parent_child.py`

- [x] **步骤 1：写检索失败测试**

  构造同一路径 hash A/B 的命中以及代码父/子重复命中，断言：

  - Document 当前 hash B 时过滤 A。
  - 无代码 metadata 的历史通用块不受影响。
  - 命中代码子块返回“紧凑父上下文 + 精确子源码”，而不是用父块替换子块。
  - 父块和子块同时命中时去重，保留最高分并合并 citation metadata。
  - `search`、`search_many`、向量和 FTS 合并出口行为一致。

- [x] **步骤 2：运行测试确认失败**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_retrieval_context.py tests\test_parent_child.py -q
  ```

- [x] **步骤 3：实现批量当前版本查询**

  `_enrich_outcome(outcome, source)` 收集命中中的 `(relative_path, content_sha256)`，一次 SQL 查询 `Document` 建当前 hash map，先过滤过期块，再调用代码上下文增强和现有通用父子增强。禁止逐条 SQL。

- [x] **步骤 4：实现代码上下文组合**

  通过 `parent_id` 批量取父块；输出正文保留子块源码并在前面加文件路径、祖先符号和父签名。metadata 保留原 chunk id、parent id、symbol id、行范围，供引用定位。

- [x] **步骤 5：通过测试并提交**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_retrieval_context.py tests\test_parent_child.py tests\test_search_service.py -q
  git add apps/api/sag_api/sag/code_context.py apps/api/sag_api/sag/engine_manager.py apps/api/sag_api/services/retrieval_service.py apps/api/tests/test_code_retrieval_context.py apps/api/tests/test_parent_child.py
  git commit -m "feat: enrich code retrieval context"
  ```

## 任务 9：文件夹增量计划和批量上传 API

**文件：**

- 新建：`apps/api/sag_api/schemas/code_folder.py`
- 新建：`apps/api/sag_api/services/code_folder_service.py`
- 新建：`apps/api/sag_api/api/v1/code_folder.py`
- 修改：`apps/api/sag_api/api/v1/__init__.py`
- 修改：`apps/api/sag_api/services/document_service.py`
- 测试：`apps/api/tests/test_code_folder_api.py`
- 测试：`apps/api/tests/test_code_folder_sync.py`

- [x] **步骤 1：写计划 API 失败测试**

  `POST /sources/{source_id}/code-folder/plan` 接收根目录名和 `{relative_path, sha256, size_bytes}` 列表，返回每项 `new | changed | unchanged | rejected` 与拒绝原因；未上报的已有路径不返回 delete。

- [x] **步骤 2：写上传 API 失败测试**

  `POST /sources/{source_id}/code-folder/upload` 使用 multipart 接收 `relative_path`、声明 hash、文件。后端重新计算 SHA-256，不信任客户端；校验 normalized path 必须以本次根目录名开头；同路径同 hash 幂等返回已有文档，changed 进入任务 7 的 replacement 流程。

- [x] **步骤 3：运行测试确认失败**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_folder_api.py tests\test_code_folder_sync.py -q
  ```

- [x] **步骤 4：实现 plan/upload 服务**

  单次 plan 用一条查询加载 source 下已有 `relative_path -> hash`；限制清单项数、单路径长度、总声明体积和单文件大小。上传复用 DiskGuard、job queue 和安全文件策略；相对路径包含用户选中根目录名，例如 `my-repo/src/service.py`。

- [x] **步骤 5：通过测试并提交**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests\test_code_folder_api.py tests\test_code_folder_sync.py -q
  git add apps/api/sag_api/schemas/code_folder.py apps/api/sag_api/services/code_folder_service.py apps/api/sag_api/api/v1/code_folder.py apps/api/sag_api/api/v1/__init__.py apps/api/sag_api/services/document_service.py apps/api/tests/test_code_folder_api.py apps/api/tests/test_code_folder_sync.py
  git commit -m "feat: sync local code folders"
  ```

## 任务 10：前端扫描 Worker 与代码文件夹导入界面

**文件：**

- 修改：`apps/web/package.json`
- 修改：`apps/web/package-lock.json`
- 新建：`apps/web/lib/code-folder-import.ts`
- 新建：`apps/web/lib/code-folder-import.test.ts`
- 新建：`apps/web/workers/code-folder-scan.worker.ts`
- 新建：`apps/web/components/features/code-folder-import.tsx`
- 修改：`apps/web/components/features/upload-zone.tsx`
- 修改：`apps/web/components/features/knowledge-source-workspace.tsx`
- 修改：`apps/web/lib/api.ts`
- 修改：`apps/web/lib/types.ts`

- [x] **步骤 1：安装 `.gitignore` 解析依赖**

  ```powershell
  cd E:\SAG-plus\apps\web
  npm install ignore@^7
  ```

- [x] **步骤 2：写纯函数失败测试**

  覆盖路径规范化、保留根目录名、默认排除集、`.gitignore`、敏感文件、二进制、默认取消 PDF/Office、plan 分类统计。测试不得依赖浏览器目录选择器。

- [x] **步骤 3：运行测试确认失败**

  ```powershell
  npm run test:unit -- lib/code-folder-import.test.ts
  ```

- [x] **步骤 4：实现 Web Worker**

  Worker 接收文件描述和可读取的 File，分批计算 `crypto.subtle.digest("SHA-256", ...)`、检测 NUL/文本，解析根 `.gitignore`，定期回传 progress。主线程只保留引用和扫描结果，不一次性读取全部文件。

- [x] **步骤 5：实现导入对话框**

  使用 `webkitdirectory` 选择整个文件夹；展示总数、可入库/跳过/默认未选、new/changed/unchanged、总大小；允许检查拒绝原因和手动勾选 PDF/Office；调用 plan 后只上传 new/changed，限制并发并支持取消/失败重试。原 UploadZone 的单文件入口保持不变。

- [x] **步骤 6：通过测试、类型检查并提交**

  ```powershell
  npm run test:unit -- lib/code-folder-import.test.ts
  npm run typecheck
  npm run lint
  git add apps/web/package.json apps/web/package-lock.json apps/web/lib/code-folder-import.ts apps/web/lib/code-folder-import.test.ts apps/web/workers/code-folder-scan.worker.ts apps/web/components/features/code-folder-import.tsx apps/web/components/features/upload-zone.tsx apps/web/components/features/knowledge-source-workspace.tsx apps/web/lib/api.ts apps/web/lib/types.ts
  git commit -m "feat: import local code folders"
  ```

## 任务 11：每知识库抽取配置与全局 Tree-sitter 资源卡

**文件：**

- 新建：`apps/web/components/features/source-code-config-card.tsx`
- 新建：`apps/web/components/features/tree-sitter-resource-card.tsx`
- 修改：`apps/web/components/features/knowledge-source-workspace.tsx`
- 修改：`apps/web/app/(app)/settings/page.tsx`
- 修改：`apps/web/lib/api.ts`
- 修改：`apps/web/lib/types.ts`
- 修改：`apps/web/messages/zh-CN.json`
- 修改：`apps/web/messages/en-US.json`
- 测试：`apps/web/components/features/source-code-config-card.test.tsx`
- 测试：`apps/web/components/features/tree-sitter-resource-card.test.tsx`

- [x] **步骤 1：写组件失败测试**

  配置卡展示三档：关闭、仅注释（推荐）、全部子块；切换后保存到当前知识库，解释只影响未来入库/重处理。资源卡展示版本、306 语言安装数、进度和磁盘，状态对应下载/暂停/继续/修复按钮；重复点击时按钮禁用。

- [x] **步骤 2：运行测试确认失败**

  ```powershell
  cd E:\SAG-plus\apps\web
  npm run test:unit -- components/features/source-code-config-card.test.tsx components/features/tree-sitter-resource-card.test.tsx
  ```

- [x] **步骤 3：实现配置卡和资源卡**

  `SourceCodeConfigCard` 只调用 code-config API；`TreeSitterResourceCard` 仅在系统设置页渲染，下载中按短间隔轮询，稳定状态停止轮询。错误直接展示后端可操作信息，不把“未安装”误报成 ready。

- [x] **步骤 4：补齐中英文案并验证**

  ```powershell
  npm run test:unit -- components/features/source-code-config-card.test.tsx components/features/tree-sitter-resource-card.test.tsx
  npm run typecheck
  npm run lint
  npm run i18n:check
  git add apps/web/components/features/source-code-config-card.tsx apps/web/components/features/tree-sitter-resource-card.tsx apps/web/components/features/knowledge-source-workspace.tsx "apps/web/app/(app)/settings/page.tsx" apps/web/lib/api.ts apps/web/lib/types.ts apps/web/messages/zh-CN.json apps/web/messages/en-US.json apps/web/components/features/source-code-config-card.test.tsx apps/web/components/features/tree-sitter-resource-card.test.tsx
  git commit -m "feat: configure code ingestion UI"
  ```

## 任务 12：文档、回归与桌面端到端验证

**文件：**

- 修改：`README.md`
- 修改：`README-CN.md`
- 修改：`docs/SAG_OPTIMIZATION_2026.md`
- 修改：`docs/ARCHITECTURE_PATCHES.md`
- 新建：`docs/guides/CODE_FOLDER_INGESTION.md`
- 修改：与启动、依赖、知识库入库有关的现有 `docs/**/*.md`
- 测试：`apps/api/tests/test_code_ingest_e2e.py`

- [x] **步骤 1：添加真实 E2E 测试**

  在临时数据目录使用已安装 parser 或受控 fake parser 完成：文件夹 plan -> 上传 -> Tree-sitter 解析 -> 父子入库 -> comments 抽取 -> 检索父上下文+精确子块 -> 修改文件 -> 发布新 revision -> 旧 revision 过滤 -> cleanup。测试默认禁用网络。

- [x] **步骤 2：运行聚焦后端回归**

  ```powershell
  cd E:\SAG-plus\apps\api
  .\.venv\Scripts\python.exe -m pytest tests\test_code_ingest_e2e.py tests\test_code_* tests\test_tree_sitter_* tests\test_symbol_chunk_builder.py tests\test_document_parsing.py tests\test_parent_child.py -q
  ```

- [x] **步骤 3：运行全量后端检查**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -q
  .\.venv\Scripts\python.exe -m ruff check sag_api tests
  ```

  若用户 `.env` 覆盖测试默认值，使用测试专用环境变量重跑并记录差异；不得把真实回归归因于环境。

- [x] **步骤 4：运行全量前端检查**

  ```powershell
  cd E:\SAG-plus\apps\web
  npm run test:unit
  npm run typecheck
  npm run lint
  npm run i18n:check
  npm run build
  ```

- [x] **步骤 5：更新全部相关说明文档**

  文档必须突出：唯一支持的开发启动方式是 `cd /e/SAG-plus/apps/desktop && npm run dev`；启动会检测依赖；Tree-sitter 约 500 MB 预留空间；资源下载/暂停/修复路径；文件路由表；增量同步不自动删除；三档抽取含义；旧知识库默认 comments；代码父子检索语义；隐私排除；故障恢复。删除或改写与当前桌面启动、旧项目名、过时截图/视频相冲突的内容。

- [x] **步骤 6：桌面启动冒烟**

  ```powershell
  cd E:\SAG-plus\apps\desktop
  npm run dev
  ```

  验证 API 启动、Web 无缺包、设置页资源卡可用、知识库可选择文件夹并看到计划、已有知识库仍可查询。完成后正常 Ctrl+C 结束，不保留后台进程。

- [x] **步骤 7：检查改动、提交并推送 main**

  ```powershell
  cd E:\SAG-plus
  git diff --check
  rg -n "FIXME|XXX|待补充|后续再写|同上实现" README.md README-CN.md docs apps/api/sag_api apps/web --glob "!**/node_modules/**"
  git status --short
  git add README.md README-CN.md docs apps/api apps/web
  git commit -m "docs: document Tree-sitter code ingestion"
  git push origin main
  ```

## 规格覆盖自检

- [x] 306 语言资源、精确版本、后台全量下载、进度/暂停/继续/修复、约 500 MB 说明均有实现与测试。
- [x] 单文件与代码文件夹的路由差异有参数化测试；Markdown/PDF/Office/普通文本无回归。
- [x] 递归选择、根目录名、相对路径、`.gitignore`、敏感/生成/二进制排除均有前后端双重校验。
- [x] 增量 new/changed/unchanged/rejected 完整，本地缺失不删除。
- [x] Document 三字段与两个索引通过幂等迁移验证。
- [x] 稳定符号 ID 不含行号；两级父子、长函数 statement 子分块、祖先路径完整。
- [x] off/comments/all 是每知识库配置，旧库默认 comments；comments 只把注释文本送入 LLM。
- [x] 更新过程旧版本持续可用；失败回滚；发布后按 hash 过滤旧块；清理可重试。
- [x] 代码检索返回父紧凑上下文 + 精确子源码，通用父子增强行为不变。
- [x] `.ipynb` 明确不在本期；未引入完整代码图数据库。
- [x] 所有新增 API、类型、中英文案、README 和架构补丁文档一致，无未完成标记和失效启动方式。
