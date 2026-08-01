# 工具参考

所有工具返回 MCP text content。空态返回中文占位（如「（无相关资料）」「（未找到该文档）」），
不会抛错——判断文案前缀即可分支。

## list_sources()
列出当前可访问的信源：`- 名称（source_id=…） · N 文档 · M 分块`。全库连接应先调用它确定范围。

## search(query: str, top_k: int = 8, source_id: str = "")
语义检索。返回带编号证据块：`[n] 标题（chunk_id=…）\n内容`。
需要服务端已配置 LLM（离线/未配时返回结构化错误说明）。

## list_documents(source_id: str = "")
`- 文件名 · id=<document_id> · <状态> · N 分块`（状态 ready 才可检索）。

## outline(document_id: str)
按 rank 排序的分块大纲：`rank. heading（chunk_id=…）`。文档处理中会提示占位。

## grep(pattern: str, limit: int = 20, source_id: str = "")
LIKE 精确匹配（大小写不敏感，% _ 已转义）。返回 `[n] heading（chunk_id）\n±240 字符上下文`。

## get_chunk(chunk_id: str, source_id: str = "")
分块完整原文（heading + 正文）。chunk_id 来自 search/outline/grep。

## read(document_id: str, offset: int = 1, limit: int = 120)
原始文件按行分页（行号 + 内容，limit ≤ 500）。首行给出 `第 a-b 行 / 共 N 行` 便于翻页。

## get_entity(name: str, source_id: str = "")
实体（人物/组织/概念）的相关事件上下文；先精确名匹配、再子串匹配。
