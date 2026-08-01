import { describe, expect, it } from "vitest";
import {
  classifyLocalCandidates,
  isBlockedPath,
  normalizeRelativePath,
  preserveRootRelativePath,
  summarizePlan,
} from "./code-folder-import";

describe("code-folder-import helpers", () => {
  it("normalizes and preserves root directory name", () => {
    expect(normalizeRelativePath("\\a\\b\\c.py")).toBe("a/b/c.py");
    expect(preserveRootRelativePath("src/main.py", "my-repo")).toBe("my-repo/src/main.py");
    expect(preserveRootRelativePath("my-repo/src/main.py", "my-repo")).toBe("my-repo/src/main.py");
  });

  it("rejects sensitive/generated/binary paths", () => {
    expect(isBlockedPath("repo/.env")).toBeTruthy();
    expect(isBlockedPath("repo/id_rsa")).toBeTruthy();
    expect(isBlockedPath("repo/node_modules/a.js")).toBeTruthy();
    expect(isBlockedPath("repo/app.min.js")).toBeTruthy();
    expect(isBlockedPath("repo/note.ipynb")).toBeTruthy();
    expect(isBlockedPath("repo/src/main.py")).toBeNull();
  });

  it("classifies default selection for office/pdf", () => {
    const items = classifyLocalCandidates(
      [
        { relativePath: "src/a.py", sizeBytes: 10 },
        { relativePath: "docs/a.pdf", sizeBytes: 20 },
        { relativePath: ".env", sizeBytes: 1 },
      ],
      "repo",
    );
    expect(items.find((i) => i.relativePath.endsWith("a.py"))?.defaultSelected).toBe(true);
    expect(items.find((i) => i.relativePath.endsWith("a.pdf"))?.defaultSelected).toBe(false);
    expect(items.find((i) => i.relativePath.endsWith(".env"))?.rejected).toBe(true);
  });

  it("summarizes plan statuses", () => {
    const summary = summarizePlan([
      { relative_path: "a", status: "new", size_bytes: 1 },
      { relative_path: "b", status: "changed", size_bytes: 2 },
      { relative_path: "c", status: "unchanged", size_bytes: 3 },
      { relative_path: "d", status: "rejected", size_bytes: 4 },
    ]);
    expect(summary.counts).toEqual({ new: 1, changed: 1, unchanged: 1, rejected: 1 });
    expect(summary.uploadable).toHaveLength(2);
    expect(summary.totalBytes).toBe(10);
  });
});
