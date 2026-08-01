import { describe, expect, it } from "vitest";

import {
  LLM_REASONING_HISTORY_COMPAT_MODES,
  LLM_TOOL_CHOICE_STRATEGIES,
  isLlmReasoningHistoryCompat,
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

  it("accepts the three reasoning history compatibility modes", () => {
    expect(LLM_REASONING_HISTORY_COMPAT_MODES).toEqual(["auto", "always", "off"]);
    for (const value of LLM_REASONING_HISTORY_COMPAT_MODES) {
      expect(isLlmReasoningHistoryCompat(value)).toBe(true);
    }
    expect(isLlmReasoningHistoryCompat("sometimes")).toBe(false);
  });
});
