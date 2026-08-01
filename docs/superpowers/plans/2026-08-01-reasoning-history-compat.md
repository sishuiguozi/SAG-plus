# 可配置推理历史兼容实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在模型配置中提供 `auto / always / off` 三档推理历史兼容，并修复 DeepSeek 工具调用后的恢复思考请求。

**架构：** 系统配置层持久化 `llm_reasoning_history_compat`；LiteLLM 请求策略在当前请求保留思考且模式允许时，复制消息列表并为缺少字段的 assistant 历史消息补 `reasoning_content=""`。前端在工具调用策略旁展示三档设置，默认自动识别 DeepSeek 路由，手动始终启用可覆盖模型别名。

**技术栈：** Python 3.11、Pydantic、LiteLLM、pytest、Next.js、React、TypeScript、Vitest、next-intl。

---

## 文件结构

- 修改 `apps/api/sag_api/enums.py`：定义三值 `ReasoningHistoryCompat` 类型。
- 修改 `apps/api/sag_api/core/config.py`：增加运行时默认配置。
- 修改 `apps/api/sag_api/schemas/system.py`：校验设置 API 输入。
- 修改 `apps/api/sag_api/services/settings_service.py`：持久化、读取并输出推荐值。
- 修改 `apps/api/tests/test_settings_system_config.py`：覆盖默认值、合法值、非法值和 API 往返。
- 修改 `apps/api/sag_api/core/litellm_policy.py`：判定启用条件并以不可变方式补齐历史消息。
- 修改 `apps/api/tests/test_litellm_policy.py`：覆盖自动、始终、关闭、无思考和不可变性。
- 修改 `apps/web/lib/tool-choice-strategy.ts`：定义前端三值常量、类型和守卫。
- 修改 `apps/web/lib/tool-choice-strategy.test.ts`：验证三值及持久化值守卫。
- 修改 `apps/web/lib/types.ts`：扩展模型配置和保存补丁类型。
- 修改 `apps/web/components/features/model-config-form.tsx`：加载、保存、重置并展示三档选择。
- 修改 `apps/web/messages/zh-CN.json`、`apps/web/messages/en-US.json`：增加双语标签、选项和说明。
- 修改 `docs/SAG_OPTIMIZATION_2026.md`：记录根因、设置入口和验证结果。
- 修改 `docs/superpowers/specs/2026-08-01-console-go-tool-choice-compat-design.md`：实现后更新状态。

### 任务 1：配置与设置 API

**文件：**
- 修改：`apps/api/tests/test_settings_system_config.py`
- 修改：`apps/api/sag_api/enums.py`
- 修改：`apps/api/sag_api/core/config.py`
- 修改：`apps/api/sag_api/schemas/system.py`
- 修改：`apps/api/sag_api/services/settings_service.py`

- [ ] **步骤 1：编写失败的设置测试**

在 `_TOUCHED` 加入 `llm_reasoning_history_compat`，并加入以下测试；现有 API 往返测试的 `patch` 使用 `"always"`，非法值循环加入 `{"llm_reasoning_history_compat": "sometimes"}`：

```python
def test_reasoning_history_compat_default_and_schema_values() -> None:
    assert Settings(_env_file=None).llm_reasoning_history_compat == "auto"
    for value in ("auto", "always", "off"):
        patch = ModelConfigUpdate(llm_reasoning_history_compat=value)
        assert patch.llm_reasoning_history_compat == value
    with pytest.raises(ValidationError):
        ModelConfigUpdate(llm_reasoning_history_compat="sometimes")
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
$env:SAG_DOCUMENT_EXTRACT_CONCURRENCY='5'
$env:SAG_LLM_TIMEOUT_MS='60000'
$env:SAG_LLM_MODEL='qwen3.6-flash'
uv run pytest tests/test_settings_system_config.py -q
```

工作目录：`apps/api`。预期：FAIL，配置或 schema 尚无 `llm_reasoning_history_compat`。

- [ ] **步骤 3：实现三值配置及持久化**

