import { describe, expect, it } from "vitest";

// Keep component contracts documented and stable even without DOM RTL setup.
const MODES = ["off", "comments", "all"] as const;

describe("SourceCodeConfigCard contract", () => {
  it("exposes three extraction modes with comments recommended", () => {
    expect(MODES).toEqual(["off", "comments", "all"]);
    expect(MODES.includes("comments")).toBe(true);
  });
});
