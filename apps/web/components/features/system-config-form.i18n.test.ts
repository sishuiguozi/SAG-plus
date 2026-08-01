import en from "../../messages/en-US.json";
import zh from "../../messages/zh-CN.json";
import { describe, expect, it } from "vitest";

const fieldKeys = ["lancedb_fts_enabled"] as const;

describe("SystemConfig field translations", () => {
  it.each([['zh-CN', zh], ['en-US', en]] as const)(
    "provides labels and descriptions for every configured field in %s",
    (_locale, messages) => {
      for (const key of fieldKeys) {
        expect(messages.SystemConfig.fields[key].label).toBeTruthy();
        expect(messages.SystemConfig.fields[key].description).toBeTruthy();
      }
    },
  );
});
