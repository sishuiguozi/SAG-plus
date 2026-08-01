# 场景化关闭推理与本地/API 重排实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 取消对外部 `sag_llm_proxy.py` 的依赖；仅在入库实体/事件抽取与兼容 LLM 重排时关闭推理。为检索增加可选的本地 Q8 GGUF 与专用 Rerank API 两种 Cross-Encoder 重排来源，并在设置页完成安装、下载、配置和测试。

**架构：** 用 `ContextVar` 将 LLM 调用场景从抽取/重排调用边界传到既有 LiteLLM hook；默认聊天路径完全不受影响。检索先运行现有融合排序，再由统一的 reranker 路由器按配置选择 local、api 或旧 LLM 模式。模型目录、下载来源与运行时能力由单一不可变目录表定义，API 重排接收完整 URL，因此兼容百炼 `/reranks` 与 vLLM `/v1/rerank`。

**技术栈：** Python 3.11、FastAPI、Pydantic、`contextvars`、LiteLLM、httpx、llama-cpp-python、CrispEmbed、pytest、React、TypeScript、Vitest、Next.js。

---

## 文件结构

- 创建：`apps/api/sag_api/core/llm_call_context.py` — 异步安全的 LLM 场景标记。
- 修改：`apps/api/sag_api/core/litellm_policy.py` — 仅在标记场景附加关闭推理字段。
- 修改：`apps/api/sag_api/sag/incremental_processor.py` — 实体/事件抽取调用边界。
- 修改：`apps/api/sag_api/services/retrieval_service.py` — LLM、本地与 API 重排路由。
- 创建：`apps/api/sag_api/sag/local_model_catalog.py` — 六种 Q8 权重与运行时的唯一白名单。
- 修改：`apps/api/sag_api/sag/local_model_manager.py` — 分类状态、后端安装和安全下载。
- 修改：`apps/api/sag_api/sag/embedding_backend.py` — BGE/Qwen 的本地 embedding 加载与维度契约。
- 创建：`apps/api/sag_api/sag/local_reranker.py` — 本地 Cross-Encoder 分数适配器。
- 创建：`apps/api/sag_api/sag/rerank_api_client.py` — `/reranks`、`/rerank` 的专用 HTTP 客户端。
- 修改：`apps/api/sag_api/core/config.py`、`apps/api/sag_api/schemas/system.py`、`apps/api/sag_api/services/settings_service.py`、`apps/api/sag_api/sag/config_builder.py` — 统一重排配置、旧字段兼容与有效 embedding 维度。
- 修改：`apps/api/sag_api/api/v1/system.py` — 模型管理、API 测试与 API Key 脱敏端点。
- 测试：`apps/api/tests/test_litellm_policy.py`、`apps/api/tests/test_rerank_api_client.py`、`apps/api/tests/test_local_model_manager.py`、`apps/api/tests/test_local_reranker.py`、`apps/api/tests/test_retrieval_service.py`、`apps/api/tests/test_settings_system_config.py`。
- 修改：`apps/web/lib/types.ts`、`apps/web/lib/api.ts`、`apps/web/lib/local-model-manager.ts`、`apps/web/components/features/model-config-form.tsx`。
- 测试：`apps/web/lib/local-model-manager.test.ts`、`apps/web/components/features/model-config-form.test.tsx`（或项目中相应既有测试文件）。
- 修改：`apps/web/messages/en-US.json`、`apps/web/messages/zh-CN.json`、`README.md`、`README-CN.md`、`apps/desktop/README.md`、`docs/ARCHITECTURE_PATCHES.md`。

## 固定行为与兼容规则

