# Tree-sitter 全语言代码入库与符号级父子分块设计

## 状态

- 日期：2026-08-01
- 状态：设计已批准，等待书面规格复核
- 目标仓库：SAG-plus

## 背景

SAG-plus 已具备通用文档分块、A4 父子分块、向量与 BM25 混合召回，以及命中子块后的父级上下文增强。
这些能力适合 Markdown、PDF 和 Office 文档，但当前代码文件仍被当成普通文本按段落、行数或 token
切分，不能稳定保留类、函数、方法、宏和起止行号等结构。

本功能为代码仓库增加独立的 Tree-sitter 入库管线。用户可以从桌面端选择整个本地代码文件夹，递归
导入并保留相对路径；代码按符号边界生成父子块，普通文档继续走原解析器。不同知识库可以分别选择
代码是否参与 LLM 实体/事件抽取，以及只抽取注释还是抽取全部代码。

## 目标

1. 支持从桌面端选择整个本地代码文件夹，递归扫描并保留相对路径。
2. 使用 `tree-sitter-language-pack` 的官方语言清单识别和解析 306 种语言，不维护一份容易过期的
   SAG 私有扩展名全集。
3. 将文件、模块、命名空间、类、接口、结构体、函数、方法等转换为可检索的符号级父子块。
4. 复用现有 `SourceChunk.extra_data.parent_id` 和父子检索基础设施，不新增符号关系表。
5. 对重复导入执行安全增量同步：未变化跳过、变化更新、新文件新增、本地缺失不自动删除。
6. 为每个知识库提供 `off`、`comments`、`all` 三档代码 LLM 抽取范围，默认 `comments`。
7. 在首次安装后后台下载全部语法解析器，并支持离线使用、进度、暂停、继续及校验修复。
8. 不改变现有 PDF、Office、Markdown、普通文本及单文件 HTML/JSON 的默认解析行为。

## 非目标

- 不构建完整调用图、引用图、继承图或跨文件类型推断。
- 不提供 Git 克隆、分支管理、远程仓库同步或提交历史索引。
- 不实时监听本地目录变化。
- 不自动删除本地已经不存在的知识库文件。
- 不提供 IDE 编辑、跳转定义、查找引用或重构功能。
- 本期不解析 Jupyter Notebook；`.ipynb` 明确报告为暂不支持，不能作为普通 JSON 入库。
- Markdown 代码围栏本期不做二次 Tree-sitter 解析，避免同一内容重复入库。

## 方案选择

采用独立 Tree-sitter 代码管线，而不是先把 AST 转成 Markdown，也不引入新的代码图数据库。

独立管线可以直接保留结构化元数据和源码跨度，并在解析失败时安全回退。AST 转 Markdown 的方案虽然
改动较少，但父子关系、行号和符号类型容易被通用分块再次破坏；完整代码图数据库则需要新表、迁移和
跨文件分析，超出本期范围。

## 总体架构

```text
桌面目录选择
  -> 文件筛选与安全检查
  -> 增量清单规划
  -> 只上传新增或变化文件
  -> 文件路由
       -> 文档解析器
       -> Tree-sitter 代码解析器
       -> 普通文本降级
  -> 符号级父子块构建
  -> 现有关系库与 LanceDB 入库
  -> 向量、BM25 与父级上下文增强检索
```

新增组件采用小而清晰的边界：

- `DocumentRouteResolver`：只决定文件由哪个解析器处理以及为什么跳过。
- `CodeLanguageDetector`：根据官方 manifest、完整文件名、扩展名、Shebang 和解析质量确定语言。
- `TreeSitterResourceManager`：管理版本化解析器缓存、下载、暂停、继续、校验和原子切换。
- `TreeSitterCodeParser`：调用官方统一 `process()` 接口并返回规范化的文件结构。
- `SymbolChunkBuilder`：把规范化结构转换成父块、子块及长函数片段。
- `CodeFolderPlanner`：比较清单与数据库，给出新增、变化、未变化、忽略和拒绝结果。
- `CodeIngestCoordinator`：控制单文件更新锁、入库顺序、失败补偿和文档状态。
- `CodeExtractionPolicy`：按知识库配置选择不抽取、只抽取注释或抽取全部子块。
- `CodeContextEnricher`：把紧凑父级上下文与命中的代码子块组合起来。