在 `enums.py` 定义：

```python
ReasoningHistoryCompat = Literal["auto", "always", "off"]
```

在 `Settings` 增加：

```python
llm_reasoning_history_compat: ReasoningHistoryCompat = "auto"
```

在 `ModelConfigUpdate` 增加：

```python
llm_reasoning_history_compat: ReasoningHistoryCompat | None = None
```

在 `settings_service.py` 的 `_FIELDS` 加入字段，并在 `effective_model_config()` 返回：

```python
"llm_reasoning_history_compat": _settings.llm_reasoning_history_compat,
```

`recommended_config()` 会从 `Settings.model_fields` 自动返回推荐值 `auto`。

- [ ] **步骤 4：运行设置测试确认通过**

运行任务 1 步骤 2 的命令。预期：该文件全部 PASS。

- [ ] **步骤 5：提交配置层**

```powershell
git add apps/api/sag_api/enums.py apps/api/sag_api/core/config.py apps/api/sag_api/schemas/system.py apps/api/sag_api/services/settings_service.py apps/api/tests/test_settings_system_config.py
git commit -m "feat: configure reasoning history compatibility"
```

### 任务 2：LiteLLM 历史消息补齐

**文件：**
- 修改：`apps/api/tests/test_litellm_policy.py`
- 修改：`apps/api/sag_api/core/litellm_policy.py`

- [ ] **步骤 1：编写失败的策略测试**

使用包含 assistant tool call、tool 结果和已有推理内容的消息，并覆盖五个边界：

```python
HISTORY = [
    {"role": "user", "content": "AFSIM 是什么"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "search_context", "arguments": "{}"}}],
    },
    {"role": "tool", "tool_call_id": "call-1", "content": "AFSIM 是仿真框架"},
    {"role": "assistant", "content": "继续检索", "reasoning_content": "已有推理"},
]

def test_deepseek_auto_fills_missing_assistant_reasoning_without_mutation() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_base_url="https://opencode.ai/zen/go/v1",
        llm_model="deepseek-v4-flash",
        llm_reasoning_history_compat="auto",
    )
    original_messages = [dict(message) for message in HISTORY]
    request = apply_litellm_completion_policy(settings, {"messages": original_messages, "tool_choice": "auto"})
    assert request["messages"][1]["reasoning_content"] == ""
    assert request["messages"][3]["reasoning_content"] == "已有推理"
    assert request["messages"] is not original_messages
    assert request["messages"][1] is not original_messages[1]
    assert "reasoning_content" not in original_messages[1]

@pytest.mark.parametrize(
    ("mode", "model", "expected"),
    [
        ("auto", "gpt-5-mini", False),
        ("always", "gpt-5-mini", True),
        ("off", "deepseek-v4-flash", False),
    ],
)
def test_reasoning_history_compat_modes(mode: str, model: str, expected: bool) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_model=model,
        llm_reasoning_history_compat=mode,
    )
    request = apply_litellm_completion_policy(settings, {"messages": HISTORY, "tool_choice": "auto"})
    assert ("reasoning_content" in request["messages"][1]) is expected

def test_disabled_reasoning_does_not_fill_history() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_model="deepseek-v4-flash",
        llm_tool_choice_strategy="all_no_thinking",
        llm_reasoning_history_compat="always",
    )
    request = apply_litellm_completion_policy(settings, {"messages": HISTORY, "tool_choice": "auto"})
    assert "reasoning_content" not in request["messages"][1]

def test_explicitly_disabled_reasoning_does_not_fill_history() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_model="deepseek-v4-flash",
        llm_reasoning_history_compat="always",
    )
    request = apply_litellm_completion_policy(
        settings,
        {"messages": HISTORY, "tool_choice": "auto", "reasoning_effort": "none"},
    )
    assert "reasoning_content" not in request["messages"][1]
```

- [ ] **步骤 2：运行策略测试确认失败**

运行：

```powershell
uv run pytest tests/test_litellm_policy.py -q
```