- 新字段 `search_rerank_mode` 取值为 `off`、`local`、`api`、`llm`，默认 `off`；一次检索只执行其中一种重排来源。
- 保留 `search_llm_rerank_enabled` 读取兼容：旧配置为 true 且没有显式新模式时视为 `llm`；一经保存新设置，由 `search_rerank_mode` 成为唯一有效值。
- API 模式使用完整 `search_rerank_api_url`，请求为 `model/query/documents/top_n/instruct`，响应只相信有效且不重复的 `results[index, relevance_score]`。密钥不出现在任意 GET 响应、浏览器日志或异常详情中。
- API 失败、本地运行时/权重不可用、模型加载错误、无效分数或超时时，返回已有融合顺序；检索请求不可失败。
- 切换 embedding 提供商、文件或有效维度时标记“需重新向量化”，不清空数据、不混用旧/新向量。

### 任务 1：先锁定场景化关闭推理的契约

**文件：**
- 创建：`apps/api/sag_api/core/llm_call_context.py`
- 修改：`apps/api/sag_api/core/litellm_policy.py`
- 测试：`apps/api/tests/test_litellm_policy.py`

- [ ] **步骤 1：编写失败测试**

覆盖嵌套作用域、异常退出和并发 task 的 token 恢复；在无场景时，Qwen/普通 OpenAI 请求不得被自动补充关闭推理字段。分别断言：

1. `extract` 与 `rerank` 对 Qwen/vLLM 增加 `extra_body.chat_template_kwargs.enable_thinking=false`；
2. DeepSeek/OpenCode 端点增加 `extra_body.thinking={"type":"disabled"}`；
3. OpenAI-compatible/未知端点增加 `reasoning_effort="none"` 与必要的 `allowed_openai_params`；
4. 显式用户配置的关闭推理仍保留，但用户请求的开启字段在上述两个场景内被策略覆盖。

- [ ] **步骤 2：运行测试确认失败**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_litellm_policy.py -q`

预期：FAIL，因为场景模块和受限策略尚不存在。

- [ ] **步骤 3：实现 ContextVar 与策略**

新增只暴露 `llm_call_scope("extract" | "rerank")` 与 `current_llm_call_scenario()` 的模块；上下文管理器必须在 `finally` 用 token 恢复。将 LiteLLM policy 拆成“合并用户 extra body”与“仅对当前场景的 provider/URL 规则”，不再按模型名在全局自动关闭 Qwen 推理。LiteLLM hook 与 `generation/llm.py` 继续调用同一个策略函数。

- [ ] **步骤 4：回归验证**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_litellm_policy.py tests/test_units.py -q`

预期：所有策略测试通过，非场景化聊天请求保持原参数。

### 任务 2：将抽取和旧 LLM 重排接入场景边界

**文件：**
- 修改：`apps/api/sag_api/sag/incremental_processor.py`
- 修改：`apps/api/sag_api/services/retrieval_service.py`
- 测试：`apps/api/tests/test_incremental_processor.py`、`apps/api/tests/test_retrieval_service.py`

- [ ] **步骤 1：补充调用边界失败测试**

用 mock extractor 和 mock `LLMClient` 断言其 await 时的上下文分别是 `extract` 和 `rerank`，成功和抛异常后均回到 `None`；并发的普通 LLM 任务不能看到该标记。

- [ ] **步骤 2：运行失败测试**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_incremental_processor.py tests/test_retrieval_service.py -q`

预期：FAIL，因为生产调用点尚未设置 scope。

- [ ] **步骤 3：实现最小作用域包裹**

只在 `extractor.extract(...)` 的 await 周围进入 `extract` 作用域；只在旧 `_llm_rerank()` 调用 `LLMClient.complete()` 的 await 周围进入 `rerank` 作用域。不得把整个请求处理函数包裹进作用域。

- [ ] **步骤 4：回归验证**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_incremental_processor.py tests/test_retrieval_service.py tests/test_litellm_policy.py -q`

预期：调用边界、异常恢复和旧 LLM 重排回退均通过。

### 任务 3：定义统一重排配置和安全 API 配置

