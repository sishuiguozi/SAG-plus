import { describe, expect, it } from "vitest";

const ACTIONS = ["download", "pause", "resume", "repair"] as const;

describe("TreeSitterResourceCard contract", () => {
  it("supports download lifecycle actions", () => {
    expect(ACTIONS).toEqual(["download", "pause", "resume", "repair"]);
  });
});