普通文档管线不依赖上述代码组件。Tree-sitter 不可用时，路由器可将确认为文本的代码文件交给现有
纯文本解析器；单个代码文件失败不能中断同批其他文件。

## 依赖与全语言包资源

后端固定使用经过验证的 `tree-sitter-language-pack==1.13.7`，避免 manifest、解析器 ABI 和查询规则
在未测试的升级中漂移。该版本的 Windows x64 Python wheel 约 2 MB；全部 306 个解析器发布包下载量
为 17.23 MiB，解压后的解析器文件合计约 360.7 MiB。安装与缓存应预留约 500 MB。

`npm run dev` 的依赖引导负责安装 Python 基础包，但不阻塞桌面窗口等待全部语法下载。API 启动后，
`TreeSitterResourceManager` 在后台准备全部解析器。缓存目录必须由中心数据目录通过
`Path(settings.data_dir) / "tree-sitter"` 构造，不能用字符串拼接路径。

资源目录采用版本分层：

```text
<data_dir>/tree-sitter/
  active.json
  v1.13.7/
  .staging-v1.13.7/
```

资源管理器读取官方 manifest，并按语言分批调用官方下载能力。这样可以在语言之间暂停、继续和报告
`已安装数量 / 306`。下载写入 staging 目录；全部完成并通过校验后才更新 `active.json`。首次安装期间，
已经校验完成的当前语言可以提前用于入库；版本更新期间继续使用旧 active 目录，直到新版本完整可用。
暂停发生在单个语言下载完成后，不承诺中断一个正在传输的解析器文件。

解析器损坏、缺失或 ABI 不兼容时，状态接口返回可操作的错误。修复操作只重下缺失或校验失败的语言；
修复失败不删除仍可用的 active 版本。

## 数据模型与迁移

`Document` 增加三个可空字段：

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `relative_path` | `String(1024)`, nullable | 代码目录内的规范化相对路径 |
| `content_sha256` | `String(64)`, nullable | 增量比较与文档修订标识 |
| `code_language` | `String(64)`, nullable | Tree-sitter 规范语言名 |

增加 `(source_id, relative_path)` 与 `(source_id, code_language)` 查询索引。索引不设数据库唯一约束，
以免改变历史单文件上传允许重名的行为；代码文件夹服务在知识库范围内通过更新锁保证同一路径只有一个
当前版本。

迁移对现有记录保持三个字段为空。现有文档继续按原逻辑工作，不要求重新入库。

每个知识库的代码设置存入现有 `Source.config`：

```json
{
  "code_ingest": {
    "llm_extraction_mode": "comments"
  }
}
```

API 使用枚举验证 `off | comments | all`。缺失字段按 `comments` 读取，不能修改或丢弃同一 JSON 中的
连接器配置和其他知识库级覆盖。

符号信息继续保存在 `SourceChunk.extra_data`：

```json
{
  "chunk_source_type": "code",
  "chunk_type": "child",
  "language": "python",
  "relative_path": "my-repo/src/service.py",
  "content_sha256": "...",
  "symbol_kind": "method",
  "symbol_name": "search",
  "qualified_name": "SearchService.search",
  "symbol_id": "...",
  "start_line": 42,
  "end_line": 88,
  "ancestor_symbols": ["SearchService"],
  "parse_quality": "exact",
  "parent_id": "..."
}
```

`symbol_id` 由知识库 ID、规范化相对路径、符号类型和完整限定名生成，不包含行号。插入空行不会改变
符号身份；相对路径或限定名变化视为新符号。

## 文件路由

路由顺序固定为：安全排除、文档优先类型、代码/结构化数据类型、纯文本降级、二进制拒绝。代码语言
扩展名和特殊文件名来自官方 manifest；SAG 只维护少量文档优先、敏感文件和生成物覆盖规则。