**文件：**
- 修改：`apps/api/sag_api/core/config.py`
- 修改：`apps/api/sag_api/schemas/system.py`
- 修改：`apps/api/sag_api/services/settings_service.py`
- 修改：`apps/api/sag_api/sag/config_builder.py`
- 测试：`apps/api/tests/test_settings_system_config.py`

- [ ] **步骤 1：编写配置迁移和脱敏失败测试**

覆盖默认 `off`、合法/非法 mode、候选上限 3–20、URL 必须为 http(s)、API Key 在设置输出中被 `***` 替代、更新请求可保留未变密钥，以及旧 `search_llm_rerank_enabled=true` 读为 `llm`。

- [ ] **步骤 2：运行失败测试**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_settings_system_config.py -q`

预期：FAIL，因为新字段和兼容转换尚未实现。

- [ ] **步骤 3：实现字段和迁移**

加入 `search_rerank_mode`、`search_rerank_candidates`、`search_local_rerank_model_file`、`search_rerank_api_url`、`search_rerank_api_key`、`search_rerank_api_model`、`search_rerank_api_instruction`、`search_rerank_api_timeout_ms`。将 API Key 作为写入专用字段，并在所有响应模型中以 `configured: bool` 替代明文。让推荐值、白名单更新、环境变量名称和系统设置 API 完全一致。

- [ ] **步骤 4：固定 zleap 基础开关**

在 `config_builder.py` 显式传递 `enable_think=False` 给 zleap `LLMConfig`，但不要用它覆盖聊天调用；其职责只是依赖内部默认配置。

- [ ] **步骤 5：回归验证**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_settings_system_config.py tests/test_units.py -q`

预期：新旧设置均能加载，任何响应不含 API Key。

### 任务 4：建立六款 Q8 模型和运行时的单一目录表

**文件：**
- 创建：`apps/api/sag_api/sag/local_model_catalog.py`
- 修改：`apps/api/sag_api/sag/local_model_manager.py`
- 修改：`apps/api/sag_api/core/config.py`
- 测试：`apps/api/tests/test_local_model_manager.py`

- [ ] **步骤 1：编写目录表失败测试**

断言目录恰含并分类以下文件，且拒绝未知/类别不符文件：

| 类型 | 固定文件 | 运行时 |
| --- | --- | --- |
| embedding | `bge-m3-Q8_0.gguf` | llama-cpp-python |
| embedding | `Qwen3-Embedding-0.6B-Q8_0.gguf` | llama-cpp-python |
| embedding | `Qwen3-Embedding-4B-Q8_0.gguf` | llama-cpp-python |
| reranker | `bge-reranker-v2-m3-q8_0.gguf` | CrispEmbed |
| reranker | `qwen3-reranker-0.6b-q8_0.gguf` | llama.cpp-compatible runtime |
| reranker | `Qwen3-Reranker-4B-Q8_0.gguf` | llama.cpp-compatible runtime |

同时断言 Qwen embedding 的有效维度分别为 1024 与 2560，旧的 BGE Q8 路径仍被识别。

- [ ] **步骤 2：运行失败测试**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_local_model_manager.py -q`

预期：FAIL，因为现有目录仅有五个 BGE embedding 文件。

- [ ] **步骤 3：实现模型描述符和下载清单**

用不可变 `ModelSpec` 表承载 kind、显示名、相对目录、固定下载 URL/版本、文件名、维度、估算磁盘大小、运行时和 SHA-256（源提供哈希时）。下载仅接受这个表中的文件，保持 `.part`、长度/哈希验证与原子替换。旧 `MODEL_CATALOG` 名称转为兼容导出，避免已有端点/测试突然失效。

- [ ] **步骤 4：把状态按能力拆分**

状态改为 `embedding`、`reranker` 两组，每组单列模型和所需 backend；保留顶层旧字段一版兼容读取。`LocalModelManager` 根目录改为稳定的应用模型根目录，具体文件落在 `embedding/`、`reranker/` 子目录。模型从不因状态读取或保存设置而自动下载。

- [ ] **步骤 5：回归验证**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_local_model_manager.py -q`

