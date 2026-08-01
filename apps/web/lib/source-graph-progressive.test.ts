import { describe, expect, it } from "vitest";

import { progressiveSourceGraphBudgets } from "./source-graph-progressive";

describe("progressiveSourceGraphBudgets", () => {
  it("creates bounded first, intermediate, and final 3D graph stages", () => {
    expect(
      progressiveSourceGraphBudgets({
        documentLimit: 240,
        eventLimit: 220,
        entityLimit: 120,
      }),
    ).toEqual([
      { documentLimit: 84, eventLimit: 77, entityLimit: 42 },
      { documentLimit: 168, eventLimit: 154, entityLimit: 84 },
      { documentLimit: 240, eventLimit: 220, entityLimit: 120 },
    ]);
  });

  it("does not stage an already small graph", () => {
    const budget = {
      documentLimit: 40,
      eventLimit: 40,
      entityLimit: 24,
    };
    expect(progressiveSourceGraphBudgets(budget)).toEqual([budget]);
  });

  it("never exceeds a caller's final limits", () => {
    const finalBudget = {
      documentLimit: 100,
      eventLimit: 100,
      entityLimit: 100,
    };
    const stages = progressiveSourceGraphBudgets(finalBudget);
    expect(stages.at(-1)).toEqual(finalBudget);
    expect(
      stages.every(
        (stage) =>
          stage.documentLimit <= finalBudget.documentLimit &&
          stage.eventLimit <= finalBudget.eventLimit &&
          stage.entityLimit <= finalBudget.entityLimit,
      ),
    ).toBe(true);
  });
});