工作目录：`apps/api`。预期：新增断言 FAIL，策略尚未补齐 `reasoning_content`。

- [ ] **步骤 3：实现最小不可变补齐逻辑**

在 `litellm_policy.py` 增加：

```python
def _reasoning_history_compat_enabled(model: str, settings: Settings) -> bool:
    mode = settings.llm_reasoning_history_compat
    return mode == "always" or (mode == "auto" and "deepseek" in _routing_text(model, settings))


def _reasoning_is_disabled(request: Mapping[str, Any]) -> bool:
    if request.get("reasoning_effort") == "none":
        return True
    extra_body = request.get("extra_body")
    if not isinstance(extra_body, Mapping):
        return False
    thinking = extra_body.get("thinking")
    return (
        isinstance(thinking, Mapping) and thinking.get("type") == "disabled"
    ) or _thinking_override(extra_body) is False


def _with_reasoning_history_compat(request: dict[str, Any]) -> None:
    messages = request.get("messages")
    if not isinstance(messages, (list, tuple)):
        return
    normalized_messages: list[Any] | None = None
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping) or message.get("role") != "assistant" or "reasoning_content" in message:
            continue
        if normalized_messages is None:
            normalized_messages = list(messages)
        normalized_messages[index] = {**message, "reasoning_content": ""}
    if normalized_messages is not None:
        request["messages"] = normalized_messages
```

在 `apply_litellm_completion_policy()` 完成思考开关处理后、OpenAI 参数白名单处理前调用：

```python
if not _reasoning_is_disabled(normalized) and _reasoning_history_compat_enabled(model, settings):
    _with_reasoning_history_compat(normalized)
```

- [ ] **步骤 4：运行策略测试确认通过**

运行任务 2 步骤 2 的命令。预期：全部 PASS。

- [ ] **步骤 5：提交策略层**

```powershell
git add apps/api/sag_api/core/litellm_policy.py apps/api/tests/test_litellm_policy.py
git commit -m "fix: preserve DeepSeek reasoning history"
```

### 任务 3：设置页三档选择

**文件：**
- 修改：`apps/web/lib/tool-choice-strategy.test.ts`
- 修改：`apps/web/lib/tool-choice-strategy.ts`
- 修改：`apps/web/lib/types.ts`
- 修改：`apps/web/components/features/model-config-form.tsx`
- 修改：`apps/web/messages/zh-CN.json`
- 修改：`apps/web/messages/en-US.json`

- [ ] **步骤 1：编写失败的前端值守卫测试**

在 `tool-choice-strategy.test.ts` 加入：

```typescript
import {
  LLM_REASONING_HISTORY_COMPAT_MODES,
  isLlmReasoningHistoryCompat,
} from "./tool-choice-strategy";

it("accepts the three reasoning history compatibility modes", () => {
  expect(LLM_REASONING_HISTORY_COMPAT_MODES).toEqual(["auto", "always", "off"]);
  for (const value of LLM_REASONING_HISTORY_COMPAT_MODES) {
    expect(isLlmReasoningHistoryCompat(value)).toBe(true);
  }
  expect(isLlmReasoningHistoryCompat("sometimes")).toBe(false);
});
```

- [ ] **步骤 2：运行前端单元测试确认失败**

运行：

```powershell
npm test -- --run lib/tool-choice-strategy.test.ts
```

工作目录：`apps/web`。预期：FAIL，常量和守卫尚未导出。

- [ ] **步骤 3：增加三值类型、表单状态和选择器**

在 `tool-choice-strategy.ts` 增加：

```typescript
export const LLM_REASONING_HISTORY_COMPAT_MODES = ["auto", "always", "off"] as const;
export type LlmReasoningHistoryCompat = (typeof LLM_REASONING_HISTORY_COMPAT_MODES)[number];
export function isLlmReasoningHistoryCompat(value: unknown): value is LlmReasoningHistoryCompat {
  return LLM_REASONING_HISTORY_COMPAT_MODES.some((mode) => mode === value);
}
```