预期：白名单、去重下载、已有文件、进度和旧 BGE Q8 兼容测试通过。

### 任务 5：扩展本地 embedding，并防止混用向量空间

**文件：**
- 修改：`apps/api/sag_api/sag/embedding_backend.py`
- 修改：`apps/api/sag_api/core/config.py`
- 修改：`apps/api/sag_api/sag/config_builder.py`
- 测试：`apps/api/tests/test_embedding_backend.py`（创建或扩展既有测试）

- [ ] **步骤 1：编写加载/维度失败测试**

以注入的 fake llama client 验证三种 embedding 文件被解析到正确路径、Qwen 走 last-token pooling 所需参数、输出长度与目录表维度一致；模型切换释放旧进程级客户端。测试有效模型变更会返回 `embedding_reindex_required=true`，而不会删除现有数据。

- [ ] **步骤 2：运行失败测试**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_embedding_backend.py -q`

预期：FAIL，因为当前实现假定所有文件是 BGE-M3。

- [ ] **步骤 3：实现 descriptor 驱动的 embedding client**

由 `ModelSpec` 确定模型文件、`pooling_type`、维度和 `EmbeddingConfig.dimensions`。BGE-M3 保持当前行为；Qwen 使用官方 GGUF 所需 last-token pooling 和归一化。加载前校验 backend/model 可用，错误给出可执行提示；配置更新时关闭缓存实例。

- [ ] **步骤 4：接入重新向量化提示**

系统配置更新响应仅在 embedding 身份或维度真实改变时返回 `embedding_reindex_required` 与清晰说明；Web 复用已有文档重新处理入口，不创建隐式删除或自动批量任务。

- [ ] **步骤 5：回归验证**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_embedding_backend.py tests/test_settings_system_config.py -q`

预期：三个模型分发正确，旧 BGE 设置不变，切换不会产生混合索引假象。

### 任务 6：实现本地 Cross-Encoder 重排运行时

**文件：**
- 创建：`apps/api/sag_api/sag/local_reranker.py`
- 修改：`apps/api/sag_api/sag/local_model_manager.py`
- 修改：`apps/api/sag_api/api/v1/system.py`
- 测试：`apps/api/tests/test_local_reranker.py`、`apps/api/tests/test_local_model_manager.py`

- [ ] **步骤 1：编写本地重排失败测试**

通过注入 fake Qwen/BGE adapter 验证 query-document 配对、原始索引回填、按 score 降序且同分保持融合顺序、单实例异步锁、启用/缺失/异常三种回退。覆盖两个 backend 安装任务不会重复执行、失败状态可读。

- [ ] **步骤 2：运行失败测试**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_local_reranker.py tests/test_local_model_manager.py -q`

预期：FAIL，因为本地 reranker adapter 尚不存在。

- [ ] **步骤 3：实现 adapter 与运行时安装**

定义 `LocalReranker.rank(query, documents) -> list[float]`。Qwen adapter 以官方 yes/no 概率分数执行 Q8 GGUF；BGE adapter 通过 CrispEmbed 的 Cross-Encoder/rerank 入口执行。每个 runtime 使用独立状态、锁和卸载方法：`llama-cpp-python` 为 Python 依赖，CrispEmbed 采用固定版本 Windows CPU 可执行文件/库清单并校验下载哈希，不能依赖用户 PATH 或在启动时从源码编译。缺少运行时的安装按钮只影响所选模型类型。

- [ ] **步骤 4：接入受认证的模型管理端点**

把后端安装请求改为指定 capability；下载请求只接受同类文件。新增 `POST /system/local-models/reranker/test`，使用一对固定中英文样本加载/评分但不保存设置；响应只返回 ready、耗时、分数数量与脱敏错误。

- [ ] **步骤 5：回归验证**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_local_reranker.py tests/test_local_model_manager.py -q`

