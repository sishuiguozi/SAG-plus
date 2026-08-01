# 工具调用思考与选择策略实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在模型配置中增加四档工具调用策略，并在 LiteLLM 请求边界准确控制强制工具选择与思考开关。

**架构：** Agent 路由器继续只决定 `tool_choice`，不感知供应商或设置页模式。`Settings` 和系统设置 API 保存四值策略，`litellm_policy.py` 在单次请求副本上识别强制工具轮并改写 `tool_choice` 或注入现有供应商适配的关闭思考参数；前端表单负责显示、保存和重置四档配置。

**技术栈：** Python 3.11、Pydantic Settings、FastAPI、LiteLLM、pytest、React 19、TypeScript、Next.js、next-intl。

**执行约束：** 用户已指定在当前 `main` 分支内联执行并直接推送，不创建 worktree，不使用子代理。每个任务完成后单独提交，保留所有已有未提交改动；执行前若工作区出现计划外改动，先核对来源。

**执行状态：** 已完成。后端相关回归 101 个通过；前端策略单测、typecheck、ESLint、i18n 通过；Console Go 的 `search_context`、`get_time` 流式指定工具及全程关闭普通回答均完成真实验证。

---

## 文件结构

- 修改 `apps/api/sag_api/enums.py`：定义无副作用的跨层 `ToolChoiceStrategy` 类型。
- 修改 `apps/api/sag_api/core/config.py`：增加工具调用策略运行时默认值。
- 修改 `apps/api/sag_api/schemas/system.py`：让模型配置更新 schema 校验四档值。
- 修改 `apps/api/sag_api/services/settings_service.py`：将设置加入持久化白名单、有效配置输出和推荐值。
- 修改 `apps/api/sag_api/core/litellm_policy.py`：识别强制工具轮并应用四档请求策略。
- 修改 `apps/api/tests/test_litellm_policy.py`：覆盖四档行为、供应商参数、请求隔离和流式标志。
- 修改 `apps/api/tests/test_settings_system_config.py`：覆盖设置 API 保存、读取、即时生效与非法值拒绝。
- 创建 `apps/web/lib/tool-choice-strategy.ts`：定义四值常量、联合类型和运行时守卫。
- 创建 `apps/web/lib/tool-choice-strategy.test.ts`：验证四个合法值及非法值拒绝。
- 修改 `apps/web/lib/types.ts`：复用联合类型并接入配置/补丁类型。
- 修改 `apps/web/components/features/model-config-form.tsx`：增加状态、加载、提交、重置和四档选择器。
- 修改 `apps/web/messages/zh-CN.json`：增加中文字段名、选项和说明。
- 修改 `apps/web/messages/en-US.json`：增加对应英文文案。
- 修改 `README-CN.md`、`README.md`：记录桌面设置入口和四档行为。
- 修改 `docs/ARCHITECTURE_PATCHES.md`：补充 LiteLLM 请求策略补丁职责。
- 修改 `docs/SAG_OPTIMIZATION_2026.md`：记录工具调用思考策略已经落地。

### 任务 1：配置类型与持久化链路

**文件：**
- 修改：`apps/api/tests/test_settings_system_config.py`
- 修改：`apps/api/sag_api/enums.py`
- 修改：`apps/api/sag_api/core/config.py`
- 修改：`apps/api/sag_api/schemas/system.py`
- 修改：`apps/api/sag_api/services/settings_service.py`

- [ ] **步骤 1：编写设置 API 的失败测试**

先增加不受本机 `.env` 影响的类型测试，固定默认值和四个合法枚举：

```python
from pydantic import ValidationError

from sag_api.core.config import Settings
from sag_api.schemas.system import ModelConfigUpdate

def test_tool_choice_strategy_default_and_schema_values() -> None:
    assert Settings(_env_file=None).llm_tool_choice_strategy == "forced_no_thinking"
    for value in (
        "forced_no_thinking",
        "forced_with_thinking",
        "auto",
        "all_no_thinking",
    ):
        assert ModelConfigUpdate(llm_tool_choice_strategy=value).llm_tool_choice_strategy == value
    with pytest.raises(ValidationError):
        ModelConfigUpdate(llm_tool_choice_strategy="sometimes")
```