| 文件类别 | 示例 | 解析器 |
| --- | --- | --- |
| Markdown | `.md`, `.markdown`, `.mdx` | 现有 Markdown 解析与通用分块 |
| 普通文本/轻量标记 | `.txt`, `.text`, `.log`, `.rst`, `.adoc` | 现有编码识别与段落/token 分块 |
| PDF | `.pdf` | 当前设置选择 MinerU 或 MarkItDown |
| Office/电子书 | `.docx`, `.pptx`, `.xls`, `.xlsx`, `.epub` | MarkItDown |
| 表格 | `.csv`, `.tsv` | MarkItDown 表格转 Markdown |
| 编程语言源码 | 官方 manifest 识别的源码扩展名 | Tree-sitter 符号解析 |
| Web 源码 | `.html`, `.css`, `.scss`, `.vue`, `.svelte`, `.astro` | 代码目录中使用 Tree-sitter |
| 脚本 | `.sh`, `.bash`, `.zsh`, `.fish`, `.ps1`, `.bat`, `.cmd` | Tree-sitter |
| 配置/数据 | `.json`, `.yaml`, `.yml`, `.toml`, `.xml`, `.ini`, `.properties`, `.hcl` | 代码目录中使用 Tree-sitter 结构化解析 |
| 查询/接口 | `.sql`, `.graphql`, `.gql`, `.proto` | Tree-sitter |
| 构建/基础设施 | `Dockerfile`, `Makefile`, `CMakeLists.txt`, `.cmake`, `.tf`, `.nix` | Tree-sitter |
| 模板 | `.jinja`, `.j2`, `.twig`, `.erb`, `.eex`, `.blade` | Tree-sitter |
| AFSIM/仿真文本 | `.fxw`, `.ag`, `.soar`, `.gnu`, `.imesh`, `.vsa`, `.earth` | 无语法时使用现有纯文本解析 |
| 未知但可可靠解码的文本 | 其他文本文件 | 普通文本降级 |
| 媒体、压缩包、可执行文件 | 图片、音视频、归档、二进制 | 默认跳过 |

单文件上传保持当前兼容行为：HTML 和 JSON 继续经 MarkItDown；单独上传由官方 manifest 明确识别的
Python、C++、Java 等源码时使用 Tree-sitter。只有“选择代码文件夹”入口将 HTML、JSON 等重叠类型
解释为仓库源码或结构化配置。普通上传继续使用现有显式白名单；代码上传和目录入口通过路由器动态验证
官方语言清单，不把 306 种语言扩展名复制进全局白名单。

代码目录扫描默认排除：

- `.git`、`node_modules`、`.venv`、`venv`、`dist`、`build`、`target`、`.next`、缓存目录和 vendor。
- `package-lock.json`、`pnpm-lock.yaml`、`yarn.lock`、`Cargo.lock`、Source Map、压缩源码和明显生成文件。
- `.env`、私钥、证书、凭据文件和已知秘密文件；示例模板如 `.env.example` 可以作为普通文本。
- 图片、音视频、压缩包、数据库、模型、可执行文件和包含 NUL/异常控制字符的内容。

扫描器读取 `.gitignore` 用于筛选，但不将 `.gitignore` 自身入库。PDF、Office 和电子书在目录导入确认
窗口中默认不勾选，避免无意触发批量 MinerU 请求；用户明确勾选后才按现有文档管线入库。

对 `.m` 等扩展名冲突的文件，语言探测器结合文件名、Shebang 和候选解析错误率选择结果；无法可靠
判定时降级到文本，并把原因显示在扫描结果中。

## 目录扫描与增量同步

桌面 UI 使用 Electron 内嵌 Chromium 的目录文件选择能力取得用户明确授权的 `File` 对象和
`webkitRelativePath`。API 不接收本地绝对目录路径，桌面 preload 也不暴露通用文件读取接口。

扫描阶段：

1. 将路径规范化为 `/`，保留目录选择器提供的路径，并以所选根目录名作为第一段，例如
   `my-repo/src/service.py`。这样同一知识库可以导入多个根目录而不会因内部路径相同发生冲突；根目录
   改名后视为新目录，旧内容仍按安全规则保留。
2. 拒绝绝对路径、空路径、`.`/`..` 段、非法控制字符及规范化后冲突的路径。
3. 应用安全排除、默认忽略和 `.gitignore`。
4. 在 Web Worker 中以受限并发计算 SHA-256，不一次把整个仓库读入内存。
5. 将清单分页发送给 plan API；单页最多 1000 项。
6. 后端按 `(source_id, relative_path)` 比较哈希并返回 `new`、`changed`、`unchanged`、`ignored` 或
   `rejected`，同时给出可显示的原因。

