---
name: sag-knowledge
description: Use when an AI coding agent needs to search, browse, cite, or read documents from a SAG knowledge base through MCP.
---

# SAG Knowledge Base

SAG 把你的文档变成可检索、可溯源的知识库，并以 **MCP** 暴露给任何 Agent。
本 Skill 教会 Agent 使用 SAG 的 8 个只读工具完成「先确认范围、再看结构、最后精确取内容」的探索漏斗。

## 连接

在 SAG 界面「设置 → 集成」复制全库 MCP 配置。需要限定单个信源时，在 HTTP URL 后添加 `?source_id=<SOURCE_ID>`，或：

```bash
curl -s http://<host>/api/v1/sources/<SOURCE_ID>/mcp -H "Authorization: Bearer <TOKEN>"
```

- **HTTP（推荐）**：`http://<host>/mcp/?source_id=<SOURCE_ID>`，Header `Authorization: Bearer <TOKEN>`
- **stdio**：`SAG_MCP_SOURCE_ID=<SOURCE_ID> python -m sag_api.mcp.server`（需 apps/api 环境）

## 工具与用法（漏斗顺序）

| 顺序 | 工具 | 何时用 |
| --- | --- | --- |
| 1 | `list_sources()` | 查看当前可访问的知识来源、文档数和分块数，并获取 source_id |
| 2 | `list_documents(source_id?)` | 了解范围内有哪些文档（id/状态/分块数） |
| 3 | `outline(document_id)` | 看目标文档的大纲（heading 序 + chunk_id），定位章节 |
| 4 | `search(query, top_k?, source_id?)` | 语义召回：自然语言问题 → 带编号证据（含 chunk_id） |
| 5 | `grep(pattern, limit?, source_id?)` | 精确匹配：专名/编号/代码片段（大小写不敏感） |
| 6 | `get_chunk(chunk_id, source_id?)` | 读某个分块完整原文（引用溯源终点） |
| 7 | `read(document_id, offset?, limit?)` | 按行分页读原始文件（默认 120 行/页） |
| 8 | `get_entity(name, source_id?)` | 人物/概念澄清：实体的相关事件上下文 |

**原则**：全库连接先用 `list_sources` 确认范围。回答引用 `search` 返回的 `[n]` 编号；不确定时先 `outline`/`grep` 缩小范围再读，
避免整篇 `read` 浪费上下文。详见 references/。

## References

- [references/mcp-tools.md](references/mcp-tools.md) — 各工具参数与返回形态
- [references/search-strategies.md](references/search-strategies.md) — 查询策略与漏斗范式