预期：本地评分路由和后端状态均通过；无需真实下载大模型即可完成自动化测试。

### 任务 7：实现完整 URL 的专用 Rerank API 和连接测试

**文件：**
- 创建：`apps/api/sag_api/sag/rerank_api_client.py`
- 修改：`apps/api/sag_api/api/v1/system.py`
- 修改：`apps/api/sag_api/schemas/system.py`
- 测试：`apps/api/tests/test_rerank_api_client.py`

- [ ] **步骤 1：编写 API 客户端失败测试**

用 `httpx.MockTransport` 覆盖百炼完整 URL（`/reranks`）和 vLLM 完整 URL（`/v1/rerank`），断言 Bearer 头、请求体、可选 `instruct`、超时、乱序结果、同分稳定排序、重复/越界/NaN index 忽略、4xx/5xx/超时回退。测试 API Key 永远不会在异常文本或 JSON 响应中出现。

- [ ] **步骤 2：运行失败测试**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_rerank_api_client.py -q`

预期：FAIL，因为 HTTP client 和测试端点尚不存在。

- [ ] **步骤 3：实现安全客户端**

使用复用的 `httpx.AsyncClient` 与配置超时，向用户填写的 URL 发 POST；仅接收 https 或显式本地环回 http，拒绝私网以外的纯 http。构造 `model/query/documents/top_n/instruct`，验证响应结构后映射为候选分数。对所有错误返回结构化、无密钥的 reason，供路由器记录和回退。

- [ ] **步骤 4：实现 API 测试端点**

新增受认证 `POST /system/reranker-api/test`：接收尚未保存的 URL/key/model/instruction/timeout，调用两条固定测试文本，返回连通、有效结果数和耗时；禁止把请求 key 写入 settings、日志或响应。

- [ ] **步骤 5：回归验证**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_rerank_api_client.py tests/test_settings_system_config.py -q`

预期：百炼、vLLM 格式和测试端点全部通过。

### 任务 8：在检索出口统一选择重排来源

**文件：**
- 修改：`apps/api/sag_api/services/retrieval_service.py`
- 测试：`apps/api/tests/test_retrieval_service.py`

- [ ] **步骤 1：编写路由失败测试**

对同一融合候选断言：`off` 不调用任何 reranker；`local` 只调用 `LocalReranker`；`api` 只调用 `RerankAPIClient`；`llm` 只调用旧 `_llm_rerank`；任何失败严格回退融合顺序。覆盖候选数上限、父子分块结果、稳定并列和 API/local 不串联。

- [ ] **步骤 2：运行失败测试**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_retrieval_service.py -q`

预期：FAIL，因为现有服务只有布尔 LLM 重排分支。

- [ ] **步骤 3：实现模式路由**

先生成既有向量/BM25/词法/父子上下文融合结果，再截取 `search_rerank_candidates`，按 mode 执行一个 reranker。将 score 排序结果与未进入候选窗口的结果稳定合并；记录模式、候选量、耗时和脱敏回退原因。旧 LLM 分支继续使用任务 2 的 `rerank` scope。

- [ ] **步骤 4：回归验证**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_retrieval_service.py tests/test_rerank_api_client.py tests/test_local_reranker.py -q`

预期：所有模式正确、无重排时零开销、故障不会导致搜索失败。

### 任务 9：完成设置页的本地/API 双路径体验

**文件：**
- 修改：`apps/web/lib/types.ts`
- 修改：`apps/web/lib/api.ts`
- 修改：`apps/web/lib/local-model-manager.ts`
- 修改：`apps/web/components/features/model-config-form.tsx`
- 修改：`apps/web/messages/en-US.json`
- 修改：`apps/web/messages/zh-CN.json`
- 测试：`apps/web/lib/local-model-manager.test.ts`、`apps/web/components/features/model-config-form.test.tsx`

- [ ] **步骤 1：编写失败测试**