上传阶段只提交 `new` 和 `changed`，默认并发 3。每个请求携带相对路径、声明哈希和文件内容；后端
重新规范路径、检查大小、重新计算哈希并再次判断动作，不能信任前端清单。

同一路径更新由每知识库/相对路径锁串行化。新文件先完成解码、Tree-sitter 解析、符号分块和向量
准备，再发布新 revision。代码块携带 `content_sha256`，检索在旧新 revision 短暂重叠时只保留当前
revision。新 revision 发布后使用 `os.replace` 切换原始文件，并异步清理旧块；任一步失败时清理未
发布的新 revision 并保留旧文档。关系库与 LanceDB 之间没有分布式事务，因此这里使用可重试的补偿
流程，而不是宣称跨存储 ACID 事务。

重复导入规则固定为：

- 相对路径和哈希相同：跳过。
- 相对路径相同、哈希不同：安全更新并重新入库。
- 新相对路径：新增。
- 原知识库存在但本地清单缺失：显示“知识库中仍保留”，不删除。

单个文件失败不终止整批任务。前端保留失败项及原因，可以只重试失败项。已经进入现有后端队列的
文档继续通过文档状态显示；关闭导入窗口或重启桌面不取消已提交任务，不新增持久化批次表。

## Tree-sitter 解析与符号分块

`TreeSitterCodeParser` 使用官方 `process()` 接口请求结构、导入、导出、符号、注释、Docstring、
诊断和源码跨度。官方语法块只作为边界提示；SAG 仍使用当前 token 计数与 `source_chunk_max_tokens`，
不能把官方按字节的 `chunk_max_size` 直接当成 token 上限。

规范层把不同语言的结果映射为：

```text
CodeFile
  imports
  globals
  symbols[]
    kind
    name
    qualified_name
    span
    ancestors[]
    children[]
    comments[]
```

父子块规则：

1. 文件或模块级父块包含相对路径、导入、全局声明和顶层符号目录。
2. 命名空间、类、结构体和接口父块包含声明、字段、继承信息和成员目录，不复制全部方法正文。
3. 函数、方法、构造函数以及独立枚举、类型和宏作为子块，父块是最近的类/命名空间/模块/文件。
4. 没有明确符号的顶层执行代码按相邻 AST 语句组成模块级子块。
5. 超长函数升级为父块，并沿 AST 语句边界生成片段子块；类等更高祖先保存在元数据和函数父块的
   紧凑上下文中。
6. 嵌套符号只写一个最近 `parent_id`，完整层级写入 `ancestor_symbols`。
7. 无安全语句边界时先按语法块降级，最后才按行切分；所有片段必须覆盖原目标源码且不得越界。

块标题统一包含相对路径和限定名，例如 `my-repo/src/search.py :: SearchService.search`，以增强 FTS 对路径、
类名、函数名和编号的精确召回。代码正文规范为 LF 用于分块和哈希展示，原始上传字节仍单独保留。

代码父块是否向量化复用现有 `parent_chunk_vectorize` 配置。无论父块是否向量化，子块标题都包含父级
限定名，确保只向量化子块时仍可通过类名或文件名召回。

解析诊断用于计算 `parse_quality`：无错误为 `exact`，少量错误且关键符号可提取为 `partial`，无法
得到可信结构为 `fallback`。`partial` 结果允许入库并记录警告；`fallback` 使用现有文本分块。

## 每知识库三档 LLM 抽取

`CodeExtractionPolicy` 在进入现有实体/事件抽取边界前读取当前知识库配置：

- `off`：不调用 LLM。Tree-sitter、符号分块、向量和 FTS 仍完整运行。
- `comments`：默认。只将 Docstring、块注释和连续行注释按所属文件/类/函数合并为临时抽取输入。
  Shebang、编码声明、格式化/静态检查指令和明显许可证模板不发送给 LLM。抽取结果引用所属符号块，
  不额外创建可检索的重复注释块。
- `all`：只提交函数、方法、类型等代码子块；跳过紧凑父块，避免父子重复。仍受现有抽取并发、超时、
  重试和单块 token 上限限制。

设置修改只影响后续新入库或重新处理的代码。知识库页提供“保存并重新处理现有代码”，必须由用户
明确触发，不能保存设置时静默启动高成本重建。

## 检索行为

普通文档继续使用现有“命中子块后父块替换”行为。代码块采用专门的上下文组合：

