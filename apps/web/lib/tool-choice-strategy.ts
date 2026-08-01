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

export const LLM_REASONING_HISTORY_COMPAT_MODES = ["auto", "always", "off"] as const;

export type LlmReasoningHistoryCompat =
  (typeof LLM_REASONING_HISTORY_COMPAT_MODES)[number];

export function isLlmReasoningHistoryCompat(
  value: unknown,
): value is LlmReasoningHistoryCompat {
  return LLM_REASONING_HISTORY_COMPAT_MODES.some((mode) => mode === value);
}

export const LLM_JSON_SCHEMA_COMPAT_MODES = ["auto", "always", "off"] as const;

export type LlmJsonSchemaCompat = (typeof LLM_JSON_SCHEMA_COMPAT_MODES)[number];

export function isLlmJsonSchemaCompat(value: unknown): value is LlmJsonSchemaCompat {
  return LLM_JSON_SCHEMA_COMPAT_MODES.some((mode) => mode === value);
}

