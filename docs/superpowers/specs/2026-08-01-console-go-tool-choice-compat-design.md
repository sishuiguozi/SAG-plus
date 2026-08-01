# 工具调用思考与选择策略设计

日期：2026-08-01
状态：四档策略已实现；DeepSeek 多轮历史兼容修订已确认

## 背景与结论

SAG-plus 当前会根据用户意图选择 `none`、`auto`、`required` 或指定函数形式的 `tool_choice`。实测 Console Go（`https://opencode.ai/zen/go/v1`）在启用思考时可以处理 `none` 和 `auto`，但指定 `search_context`、`get_time` 等函数或使用 `required` 会返回上游请求失败；仅关闭该次工具选择调用的思考后，指定工具和流式调用均可正常工作。

这个问题不应通过写死 Console Go 特例解决。模型配置应提供四种通用策略，让用户分别控制普通回答、强制工具轮和工具后回答轮的思考行为，以及是否把强制工具选择交给模型自动决定。

默认推荐“工具轮关闭思考”：普通回答保留思考，指定工具轮关闭思考，工具执行后的回答轮恢复思考。

## 目标

- 支持四种清晰、可保存的工具调用与思考策略。
- 让 Console Go 稳定执行 `search_context`、`get_time` 等指定工具。
- 支持其他 OpenAI 兼容、OpenCode/DeepSeek 和 Qwen 类接口使用相同设置。
- 保持入库抽取、实体事件抽取与 LLM 重排已有的关闭思考逻辑。
- 同时支持流式与非流式模型调用。

## 非目标

- 不改变工具定义、工具执行器、意图路由或知识库检索逻辑。
- 不通过失败后自动切换策略并重试来掩盖上游错误。
- 不根据模型名或供应商替用户自动改变已保存的模式。
- 不尝试预测一个 `auto` 请求最终是否会产生工具调用。

## 配置设计

新增系统配置 `llm_tool_choice_strategy`，默认值为 `forced_no_thinking`，支持四档：

| 值 | 设置页名称 | 普通回答 | 指定工具或 `required` | 工具后回答 |
| --- | --- | --- | --- | --- |
| `forced_no_thinking` | 工具轮关闭思考（推荐） | 保留思考 | 保留强制选择并关闭思考 | 恢复思考 |
| `forced_with_thinking` | 全程保留思考 | 保留思考 | 保留强制选择和思考 | 保留思考 |
| `auto` | 自动工具选择 | 保留原设置 | 改写为 `auto`，保留原设置 | 保留原设置 |
| `all_no_thinking` | 全程关闭思考 | 关闭思考 | 保留强制选择并关闭思考 | 关闭思考 |

“保留思考”表示不由本策略覆盖模型原有的思考配置；“关闭思考”表示只对当前模型请求注入关闭思考参数。

`none` 和原本已经是 `auto` 的选择不会被改写。第三档“自动工具选择”只修改 `tool_choice`，不额外开启或关闭思考。由于模型在返回结果前才决定一个 `auto` 请求是否调用工具，该模式无法做到“决定调用工具后再关闭同一请求的思考”。

设置页字段名为“工具调用策略”，展示四档说明和推荐标记。配置允许用户随时切换并保存，不根据当前接口限制可选项。

## 强制工具轮定义

以下两种请求属于强制工具轮：

- `tool_choice="required"`；
- `tool_choice={"type":"function","function":{"name":"..."}}`。

`tool_choice="none"` 和 `tool_choice="auto"` 不属于强制工具轮。工具执行后的回答请求通常使用 `auto`，因此在“工具轮关闭思考”模式下会自然恢复模型原有思考设置。

## 供应商参数适配

四档策略是供应商无关的，但关闭思考参数继续复用现有 LiteLLM 策略层按路由适配：

- OpenCode/DeepSeek 风格接口：`reasoning_effort="none"` 与 `extra_body.thinking={"type":"disabled"}`；
- Qwen、vLLM、SGLang 风格接口：`reasoning_effort="none"` 与 `extra_body.chat_template_kwargs.enable_thinking=false`；
- 其他 OpenAI 兼容接口：通过 LiteLLM 发送 `reasoning_effort="none"`。

如果某个未知接口不接受对应参数，用户可以切换“全程保留思考”或“自动工具选择”。策略不进行静默降级或二次请求。

## DeepSeek 多轮思考历史兼容

DeepSeek 思考模式要求历史中的 assistant 消息带回 `reasoning_content`。当第一轮指定工具按
`forced_no_thinking` 关闭思考时，返回的 assistant tool-call 消息没有该字段；工具执行后的回答轮
恢复思考，Console Go 会因此拒绝请求。仅移除当前轮工具 schema 或把 `tool_choice` 设为 `none`
不能解决这个历史消息校验问题。

请求策略层在同时满足以下条件时补齐历史字段：

- 当前路由的模型标识包含 `deepseek`；
- 当前请求没有被任一策略或抽取/重排作用域关闭思考；
- `messages` 中存在 `role="assistant"` 且缺少 `reasoning_content` 的消息。

