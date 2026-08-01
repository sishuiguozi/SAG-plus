import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("local model API", () => {
  it("tests a local embedding model using its unsaved draft parameters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.testLocalEmbedding({
      model_file: "bge-m3-Q6_K.gguf",
      n_ctx: 4096,
      n_threads: 8,
    });

    const [url, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/system/local-models/test");
    expect(request).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        model_file: "bge-m3-Q6_K.gguf",
        n_ctx: 4096,
        n_threads: 8,
      }),
    });
  });
});
