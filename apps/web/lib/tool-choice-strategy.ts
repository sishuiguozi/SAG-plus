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
