import { describe, expect, it } from "vitest";

import {
  isLocalEmbeddingTestDisabled,
  isLocalModelDownloadDisabled,
  toggleLocalModelSelection,
} from "./local-model-manager";
import type { LocalModelManagerStatus } from "./types";

const status = (backend: LocalModelManagerStatus["backend"]["status"]): LocalModelManagerStatus => ({
  backend_installed: backend === "ready",
  backend: { status: backend, error: null },
  models: [],
});

describe("local model manager controls", () => {
  it("keeps model selection multi-selectable without duplicate files", () => {
    expect(toggleLocalModelSelection(["bge-m3-Q8_0.gguf"], "bge-m3-Q6_K.gguf", true)).toEqual([
      "bge-m3-Q8_0.gguf",
      "bge-m3-Q6_K.gguf",
    ]);
    expect(toggleLocalModelSelection(["bge-m3-Q8_0.gguf"], "bge-m3-Q8_0.gguf", true)).toEqual([
      "bge-m3-Q8_0.gguf",
    ]);
  });

  it("requires a ready backend, a model choice, and no active request before download", () => {
    expect(isLocalModelDownloadDisabled(status("missing"), ["bge-m3-Q8_0.gguf"], null)).toBe(true);
    expect(isLocalModelDownloadDisabled(status("ready"), [], null)).toBe(true);
    expect(isLocalModelDownloadDisabled(status("ready"), ["bge-m3-Q8_0.gguf"], "download")).toBe(true);
    expect(isLocalModelDownloadDisabled(status("ready"), ["bge-m3-Q8_0.gguf"], null)).toBe(false);
  });

  it("enables an embedding test for an unsaved, ready local model draft", () => {
    const draftModelFile = "bge-m3-Q6_K.gguf";
    const readyStatus = {
      ...status("ready"),
      models: [{ file_name: draftModelFile, status: "ready" }],
    } as LocalModelManagerStatus;

    expect(isLocalEmbeddingTestDisabled("local", draftModelFile, readyStatus, null, false)).toBe(false);
    expect(isLocalEmbeddingTestDisabled("api", draftModelFile, readyStatus, null, false)).toBe(true);
    expect(isLocalEmbeddingTestDisabled("local", draftModelFile, readyStatus, "download", false)).toBe(true);
    expect(isLocalEmbeddingTestDisabled("local", draftModelFile, readyStatus, null, true)).toBe(true);
  });
});