```text
紧凑父级上下文

精确命中的函数、方法或语句片段
```

这样既保留类、接口或文件语境，也不会因父块替换而丢失真正命中的方法正文。组合结果受现有上下文
token 预算限制；预算不足时优先保留命中子块，再截断父级目录。

同时命中父块和多个子块时，以 `symbol_id + content_sha256` 去重。同一路径更新期间只返回 Document
当前 `content_sha256` 对应的 revision。向量、LanceDB FTS、grep 回退及现有规则融合次序不改变。

引用信息至少展示相对路径和起止行号。解析降级的文本块没有符号跨度时仍显示相对路径，不伪造行号。

## API

### 知识库代码配置

```text
GET /api/v1/sources/{source_id}/code-config
PUT /api/v1/sources/{source_id}/code-config
```

响应只暴露经过验证的代码配置，不直接返回 `Source.config` 中可能属于其他连接器的字段。PUT 合并
`code_ingest` 子对象并保留其他键。

### 目录计划与文件上传

```text
POST /api/v1/sources/{source_id}/code-folder/plan
POST /api/v1/sources/{source_id}/code-folder/file
```

plan 接收最多 1000 个 `{relative_path, size_bytes, content_sha256}` 项并返回逐项动作与原因。file 使用
multipart 接收一个文件及其相对路径和声明哈希。两个接口均要求登录、验证知识库所有权并重新执行
服务端安全检查。

### Tree-sitter 资源

```text
GET  /api/v1/system/tree-sitter/status
POST /api/v1/system/tree-sitter/download
POST /api/v1/system/tree-sitter/pause
POST /api/v1/system/tree-sitter/resume
POST /api/v1/system/tree-sitter/repair
```

状态包含版本、manifest 总数、已校验数量、当前语言、字节/语言进度、实际磁盘占用、状态和脱敏错误。
重复启动操作必须幂等。正在切换资源或被解析任务持有时不删除 active 目录。

## 桌面界面

知识库详情页新增：

- “选择代码文件夹”按钮。
- 扫描确认窗口，按解析器与动作展示数量。
- PDF/Office/电子书的独立默认关闭勾选项及 MinerU 成本提示。
- 上传、解析、完成、更新、跳过、忽略、保留和失败统计。
- 逐文件错误及“仅重试失败项”。
- “代码 LLM 抽取范围”三档设置。
- “保存并重新处理现有代码”按钮和明确成本提示。

系统设置的“本地运行资源”区域新增 Tree-sitter 卡片：版本、`已安装 / 306`、下载进度、磁盘占用、
下载、暂停、继续和校验修复按钮。设置页还提供可搜索的官方语言/扩展名清单及每种语言的安装状态。

目录选择使用现有桌面 Chromium 能力，不向 preload 增加任意路径读取 API。前端扫描与哈希工作在受限
并发的 Worker 中，页面卸载时停止尚未提交的本地扫描，但不取消已经提交给后端的文档任务。

## 安全与错误处理

- 所有相对路径在前后端双重规范化，服务端以解析后的目标路径确认其仍位于知识库存储目录内。
- 不接受前端提供的绝对路径或存储目标，不跟随符号链接。
- 哈希、大小、语言和 MIME 仅作前端提示，后端重新计算或探测。
- 秘密文件默认排除；扫描确认窗口明确列出排除原因。
- 资源下载只允许固定官方仓库、固定版本和 manifest 中的文件，执行官方校验或发布哈希校验。
- 下载、解析和入库错误不得包含 API Key、完整本地绝对路径或文件正文。
- parser 缺失、ABI 错误或下载失败时，文本文件降级并记录原因；二进制文件直接拒绝。
- 更新通过 revision 和补偿清理保持旧索引可用；清理失败进入可重试任务，不把整个知识库标为失败。
- LLM 抽取失败沿用现有任务重试；结构化代码块已经成功入库时，不因可选抽取失败删除代码索引。

## 可观测性

日志记录知识库 ID、文档 ID、相对路径哈希、语言、路由、解析质量、符号/块数量、耗时和降级原因，
不记录源码正文。性能指标区分扫描、哈希、上传、解析、向量化、抽取和清理阶段。

知识库入库统计增加代码文件数、语言分布、Tree-sitter 精确/部分/降级数量，以及增量跳过数量。资源
状态记录当前版本、已安装语言数、最近校验时间和最后一次错误。

