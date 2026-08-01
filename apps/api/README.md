# SAG-plus API

API 是 SAG-plus 桌面运行时的一部分，由
[`apps/desktop`](../desktop/README.md) 中的 `npm run dev` 自动启动或复用。
不要把本目录作为独立启动入口。

## 架构

| 层 | 目录 | 职责 |
| --- | --- | --- |
| 适配层 | `sag_api/sag/` | 唯一导入 `zleap-sag` 的兼容层；连接信源与 `DataEngine`。 |
| 文档与任务 | `sag_api/parsing/`、`sag_api/jobs/` | 文档转换、抽取状态机与后台编排。 |
| 向量写入 | `sag_api/sag/vector_write_queue.py` | 持久化批量写入、单写者、幂等、租约、重试和恢复。 |
| 检索 | `sag_api/services/retrieval_service.py` | 语义召回、FTS/BM25、缓存、重排与父子上下文增强。 |
| 生成与 Agent | `sag_api/generation/`、`sag_agent/`、`sag_api/tools/` | 流式回答、引用、工具与 Agent 生命周期。 |
| 接口 | `sag_api/api/v1/`、`sag_api/mcp/` | HTTP 路由与知识库 MCP。 |

本地数据、数据库、上传文件和模型缓存不应提交到 Git。向量维护和恢复脚本位于
`apps/api/scripts/`；执行任何清理前先做只读审计并保留备份。