在 `ModelConfig` 和 `ModelConfigPatch` 增加 `llm_reasoning_history_compat`。在 `model-config-form.tsx` 增加默认 `auto` 的 state，在加载、保存和推荐值重置路径同步该字段，并紧接工具调用策略增加三项 Select：`auto`、`always`、`off`。字段说明使用当前值读取 `reasoningHistoryCompatDescription.<mode>`。

双语文案键固定为：

```json
"reasoningHistoryCompat": "推理历史兼容",
"reasoningHistoryCompatAuto": "自动（推荐）",
"reasoningHistoryCompatAlways": "始终启用",
"reasoningHistoryCompatOff": "关闭",
"reasoningHistoryCompatDescription": {
  "auto": "仅在识别为 DeepSeek 路由时补齐工具调用历史中的推理字段。",
  "always": "对所有模型补齐推理历史字段，适用于使用模型别名的兼容接口。",
  "off": "不修改历史消息；接口不接受 reasoning_content 时使用。"
}
```

英文文案表达相同语义，并保留 `reasoning_content` 原字段名。

- [ ] **步骤 4：运行前端验证**

```powershell
npm test -- --run lib/tool-choice-strategy.test.ts
npm run typecheck
npm run lint
npm run i18n:check
```

工作目录：`apps/web`。预期：四条命令全部成功。

- [ ] **步骤 5：提交设置页**

```powershell
git add apps/web/lib/tool-choice-strategy.ts apps/web/lib/tool-choice-strategy.test.ts apps/web/lib/types.ts apps/web/components/features/model-config-form.tsx apps/web/messages/zh-CN.json apps/web/messages/en-US.json
git commit -m "feat: expose reasoning history compatibility"
```

### 任务 4：回归、真实接口验证与文档收尾

**文件：**
- 修改：`docs/SAG_OPTIMIZATION_2026.md`
- 修改：`docs/superpowers/specs/2026-08-01-console-go-tool-choice-compat-design.md`

- [ ] **步骤 1：运行后端相关回归**

```powershell
$env:SAG_DOCUMENT_EXTRACT_CONCURRENCY='5'
$env:SAG_LLM_TIMEOUT_MS='60000'
$env:SAG_LLM_MODEL='qwen3.6-flash'
uv run pytest tests/test_litellm_policy.py tests/test_settings_system_config.py tests/test_settings_service.py tests/test_sag_generation_policy.py -q
uv run ruff check --ignore E501 sag_api/core/litellm_policy.py sag_api/core/config.py sag_api/enums.py sag_api/schemas/system.py sag_api/services/settings_service.py tests/test_litellm_policy.py tests/test_settings_system_config.py
```

工作目录：`apps/api`。预期：pytest 与 Ruff 均成功。

- [ ] **步骤 2：真实验证 Console Go 工具后恢复思考**

使用当前本地数据库中已保存的模型配置构造两轮请求，不打印 API key：第一轮指定 `search_context` 并关闭思考；把 assistant tool-call 与最小工具结果作为历史传给第二轮，第二轮使用 `tool_choice="auto"` 恢复思考。验证策略生成的第二轮历史含空 `reasoning_content`，Console Go 返回内容或后续工具调用，不再返回 `upstream request failed`。

- [ ] **步骤 3：更新文档状态**

在优化文档 D5 记录三档后台设置和 Console Go 双轮验证。在设计规格中把状态更新为“已实现并验证”，并保留默认 `auto`、别名用 `always`、不兼容接口用 `off` 的说明。

- [ ] **步骤 4：执行最终差异检查**

```powershell
git diff --check
git status --short
git diff --stat
```

预期：无空白错误，只有计划内文件。

- [ ] **步骤 5：提交文档并推送**

```powershell
git add docs/SAG_OPTIMIZATION_2026.md docs/superpowers/specs/2026-08-01-console-go-tool-choice-compat-design.md
git commit -m "docs: document reasoning history compatibility"
git push origin main
```