## 测试策略

### 单元测试

- 路由：文档优先、官方 manifest、特殊文件名、扩展名冲突、未知文本、二进制和秘密文件。
- 路径：Windows/POSIX 分隔符、Unicode、大小写、`.`/`..`、绝对路径、规范化冲突和符号链接。
- 语言探测：扩展名、Shebang、特殊文件名和候选解析错误率。
- 符号分块：文件、模块、类、嵌套类、函数、方法、构造函数、枚举、接口、宏和顶层语句。
- 长函数：语句边界、无安全边界降级、token 上限、完整覆盖和无越界。
- 元数据：稳定 `symbol_id`、有效 `parent_id`、祖先路径、相对路径、行号和 revision。
- 抽取策略：`off` 零 LLM 调用，`comments` 仅注释文本，`all` 仅代码子块；不同知识库隔离。
- 增量计划：新增、变化、未变化、缺失保留、哈希冲突和并发重复上传。
- 资源管理：下载、暂停、继续、幂等、校验失败、修复、旧版本保留和路径构造。
- 检索增强：代码父级前缀、子块优先、预算截断、revision 过滤和稳定去重。

### 集成测试

使用 Python、JavaScript/TypeScript、C/C++、Rust、Go、Java、C#、Shell、HTML、YAML/JSON 等代表性
fixture 验证真实 Tree-sitter `process()` 结果。慢速测试验证官方 manifest 中 306 个解析器全部下载、
可加载并能对最小合法/空输入返回受控结果；常规 CI 不重复下载全包，使用缓存或单独的资源作业。

真实入库测试覆盖：目录计划、逐文件上传、Document 字段、符号块写库、parent_id 回填、向量过滤、
FTS 搜索、代码上下文组合、变化文件替换及失败保留旧 revision。

普通 PDF、Office、Markdown、纯文本和现有 A4 父子检索必须通过回归测试。单文件 HTML/JSON 仍验证
MarkItDown，代码目录中的 HTML/JSON 验证 Tree-sitter 路由。

### 前端测试

- 目录选择、Worker 清单、扫描确认、解析器分类、进度与失败重试。
- 三档配置读取、默认值、保存、知识库隔离和重新处理确认。
- Tree-sitter 状态、下载、暂停、继续、修复及按钮禁用条件。
- TypeScript typecheck、ESLint、Vitest 和中英文 i18n 一致性检查。

## 验收标准

1. 桌面端可递归选择完整代码目录并保留相对路径。
2. 重复导入只处理新增和变化文件；本地删除项保留并明确提示。
3. 代表性语言生成正确、可追溯行号的符号级父子块。
4. 搜索限定符号名、文件路径或源码内容能返回命中代码及紧凑父级上下文。
5. 两个知识库可分别设置 `off` 和 `comments`/`all`，调用内容和成本互不影响。
6. 全部解析器安装后，断网仍可解析官方支持的语言。
7. 单个文件更新或解析失败不破坏旧索引，也不中断同批其他文件。
8. 敏感、生成和二进制文件不会静默进入知识库。
9. PDF、Office、Markdown、普通文本和单文件 HTML/JSON 的既有行为无回归。
10. 后端相关测试、前端 typecheck、ESLint、Vitest 和 i18n 检查通过。

## 兼容与上线

功能按增量方式启用。数据库迁移只增加可空字段和索引；历史文档无需重建。新代码目录导入自动使用
Tree-sitter，普通上传继续原路由。已有知识库缺少代码抽取配置时默认 `comments`，但只有新入库或用户
明确重新处理的代码才执行新策略。

实现应分阶段交付：数据库与配置、资源管理、解析与分块、增量目录导入、检索增强、前端和文档。
每阶段独立测试，最终统一更新 README、README-CN、架构、配置、优化计划和用户指南中与入库及启动
相关的说明。

## 参考资料

- [Tree-sitter Language Pack 支持语言与扩展名](https://docs.tree-sitter-language-pack.xberg.io/languages/)
- [解析器下载、全量安装与缓存说明](https://docs.tree-sitter-language-pack.xberg.io/getting-started/quickstart/)
- [v1.13.7 官方发布资源](https://github.com/xberg-io/tree-sitter-language-pack/releases/tag/v1.13.7)
