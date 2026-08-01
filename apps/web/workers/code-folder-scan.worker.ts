/// <reference lib="webworker" />

import ignore from "ignore";

type Incoming = {
  rootName: string;
  files: Array<{ relativePath: string; sizeBytes: number; buffer?: ArrayBuffer; name: string }>;
};

function toHex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256(buf: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return toHex(digest);
}

self.onmessage = async (event: MessageEvent<Incoming>) => {
  const { rootName, files } = event.data;
  const ig = ignore();
  // best-effort: look for root gitignore content if provided as a virtual file later
  const results: Array<{
    relativePath: string;
    sizeBytes: number;
    sha256?: string;
    rejected?: boolean;
    reason?: string;
  }> = [];
  let done = 0;
  for (const file of files) {
    const relativePath = file.relativePath.replace(/\\/g, "/");
    if (ig.ignores(relativePath.replace(new RegExp(`^${rootName}/`), ""))) {
      results.push({
        relativePath,
        sizeBytes: file.sizeBytes,
        rejected: true,
        reason: "gitignore excluded",
      });
    } else if (file.buffer) {
      const bytes = new Uint8Array(file.buffer);
      if (bytes.includes(0)) {
        results.push({
          relativePath,
          sizeBytes: file.sizeBytes,
          rejected: true,
          reason: "binary file",
        });
      } else {
        const hash = await sha256(file.buffer);
        results.push({ relativePath, sizeBytes: file.sizeBytes, sha256: hash });
      }
    } else {
      results.push({ relativePath, sizeBytes: file.sizeBytes });
    }
    done += 1;
    if (done % 20 === 0) {
      (self as DedicatedWorkerGlobalScope).postMessage({
        type: "progress",
        done,
        total: files.length,
      });
    }
  }
  (self as DedicatedWorkerGlobalScope).postMessage({ type: "done", results });
};

export {};

