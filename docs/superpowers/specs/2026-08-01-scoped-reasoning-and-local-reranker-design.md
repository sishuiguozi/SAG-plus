# 抽取/重排关闭推理与本地重排模型设计

## 目标

移除对 `sag_llm_proxy.py` 的依赖：SAG 在自身进程内仅为“入库后的实体/事件抽取”和
“可选 LLM 重排”关闭模型推理。聊天回答、Agent 调用和普通检索保持原有模型行为。

同时在桌面设置中增加可按需安装、下载并启用的本地 Q8 GGUF 向量与 Cross-Encoder 重排模型，
以及可直接接入 Qwen 百炼、vLLM 等服务的专用重排 API。首发提供三种向量模型和三种本地
重排模型，用于中文、英文、代码和混合知识库的召回与最终候选排序。

## 范围与非目标

- 覆盖增量入库的实体/事件抽取，以及 `search_llm_rerank_enabled=true` 时的 LLM 重排。
- 覆盖 OpenAI-compatible、Qwen/vLLM/SGLang 和 DeepSeek/OpenCode 风格关闭推理请求。
- 本地或 API 重排只处理已召回的有限候选（默认最多 20 条）；不改变 BM25、父子分块或索引写入。
- 切换本地 embedding 模型必须重新向量化对应知识库；不同模型生成的向量不得混在同一索引。
- 本次不更改聊天回答的推理开关，不删除现有 LLM 重排功能，也不把远程 Rerank API 用于入库或聊天。

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

扩展现有本地模型管理器，使本地 GGUF embedding 与 reranker 都按需下载，默认不占下载和内存。
Qwen 模型通过现有 `llama-cpp-python` 后端执行；BGE Cross-Encoder 通过专用的可选 GGUF
reranker 运行时执行。两类运行时分别有安装状态和按钮，不能假设一个后端可执行所有模型。

| 类型 | Q8 GGUF 模型 | 档位与用途 |
| --- | --- | --- |
| 向量 | BGE-M3 Q8 | 现有稳定默认；1024 维 |
| 向量 | Qwen3-Embedding-0.6B Q8 | 轻量 Qwen 方案；1024 维 |
| 向量 | Qwen3-Embedding-4B Q8 | 高精度 Qwen 方案；2560 维，需要更多 CPU、内存和索引空间 |
| 重排 | BGE-Reranker-v2-M3 Q8 | 成熟的多语言 Cross-Encoder |
| 重排 | Qwen3-Reranker-0.6B Q8 | 轻量、代码和多语言的默认推荐 |
| 重排 | Qwen3-Reranker-4B Q8 | 高精度选项，需要更多 CPU 与内存 |

- 后端安装按钮：保留 `llama-cpp-python` 安装入口，并新增 BGE GGUF reranker 运行时安装入口；
  状态可轮询、失败可读。
- 模型下载按钮：下载固定、白名单的上述六个 Q8 GGUF 文件到应用数据目录的 `embedding/` 或
  `reranker/` 子目录；下载先写 `.part`，完成后原子替换。用户可下载多个模型，但每类一次只启用一个。
- 启用开关：只有后端与模型均 ready 时可启用；启用后对规则融合结果的前 N 条计算 query-passage
  分数并按分数降序排列。初始 N 使用现有 `search_llm_rerank_candidates` 的上限（默认 8），但
  不调用 LLM。
- 每种运行期客户端受独立锁保护，防止并发搜索重复加载模型；配置改变或应用关闭时释放客户端。
- 后端缺失、模型缺失、加载/推理异常时记录可操作日志并直接回退到已有规则排序，不使搜索失败。

本地 Cross-Encoder 输出是相关性分数，不会生成可见文本或推理内容，因此不需要“关闭推理”。

### 专用 Rerank API

API 重排与本地重排是并列来源：每次检索只选择一个来源，避免把同一候选重复计费或产生难以
解释的双重排序。设置页提供 `关闭`、`本地模型`、`Rerank API` 和保留兼容的 `LLM 重排` 四种模式。

Rerank API 使用完整可配置 URL，而不是把路径拼死。请求和响应采用 Qwen 百炼、Jina、Cohere 及
vLLM 均支持的语义：

```json
{"model":"qwen3-rerank","query":"...","documents":["..."],"top_n":8,"instruct":"..."}
```

响应读取 `results[].index` 与 `results[].relevance_score`；未知/额外字段忽略。这样用户可填百炼的
`https://dashscope-intl.aliyuncs.com/compatible-api/v1/reranks`，也可填自建 vLLM 的
`http://host:port/v1/rerank`。API 密钥仅保存在服务端设置，状态接口与日志绝不返回明文。请求失败、
超时、重复/越界索引或无有效分数时稳定回退到已有规则顺序。

现有 LLM 重排保留为兼容模式，不再作为本地/API 的自动后备；用户明确选中它时才运行。此模式继续
使用场景化关闭推理策略。

## 设置与 API

模型配置页将“本地模型”分为向量模型和重排模型两部分：后端状态/安装、模型状态/下载、
启用开关、候选数及刷新按钮。重排来源选择为 `本地模型` 时显示本地模型控制；选择 `Rerank API`
时显示完整 URL、API Key、模型、可选 instruction、超时和连接测试按钮。所有模型默认不下载；
切换向量模型时页面会明确提示重新向量化对应知识库。

增加受登录保护的本地 reranker 状态、后端安装和模型下载接口；状态响应明确区分 embedding
与 reranker，避免现有 `LocalModelManager` API 语义被破坏。系统配置新增统一的
`search_rerank_mode`、`search_rerank_candidates`、本地模型选择和 API 凭据字段；保留
`search_llm_rerank_enabled` 作为旧配置兼容读取。保存后立即作用于后续检索，无须重建知识库。

## 测试与验收

- 请求策略单测：`extract` 与 `rerank` 分别覆盖 Qwen、DeepSeek/OpenCode、通用 OpenAI-compatible；
  聊天场景必须不带场景化关闭字段。
- zleap 抽取真实调用边界测试：标记在异常和并发任务后均恢复。
- LLM 重排测试：关闭推理策略生效，模型调用失败仍回退原规则顺序。
- 本地 reranker 管理器测试：白名单校验、按需安装状态、下载状态、失败信息和无自动下载。
- 本地 reranker 客户端测试：分数映射回原始片段、稳定排序、异常回退、单实例锁。
- Rerank API 测试：百炼 `/reranks` 与 vLLM `/v1/rerank` 完整 URL、认证头、请求体、分数映射、
  API Key 脱敏和失败回退。
- 前端：来源切换、API 测试、状态、下载/安装按钮禁用条件、启用开关、中文/英文文案和类型检查。

## 验收标准

用户不再需要启动外部代理。入库抽取及 LLM 重排对支持的模型会发送正确的关闭推理参数；聊天
请求不会被这些参数污染。用户可在设置页自行安装所需后端，并从三种 Q8 向量模型及三种 Q8
重排模型中分别下载、启用一种，也可改为填写 Qwen 百炼或自建服务的 Rerank API。切换向量模型后，
系统提示用户重新向量化相应知识库。启用本地或 API 重排后，知识库检索先完成现有向量/BM25/
规则融合，再以相应分数稳定重排有限候选。任何 API 或本地模型的前置条件、网络或推理失败都不会
中断搜索。