策略必须创建新的消息列表和 assistant 字典，只为缺失字段写入 `reasoning_content=""`；已有的非空
推理内容原样保留，调用方传入的请求与消息对象不得被修改。非 DeepSeek 模型、当前仍关闭思考的
请求以及非 assistant 消息不做处理。

这个兼容处理不改变四档配置语义：工具轮仍按第一档关闭思考，工具后的回答轮仍恢复思考；多工具
链可以继续运行，不强制 `tool_choice="none"`，也不把工具结果降级为普通文本。

## 请求处理流程

1. Agent 路由器按现有规则产生初始 `tool_choice`。
2. LLM 客户端构造请求参数。
3. LiteLLM 策略层读取 `llm_tool_choice_strategy` 和实际 `tool_choice`。
4. 按四档策略处理当前请求：
   - `forced_no_thinking`：仅强制工具轮关闭思考，选择本身不变；
   - `forced_with_thinking`：工具选择和思考参数均不变；
   - `auto`：强制工具轮改写为 `auto`，思考参数不变；
   - `all_no_thinking`：每个聊天模型请求都关闭思考，工具选择本身不变。
5. 已有入库抽取、实体事件抽取和 LLM 重排作用域继续独立关闭思考，不受四档聊天策略削弱。

所有处理必须是单次请求级的，不得修改全局模型请求对象或泄漏到下一轮。用户显式提供的其他 `extra_body` 字段必须保留，只合并本策略需要的字段。

## 后端改动范围

- 核心配置：新增四值枚举字段与推荐默认值。
- 系统设置 schema：校验并向前端输出新字段。
- 设置服务：读写、推荐值和重置默认值。
- LiteLLM 请求策略：识别强制工具轮并应用四档行为。
- Agent 路由器保持供应商无关，只负责意图和工具选择。

## 前端改动范围

- 模型配置类型增加工具调用策略。
- 模型配置表单增加四档选择、说明和推荐标记。
- 中英文文案同步补齐。
- 未保存配置继续遵循现有设置页保存机制，不改变其他模型配置交互。

## 测试设计

后端单元测试至少覆盖：

- 默认策略为 `forced_no_thinking`。
- `forced_no_thinking` + 指定函数：保留函数名并关闭当前调用的思考。
- `forced_no_thinking` + `required`：保留 `required` 并关闭当前调用的思考。
- `forced_no_thinking` + `auto` 或 `none`：不覆盖思考设置。
- `forced_with_thinking`：四类工具选择均保持原样，不附加策略级关闭思考参数。
- `auto`：指定函数和 `required` 改写为 `auto`，原有 `auto` 与 `none` 不变。
- `all_no_thinking`：指定工具、`required`、`auto` 与 `none` 请求全部关闭思考。
- OpenCode/DeepSeek、Qwen 和普通 OpenAI 兼容路由使用各自参数格式。
- DeepSeek 恢复思考时，为缺失字段的历史 assistant 消息补空 `reasoning_content`。
- 已有 `reasoning_content` 保持不变，原始请求和嵌套消息对象不被修改。
- 非 DeepSeek 路由或关闭思考的请求不增加该历史字段。
- 入库抽取和重排已有关闭思考作用域在任意聊天策略下继续生效。
- 流式与非流式请求使用相同策略。
- 系统设置 API 能保存、读取和重置四档值。

前端验证至少覆盖：

- TypeScript 类型检查。
- ESLint。
- 中英文 i18n 键完整性检查。
- 表单能够显示并提交四档值。

## 验收标准

- 模型配置可选择、保存并恢复四种策略。
- 默认模式下，普通回答保留思考，指定工具轮关闭思考，工具后回答恢复思考。
- 全程保留模式不由本策略关闭任何聊天轮的思考。
- 自动工具模式将指定函数和 `required` 改为 `auto`。
- 全程关闭模式对普通回答、工具轮和工具后回答都关闭思考。
- Console Go 在默认模式下可成功执行指定 `search_context` 和 `get_time`。
- Console Go 在工具结果返回后的第二轮恢复思考并成功继续生成，不再返回 upstream request failed。
- 第二轮仍可继续选择其他工具，不通过强制 `none` 限制多工具链。
- “你好”等无需工具的直接回答行为保持正常。
- 相关后端测试、前端类型检查、ESLint 与 i18n 检查全部通过。

## 风险与回退

- 未知接口可能不接受关闭思考参数；用户可切换为“全程保留思考”或“自动工具选择”。
- 自动工具模式可能让模型放弃本应执行的工具，这是主动选择该模式的预期权衡。
- 全程关闭模式会降低复杂回答的推理深度，但可以减少延迟并兼容不支持思考的接口。
- 空 `reasoning_content` 是 DeepSeek 历史消息协议兼容字段，不代表伪造思考内容；如果上游以后取消
  该要求，可以移除这段补齐逻辑而不影响四档配置。
- 新配置默认使用 `forced_no_thinking`，旧安装无需数据库迁移即可获得推荐行为；回退代码后，数据库中的额外配置键应被旧版本安全忽略。
