# SAG 应用层补丁登记（C3）

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
| 7 | `sag_api/sag/lancedb_fts.py` | BM25 独立召回（LanceDB FTS + tantivy） | 懒加载（首次检索） | — | test_lancedb_fts |
| 8 | `sag_api/core/litellm_policy.py` | LiteLLM pre-call 策略（provider 参数） | `main.py` | `uninstall_litellm_policy` | 模型配置测试 |
| 9 | `sag_api/sag/parent_child.py` | 父子分块（A4）：入库 parent_id 回填 + 父块向量过滤 + 检索父上下文 | `main.py` | `uninstall_parent_child_loader_patch` | test_parent_child |

## 治理约定
- 新补丁必须提供 `install_*` / `uninstall_*`（可卸载的）或幂等标记
- 每个补丁必须有对应回归测试（上表最后一列）
- 升级 zleap-sag / litellm 前先跑全部补丁回归测试
- 发现上游已修复的补丁及时移除并更新本表
