# 抽取/重排关闭推理与本地重排模型设计

## 目标

移除对 `sag_llm_proxy.py` 的依赖：SAG 在自身进程内仅为“入库后的实体/事件抽取”和
“可选 LLM 重排”关闭模型推理。聊天回答、Agent 调用和普通检索保持原有模型行为。

同时在桌面设置中增加可按需安装、下载并启用的本地 Cross-Encoder 重排器。默认模型为
`BAAI/bge-reranker-v2-m3`，用于中文、英文和混合知识库的最终候选排序。

## 范围与非目标

- 覆盖增量入库的实体/事件抽取，以及 `search_llm_rerank_enabled=true` 时的 LLM 重排。
- 覆盖 OpenAI-compatible、Qwen/vLLM/SGLang 和 DeepSeek/OpenCode 风格关闭推理请求。
- 本地重排只处理已召回的有限候选（默认最多 20 条）；不改变向量、BM25、父子分块或索引写入。
- 本次不更改聊天回答的推理开关，不引入远程 rerank API，也不删除现有 LLM 重排功能。

## 架构

### 场景化关闭推理

新增一个 `ContextVar` 保存当前 LLM 调用场景：`extract`、`rerank` 或 `None`。它通过 async
任务上下文传递，因此不会串到并发的聊天或其他任务。

1. `IncrementalDocumentProcessor` 在调用 zleap-sag `extractor.extract()` 前设置 `extract`，在
   `finally` 中恢复 token。
2. `_llm_rerank()` 在调用 `LLMClient.complete()` 前设置 `rerank`，同样在 `finally` 中恢复。
3. 应用已安装的 LiteLLM pre-call hook 读取该 `ContextVar`。只有场景为 `extract` 或 `rerank`
   时才补充关闭推理参数。

这一做法不修改 `site-packages`，也不使用进程全局可变开关；zleap-sag 自己持有的 LiteLLM
调用与 SAG 的 `LLMClient` 都会经过同一个 hook。

### 多协议请求策略

关闭策略根据配置的 provider、base URL 和模型名称选择最小的兼容字段。用户配置的
`llm_extra_body` 先合并，随后由关闭策略覆盖冲突的推理字段。

| 路由类别 | 发送字段 |
| --- | --- |
| OpenAI、OpenAI-compatible、未知路由 | `reasoning_effort: "none"` |
| Qwen、vLLM、SGLang | `extra_body.chat_template_kwargs.enable_thinking: false`，并保留标准字段 |
| DeepSeek/OpenCode 风格 endpoint | `extra_body.thinking: {"type": "disabled"}`，并保留标准字段 |

模型/endpoint 规则只识别关闭推理能力，不把所有供应商私有字段盲发给每个上游。未知模型安全
降级到标准 `reasoning_effort: "none"`。上游拒绝请求时，抽取仍使用现有任务重试；LLM 重排仍
回退到规则融合后的原顺序。

现有 `LLMConfig.enable_think=False` 继续作为 zleap-sag 的基础配置；新策略补足其只覆盖
`chat_template_kwargs.enable_thinking=false` 的限制。

### 本地 Cross-Encoder 重排

增加独立的 `LocalRerankerManager` 和 `LocalRerankerClient`，不复用 embedding 的 GGUF/llama.cpp
后端。`bge-reranker-v2-m3` 是 XLM-R Cross-Encoder，需要其官方 Transformers 权重和可选的
PyTorch CPU 推理后端；两者均按需安装/下载，默认不占下载和内存。

- 后端安装按钮：安装 `sentence-transformers` 及其 CPU 运行依赖，状态可轮询、失败可读。
- 模型下载按钮：下载固定、白名单的 `BAAI/bge-reranker-v2-m3` 到应用数据目录的 `reranker/`
  子目录；下载先写 `.part`，完成后原子替换。
- 启用开关：只有后端与模型均 ready 时可启用；启用后对规则融合结果的前 N 条计算 query-passage
  分数并按分数降序排列。初始 N 使用现有 `search_llm_rerank_candidates` 的上限（默认 8），但
  不调用 LLM。
- 运行期只创建一个受锁保护的本地客户端，防止并发搜索重复加载约 0.6B 参数模型；配置改变或
  应用关闭时释放该客户端。
- 后端缺失、模型缺失、加载/推理异常时记录可操作日志并直接回退到已有规则排序，不使搜索失败。

本地 Cross-Encoder 输出是相关性分数，不会生成可见文本或推理内容，因此不需要“关闭推理”。
现有 LLM 重排保留为最后可选兜底：仅当本地重排关闭或失败且 LLM 开关开启时才执行。

## 设置与 API

模型配置页新增“本地重排模型”区块，独立于本地 embedding：后端状态/安装、模型状态/下载、
启用开关、候选数及刷新按钮。重排模型不会在用户未点击安装或下载时自动获取。

增加受登录保护的本地 reranker 状态、后端安装和模型下载接口；状态响应明确区分 embedding
与 reranker，避免现有 `LocalModelManager` API 语义被破坏。系统配置新增
`search_local_rerank_enabled` 与 `search_local_rerank_candidates`，推荐默认值分别为 `false` 和
`8`。保存后立即作用于后续检索，无须重建知识库。

## 测试与验收

- 请求策略单测：`extract` 与 `rerank` 分别覆盖 Qwen、DeepSeek/OpenCode、通用 OpenAI-compatible；
  聊天场景必须不带场景化关闭字段。
- zleap 抽取真实调用边界测试：标记在异常和并发任务后均恢复。
- LLM 重排测试：关闭推理策略生效，模型调用失败仍回退原规则顺序。
- 本地 reranker 管理器测试：白名单校验、按需安装状态、下载状态、失败信息和无自动下载。
- 本地 reranker 客户端测试：分数映射回原始片段、稳定排序、异常回退、单实例锁。
- 前端：状态、下载/安装按钮禁用条件、启用开关、中文/英文文案和类型检查。

## 验收标准

用户不再需要启动外部代理。入库抽取及 LLM 重排对支持的模型会发送正确的关闭推理参数；聊天
请求不会被这些参数污染。用户可在设置页自行安装后端、下载并启用本地
`bge-reranker-v2-m3`；启用后知识库检索先完成现有向量/BM25/规则融合，再以本地分数稳定重排
有限候选。任何本地模型前置条件或推理失败都不会中断搜索。