再在 `_TOUCHED` 中加入 `llm_tool_choice_strategy`，在现有 `patch` 中保存非默认值，并在非法值循环中加入错误输入：

```python
_TOUCHED = (
    "llm_tool_choice_strategy",
    # existing fields...
)

patch = {
    "llm_tool_choice_strategy": "all_no_thinking",
    # existing fields...
}

for invalid in (
    {"llm_tool_choice_strategy": "sometimes"},
    # existing invalid patches...
):
    response = await c.put("/api/v1/system/model-config", headers=A, json=invalid)
    assert response.status_code == 422
```

现有测试会继续验证 PUT 回显、`settings` 单例即时更新和后续 GET 持久化读取。

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
cd E:\SAG-plus\apps\api
.\.venv\Scripts\python.exe -m pytest tests\test_settings_system_config.py -q
```

预期：FAIL；合法字段尚未进入 schema/白名单，或响应配置缺少 `llm_tool_choice_strategy`。

- [ ] **步骤 3：添加后端配置类型和默认值**

在无副作用的 `enums.py` 定义共享类型：

```python
ToolChoiceStrategy = Literal[
    "forced_no_thinking",
    "forced_with_thinking",
    "auto",
    "all_no_thinking",
]
```

在 `config.py` 和 `schemas/system.py` 都从 `sag_api.enums` 导入该类型，避免 schema 反向依赖全局配置实例。随后在 LLM 配置区域增加字段：

```python
from sag_api.enums import SearchStrategy, ToolChoiceStrategy, normalize_search_strategy

class Settings(BaseSettings):
    # existing LLM fields...
    llm_tool_choice_strategy: ToolChoiceStrategy = "forced_no_thinking"
```

在 `schemas/system.py` 接入部分更新 schema：

```python
from sag_api.enums import SearchStrategy, ToolChoiceStrategy

class ModelConfigUpdate(BaseModel):
    # existing LLM fields...
    llm_tool_choice_strategy: ToolChoiceStrategy | None = None
```

字段未出现仍表示保持原值；显式非法字符串由 Pydantic 返回 422。

- [ ] **步骤 4：接入设置服务的读写与推荐值**

将字段加入 `_FIELDS`，并从 `effective_model_config()` 输出当前值：

```python
_FIELDS = frozenset(
    {
        # existing fields...
        "llm_tool_choice_strategy",
    }
)

def effective_model_config() -> dict:
    return {
        # existing LLM values...
        "llm_tool_choice_strategy": _settings.llm_tool_choice_strategy,
    }
```

`recommended_config()` 已从 `_FIELDS` 和 `Settings.model_fields` 自动生成推荐值，不增加第二份默认值。

- [ ] **步骤 5：运行设置测试确认通过**

运行：

```powershell
cd E:\SAG-plus\apps\api
.\.venv\Scripts\python.exe -m pytest tests\test_settings_system_config.py -q
```

预期：该文件全部通过。

- [ ] **步骤 6：提交配置链路**

```powershell
cd E:\SAG-plus
git add apps/api/sag_api/enums.py apps/api/sag_api/core/config.py apps/api/sag_api/schemas/system.py apps/api/sag_api/services/settings_service.py apps/api/tests/test_settings_system_config.py
git commit -m "feat: persist tool reasoning strategy"
```

### 任务 2：LiteLLM 四档请求策略

**文件：**
- 修改：`apps/api/tests/test_litellm_policy.py`
- 修改：`apps/api/sag_api/core/litellm_policy.py`

- [ ] **步骤 1：编写四档策略的失败测试**

在 `test_litellm_policy.py` 增加参数化测试。指定函数对象使用真实请求形状，且每次断言输入字典未被修改：

```python
import pytest

NAMED_SEARCH = {
    "type": "function",
    "function": {"name": "search_context"},
}

