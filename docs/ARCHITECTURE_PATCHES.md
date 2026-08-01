# SAG 应用层补丁登记（C3）

> 运行入口：本文仅记录实现补丁；启动 SAG-plus 请在 `apps/desktop` 运行 `npm run dev`。

> 目的：集中登记所有针对第三方依赖（zleap-sag / litellm）的应用层补丁，
> 说明每个补丁解决的问题、安装点、卸载函数与回归测试，降低升级成本。

| # | 补丁模块 | 解决的问题 | 安装点 | 卸载 | 回归测试 |
|---|---|---|---|---|---|
| 1 | `sag_api/sag/compat.py` | zleap-sag SQLite 异步 reset / pool / int 兼容 / extract 兼容 | `main.py` lifespan | 内嵌 context | 全量单测 |
| 2 | `sag_api/sag/lancedb_write_compat.py` | LanceDB 追加/更新分离（bulk_append_new） | `main.py` | — | 向量写入测试 |
| 3 | `sag_api/sag/lancedb_search_compat.py` | ANN 检索参数（nprobes/refine_factor）召回率修复 | `main.py` | — | test_api_smoke |
| 4 | `sag_api/sag/vector_write_queue.py` | 事件/分块向量写入队列化（批处理、租约、幂等） | `main.py` | — | 向量队列测试 |
| 5 | `sag_api/sag/embedding_backend.py` | 本地 bge-m3（llama-cpp）替换 zleap OpenAI embedding | `main.py` + 设置保存 | `uninstall_embedding_backend` | test_embedding_backend |
| 6 | `sag_api/sag/chunking_compat.py` | 结构感知分块（代码块/表格不切断） | `main.py` | `uninstall_structural_chunking_patch` | test_chunking_structural |
| 7 | `sag_api/sag/lancedb_fts.py` | BM25 独立召回（LanceDB FTS + tantivy）；同步索引/查询经 worker thread 执行，避免阻塞 API 事件循环 | 懒加载（首次检索） | — | test_lancedb_fts |
| 8 | `sag_api/core/litellm_policy.py` | LiteLLM pre-call 策略：抽取/LLM 重排作用域关闭思考，聊天四档工具选择与思考控制，按 provider 合并兼容参数 | `main.py` | `uninstall_litellm_policy` | `test_litellm_policy`、模型配置测试 |
| 9 | `sag_api/sag/parent_child.py` | 父子分块（A4）：入库 parent_id 回填 + 父块向量过滤 + 检索父上下文 | `main.py` | `uninstall_parent_child_loader_patch` | test_parent_child |

## 本地模型管理

`sag_api/sag/local_model_manager.py` 是应用层服务而不是第三方兼容补丁，因此不计入上表。
它只接受经过登记的三种 embedding 与三种 reranker Q8 GGUF 文件，按 `embedding/`、`reranker/`
分目录保存；旧 `bge-m3/` 路径仍会被识别。下载使用 `.part` 临时文件、长度与 ETag 校验后原子
提交。`/system/local-models*` 端点要求已登录用户；设置页可按需安装 CPU 版
`llama-cpp-python`，并可选安装提供 `LlamaEmbedding` / `pooling=rank` 的原生重排运行时。
普通 embedding 运行时不会被误判为支持 Cross-Encoder 重排；不可用时检索保留融合排序。模型与
后端均不会在启动时下载。

## 工具调用与思考策略

模型配置页提供“工具轮关闭思考（推荐）”“全程保留思考”“自动工具选择”和“全程关闭思考”
四档。Agent 路由器仍只负责意图与 `tool_choice`；`litellm_policy.py` 在请求副本上识别指定函数或
`required`，按当前策略关闭思考或改写为 `auto`。工具调用结束后的回答请求不会继承上一次的临时
覆盖。入库抽取、实体事件抽取和 LLM 重排的独立关闭思考作用域始终优先保留。

## 运行期验证

- `apps/api/scripts/eval_retrieval.py` 是 A5 检索评估入口。它会在调用者上下文预热引擎，再执行并发检索，避免 zleap-sag 的跨 `ContextVar` 关闭警告；输出每条用例的命中数与耗时。
- 当前内置评估集是轻量回归基线，不替代面向真实业务语料的人工标注评估集。

## 治理约定
- 新补丁必须提供 `install_*` / `uninstall_*`（可卸载的）或幂等标记
- 每个补丁必须有对应回归测试（上表最后一列）
- 升级 zleap-sag / litellm 前先跑全部补丁回归测试
- 发现上游已修复的补丁及时移除并更新本表