覆盖模式切换显示规则：`local` 才显示运行时、选择、下载和本地测试；`api` 才显示 URL、Key、模型、instruction、超时和 API 测试。验证模型/backend 未就绪不能启用本地重排，API 缺 URL/模型/key 不能保存 API 模式，测试按钮允许使用未保存但有效的表单值，密钥不被回显。

- [ ] **步骤 2：运行失败测试**

运行：`npm --prefix apps/web run test:unit -- lib/local-model-manager.test.ts components/features/model-config-form.test.tsx`

预期：FAIL，因为当前 UI 只有 embedding 本地模型与旧 LLM 布尔开关。

- [ ] **步骤 3：实现类型、API 和组件**

将模型状态按 embedding/reranker 表达；保持已经下载的旧 BGE 文件的兼容显示。重排区域包含模式选择、统一候选数、模型卡（尺寸/运行时/状态）、按需安装/下载/测试按钮和 1 秒轮询。API 区域提供“百炼 Qwen”预设（只填 URL、模型 `qwen3-rerank` 和推荐 instruction，不填 key），也允许自由修改完整 URL；测试使用当前表单，不要求先保存。

- [ ] **步骤 4：实现重新向量化提醒**

消费 `embedding_reindex_required`，显示非阻塞提醒和已有“重新处理文档”入口；不在保存时触发不可撤销数据库操作。

- [ ] **步骤 5：添加 i18n 并验证**

补齐中英文的模式、API URL、Key 已配置、百炼预设、测试、下载尺寸、后端缺失、回退和重新向量化文案。运行：

`npm --prefix apps/web run test:unit -- lib/local-model-manager.test.ts components/features/model-config-form.test.tsx`

`npm --prefix apps/web run typecheck && npm --prefix apps/web run lint && npm --prefix apps/web run i18n:check`

预期：测试、类型、lint 与 i18n 全部通过，无 missing-message。

### 任务 10：文档、全量回归与交付

**文件：**
- 修改：`README.md`
- 修改：`README-CN.md`
- 修改：`apps/desktop/README.md`
- 修改：`docs/ARCHITECTURE_PATCHES.md`
- 修改：`docs/SAG_OPTIMIZATION_2026.md`（如存在对应优化条目）

- [ ] **步骤 1：更新运行与安全说明**

说明桌面仍只需 `cd E:\\SAG-plus\\apps\\desktop` 后 `npm run dev`；模型不会自动下载。说明本地/API/LLM 三种重排模式、百炼 URL 示例、API Key 的保存与脱敏边界、切换 embedding 后必须重新向量化，以及检索的稳定回退行为。删除任何要求启动 `sag_llm_proxy.py` 的说明。

- [ ] **步骤 2：执行后端验证**

运行：

`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_litellm_policy.py tests/test_incremental_processor.py tests/test_rerank_api_client.py tests/test_local_model_manager.py tests/test_local_reranker.py tests/test_retrieval_service.py tests/test_settings_system_config.py -q`

预期：相关后端测试全部通过。

- [ ] **步骤 3：执行前端和桌面验证**

运行：

`npm --prefix apps/web run typecheck && npm --prefix apps/web run lint && npm --prefix apps/web run i18n:check`

`npm --prefix apps/desktop run typecheck`

`git diff --check`

预期：均通过，且没有空白字符错误。

- [ ] **步骤 4：人工小规模验收**

用已有知识库依次验证 `off`、本地模型（下载后）、百炼/自建 API（有效 key）和旧 LLM 四种模式；确认 API 测试无需保存、搜索可在远端/本地失败时返回规则结果、聊天回答不带场景化关闭推理字段。

- [ ] **步骤 5：提交并推送**

运行：`git add apps docs README.md README-CN.md; git commit -m "feat: add scoped reasoning and reranker sources"; git push origin main`

预期：工作树干净，远程 `main` 与本地 HEAD 一致。