@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("tool_choice", [NAMED_SEARCH, "required"])
def test_forced_no_thinking_preserves_forced_choice_and_disables_reasoning(
    stream: bool,
    tool_choice: object,
) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_base_url="https://opencode.ai/zen/go/v1",
        llm_model="deepseek-v4-flash",
        llm_tool_choice_strategy="forced_no_thinking",
    )
    original = {"messages": [], "tool_choice": tool_choice, "stream": stream}
    request = apply_litellm_completion_policy(settings, original)

    assert request["tool_choice"] == tool_choice
    assert request["reasoning_effort"] == "none"
    assert request["extra_body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in original
    assert "extra_body" not in original

@pytest.mark.parametrize("tool_choice", ["none", "auto"])
def test_forced_no_thinking_leaves_non_forced_chat_reasoning_unchanged(tool_choice: str) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_tool_choice_strategy="forced_no_thinking",
    )
    request = apply_litellm_completion_policy(
        settings,
        {"messages": [], "tool_choice": tool_choice},
    )
    assert request["tool_choice"] == tool_choice
    assert "reasoning_effort" not in request

def test_forced_with_thinking_preserves_named_choice_and_reasoning() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_tool_choice_strategy="forced_with_thinking",
    )
    request = apply_litellm_completion_policy(
        settings,
        {"messages": [], "tool_choice": NAMED_SEARCH},
    )
    assert request["tool_choice"] == NAMED_SEARCH
    assert "reasoning_effort" not in request

@pytest.mark.parametrize("tool_choice", [NAMED_SEARCH, "required"])
def test_auto_strategy_rewrites_only_forced_choices(tool_choice: object) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_tool_choice_strategy="auto",
    )
    request = apply_litellm_completion_policy(
        settings,
        {"messages": [], "tool_choice": tool_choice},
    )
    assert request["tool_choice"] == "auto"
    assert "reasoning_effort" not in request

@pytest.mark.parametrize("tool_choice", [NAMED_SEARCH, "required", "auto", "none"])
def test_all_no_thinking_disables_every_chat_request(tool_choice: object) -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_model="qwen3.6-flash",
        llm_tool_choice_strategy="all_no_thinking",
    )
    request = apply_litellm_completion_policy(
        settings,
        {"messages": [], "tool_choice": tool_choice},
    )
    assert request["tool_choice"] == tool_choice
    assert request["reasoning_effort"] == "none"
    assert request["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
```

再增加一个作用域优先级测试，证明 `auto` 只改工具选择，不会削弱抽取关闭思考：

```python
def test_extract_scope_still_disables_reasoning_under_auto_strategy() -> None:
    from sag_api.core.llm_call_context import llm_call_scope

    settings = Settings(
        _env_file=None,
        llm_api_key="test-key",
        llm_model="qwen3.6-flash",
        llm_tool_choice_strategy="auto",
    )
    with llm_call_scope("extract"):
        request = apply_litellm_completion_policy(
            settings,
            {"messages": [], "tool_choice": "required"},
        )
    assert request["tool_choice"] == "auto"
    assert request["reasoning_effort"] == "none"
```

- [ ] **步骤 2：运行策略测试确认失败**

运行：

```powershell
cd E:\SAG-plus\apps\api
.\.venv\Scripts\python.exe -m pytest tests\test_litellm_policy.py -q
```

预期：新增测试 FAIL；当前策略既不会识别强制工具轮，也不会读取四档设置。

- [ ] **步骤 3：实现强制工具轮识别**

在 `litellm_policy.py` 增加只接受标准 LiteLLM/OpenAI 形状的纯函数：

```python
def _is_forced_tool_choice(value: object) -> bool:
    if value == "required":
        return True
    if not isinstance(value, Mapping) or value.get("type") != "function":
        return False
    function = value.get("function")
    return (
        isinstance(function, Mapping)
        and isinstance(function.get("name"), str)
        and bool(function["name"].strip())
    )
```

`none`、`auto`、空值和畸形字典都返回 `False`，避免把普通回答误判为强制工具轮。

- [ ] **步骤 4：实现四档请求改写与作用域优先级**

在 `apply_litellm_completion_policy()` 中先根据原始选择判断强制轮，再决定工具改写和关闭思考：

```python
tool_choice = normalized.get("tool_choice")
forced_tool_choice = _is_forced_tool_choice(tool_choice)
strategy = settings.llm_tool_choice_strategy

if strategy == "auto" and forced_tool_choice:
    normalized["tool_choice"] = "auto"

disable_reasoning = (
    current_llm_call_scenario() is not None
    or strategy == "all_no_thinking"
    or (strategy == "forced_no_thinking" and forced_tool_choice)
)
if disable_reasoning:
    _apply_scoped_reasoning_disable(normalized, model, settings)
elif "reasoning_effort" not in normalized and thinking is False:
    normalized["reasoning_effort"] = "none"
```

保留现有 `_apply_scoped_reasoning_disable()`、`_merge_extra_body()` 和 `allowed_openai_params` 逻辑，从而继续支持 OpenCode/DeepSeek、Qwen/vLLM/SGLang 和普通 OpenAI 兼容接口。

- [ ] **步骤 5：运行策略测试与既有策略回归**

运行：

```powershell
cd E:\SAG-plus\apps\api
.\.venv\Scripts\python.exe -m pytest tests\test_litellm_policy.py tests\test_units.py -q
```

预期：全部 PASS；既有抽取、重排、显式 `extra_body` 和 LiteLLM hook 测试不回归。

- [ ] **步骤 6：提交请求策略**

```powershell
cd E:\SAG-plus
git add apps/api/sag_api/core/litellm_policy.py apps/api/tests/test_litellm_policy.py
git commit -m "fix: control reasoning around tool calls"
```

### 任务 3：模型配置页四档选择器

**文件：**
- 创建：`apps/web/lib/tool-choice-strategy.ts`
- 创建：`apps/web/lib/tool-choice-strategy.test.ts`
- 修改：`apps/web/lib/types.ts`
- 修改：`apps/web/components/features/model-config-form.tsx`
- 修改：`apps/web/messages/zh-CN.json`
- 修改：`apps/web/messages/en-US.json`

- [ ] **步骤 1：编写前端四值守卫的失败测试**

创建 `tool-choice-strategy.test.ts`：

```typescript
import { describe, expect, it } from "vitest";

import {
  LLM_TOOL_CHOICE_STRATEGIES,
  isLlmToolChoiceStrategy,
} from "./tool-choice-strategy";

describe("tool choice strategies", () => {
  it("contains the four configured modes in UI order", () => {
    expect(LLM_TOOL_CHOICE_STRATEGIES).toEqual([
      "forced_no_thinking",
      "forced_with_thinking",
      "auto",
      "all_no_thinking",
    ]);
  });

  it("accepts only supported persisted values", () => {
    for (const value of LLM_TOOL_CHOICE_STRATEGIES) {
      expect(isLlmToolChoiceStrategy(value)).toBe(true);
    }
    expect(isLlmToolChoiceStrategy("sometimes")).toBe(false);
    expect(isLlmToolChoiceStrategy(null)).toBe(false);
  });
});
```

运行：

```powershell
cd E:\SAG-plus\apps\web
npm run test:unit -- lib/tool-choice-strategy.test.ts
```

预期：FAIL，模块 `./tool-choice-strategy` 尚不存在。

- [ ] **步骤 2：实现联合类型、常量与运行时守卫**

创建 `tool-choice-strategy.ts`：

```typescript
export const LLM_TOOL_CHOICE_STRATEGIES = [
  "forced_no_thinking",
  "forced_with_thinking",
  "auto",
  "all_no_thinking",
] as const;

export type LlmToolChoiceStrategy = (typeof LLM_TOOL_CHOICE_STRATEGIES)[number];

export function isLlmToolChoiceStrategy(value: unknown): value is LlmToolChoiceStrategy {
  return LLM_TOOL_CHOICE_STRATEGIES.some((strategy) => strategy === value);
}
```

在 `types.ts` 导入该类型并接入 API 类型：

```typescript
import type { LlmToolChoiceStrategy } from "@/lib/tool-choice-strategy";

export interface ModelConfig {
  // existing LLM fields...
  llm_tool_choice_strategy: LlmToolChoiceStrategy;
}

export type ModelConfigPatch = Partial<{
  // existing LLM fields...
  llm_tool_choice_strategy: ModelConfig["llm_tool_choice_strategy"];
}>;
```

运行：

```powershell
cd E:\SAG-plus\apps\web
npm run test:unit -- lib/tool-choice-strategy.test.ts
```

预期：`2 passed`。

- [ ] **步骤 3：接入表单状态、加载、提交和默认值重置**

在 `model-config-form.tsx` 导入运行时守卫并增加状态：

```typescript
import { isLlmToolChoiceStrategy } from "@/lib/tool-choice-strategy";

const [toolChoiceStrategy, setToolChoiceStrategy] = React.useState<
  ModelConfig["llm_tool_choice_strategy"]
>("forced_no_thinking");
```

在 `hydrate()` 中读取，在 `currentPatch()` 中提交，在 `resetToDefaults()` 中校验四值后重置：

```typescript
setToolChoiceStrategy(config.llm_tool_choice_strategy);

const patch: ModelConfigPatch = {
  // existing values...
  llm_tool_choice_strategy: toolChoiceStrategy,
};

const strategy = rec.llm_tool_choice_strategy;
if (isLlmToolChoiceStrategy(strategy)) {
  setToolChoiceStrategy(strategy);
}
```

- [ ] **步骤 4：增加四档选择器**

在生成参数网格中增加字段，推荐值徽标来自后端 `recommended`：

```tsx
<Field className="sm:col-span-2">
  <FieldLabel htmlFor="llm-tool-choice-strategy">
    {t("toolChoiceStrategy")}
    <RecommendedBadge t={translate} value={recommended.llm_tool_choice_strategy} />
  </FieldLabel>
  <Select
    value={toolChoiceStrategy}
    onValueChange={(value) =>
      setToolChoiceStrategy(value as ModelConfig["llm_tool_choice_strategy"])
    }
  >
    <SelectTrigger id="llm-tool-choice-strategy"><SelectValue /></SelectTrigger>
    <SelectContent>
      <SelectItem value="forced_no_thinking">{t("toolStrategyForcedNoThinking")}</SelectItem>
      <SelectItem value="forced_with_thinking">{t("toolStrategyForcedWithThinking")}</SelectItem>
      <SelectItem value="auto">{t("toolStrategyAuto")}</SelectItem>
      <SelectItem value="all_no_thinking">{t("toolStrategyAllNoThinking")}</SelectItem>
    </SelectContent>
  </Select>
  <FieldDescription>{t(`toolStrategyDescription.${toolChoiceStrategy}`)}</FieldDescription>
</Field>
```

- [ ] **步骤 5：补齐中英文文案**

在两个 `ModelConfig` 对象中添加完全相同的键结构。中文：

```json
"toolChoiceStrategy": "工具调用策略",
"toolStrategyForcedNoThinking": "工具轮关闭思考（推荐）",
"toolStrategyForcedWithThinking": "全程保留思考",
"toolStrategyAuto": "自动工具选择",
"toolStrategyAllNoThinking": "全程关闭思考",
"toolStrategyDescription": {
  "forced_no_thinking": "普通回答保留思考；指定工具轮关闭思考；工具后的回答恢复思考。",
  "forced_with_thinking": "普通回答、工具调用和工具后的回答都保留模型原有思考设置。",
  "auto": "将指定工具和 required 改为 auto，由模型决定是否调用工具，不改变思考设置。",
  "all_no_thinking": "普通回答、工具调用和工具后的回答全部关闭思考。"
}
```

英文：

```json
"toolChoiceStrategy": "Tool calling strategy",
"toolStrategyForcedNoThinking": "Disable reasoning for forced tools (recommended)",
"toolStrategyForcedWithThinking": "Keep reasoning throughout",
"toolStrategyAuto": "Automatic tool choice",
"toolStrategyAllNoThinking": "Disable reasoning throughout",
"toolStrategyDescription": {
  "forced_no_thinking": "Keep reasoning for normal answers, disable it for forced tool selection, then restore it for the answer after the tool.",
  "forced_with_thinking": "Preserve the model's reasoning settings for normal answers, tool calls, and post-tool answers.",
  "auto": "Convert named and required tool choices to auto so the model decides whether to call a tool; reasoning settings stay unchanged.",
  "all_no_thinking": "Disable reasoning for normal answers, tool calls, and post-tool answers."
}
```

- [ ] **步骤 6：运行前端验证**

运行：

```powershell
cd E:\SAG-plus\apps\web
npm run test:unit -- lib/tool-choice-strategy.test.ts
npm run typecheck
npm run lint
npm run i18n:check
```

预期：三条命令全部退出码 0；i18n 输出中英文键一致。

- [ ] **步骤 7：提交设置页**

```powershell
cd E:\SAG-plus
git add apps/web/lib/tool-choice-strategy.ts apps/web/lib/tool-choice-strategy.test.ts apps/web/lib/types.ts apps/web/components/features/model-config-form.tsx apps/web/messages/zh-CN.json apps/web/messages/en-US.json
git commit -m "feat: add tool reasoning controls"
```

### 任务 4：说明文档与完整验证

**文件：**
- 修改：`README-CN.md`
- 修改：`README.md`
- 修改：`docs/ARCHITECTURE_PATCHES.md`
- 修改：`docs/SAG_OPTIMIZATION_2026.md`

- [ ] **步骤 1：更新用户入口说明**

在 README-CN 的桌面使用步骤中增加：

```markdown
7. 在 **设置 → 模型配置 → 生成参数 → 工具调用策略** 选择思考行为。默认“工具轮关闭思考”会保留普通回答的思考，只在指定工具轮关闭，并在工具后的回答恢复；也可选择全程保留、自动工具或全程关闭。
```

在 README 对应位置增加语义一致的英文说明：

```markdown
7. Under **Settings → Model settings → Generation parameters → Tool calling strategy**, choose how reasoning behaves around tools. The recommended mode keeps reasoning for normal answers, disables it only for forced tool selection, and restores it for the post-tool answer; keep-reasoning, automatic-tool, and disable-all modes are also available.
```

- [ ] **步骤 2：更新架构与优化记录**

在 `docs/ARCHITECTURE_PATCHES.md` 的 `litellm_policy.py` 条目说明其同时负责抽取/重排作用域和聊天四档工具策略；在 `docs/SAG_OPTIMIZATION_2026.md` 的模型调用优化说明中记录四档设置、默认行为以及 Console Go 实测兼容结果。

- [ ] **步骤 3：运行后端相关回归**

运行：

```powershell
cd E:\SAG-plus\apps\api
.\.venv\Scripts\python.exe -m pytest tests\test_litellm_policy.py tests\test_settings_system_config.py tests\test_settings.py tests\test_agentic.py tests\test_agent_runtime.py tests\test_ask_stream.py tests\test_units.py -q
.\.venv\Scripts\python.exe -m ruff check sag_api\core\config.py sag_api\core\litellm_policy.py sag_api\schemas\system.py sag_api\services\settings_service.py tests\test_litellm_policy.py tests\test_settings_system_config.py
```

预期：pytest 全部通过，Ruff 输出 `All checks passed!`。

- [ ] **步骤 4：运行前端完整静态检查**

运行：

```powershell
cd E:\SAG-plus\apps\web
npm run typecheck
npm run lint
npm run i18n:check
```

预期：三条命令全部退出码 0。

- [ ] **步骤 5：验证真实 Console Go 请求矩阵**

使用当前本地数据库中的模型配置执行只输出状态、不输出密钥的验证脚本，至少覆盖：

```text
forced_no_thinking + named search_context -> 成功返回 search_context 工具调用
forced_no_thinking + named get_time       -> 成功返回 get_time 工具调用
forced_with_thinking + named tool         -> 保留上游原始行为，不自动改写或重试
auto + named tool                         -> 实际发出的 tool_choice 为 auto
all_no_thinking + tool_choice none        -> 普通回答成功且请求关闭思考
```

脚本必须从 `Settings` 读取连接信息，不得打印 API Key、Authorization header 或完整请求字典。若上游当时不可用，只将该项记录为外部验证受阻，不能用它否定离线单测结果。

- [ ] **步骤 6：提交文档并推送 main**

```powershell
cd E:\SAG-plus
git add README.md README-CN.md docs/ARCHITECTURE_PATCHES.md docs/SAG_OPTIMIZATION_2026.md
git commit -m "docs: explain tool reasoning strategies"
git status --short
git push origin main
```

预期：工作区干净，`main` 成功推送到 `https://github.com/sishuiguozi/SAG-plus.git`。
