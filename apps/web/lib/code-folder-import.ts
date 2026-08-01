export type ScanCandidate = {
  relativePath: string;
  sizeBytes: number;
  file?: File;
  sha256?: string;
  rejected?: boolean;
  reason?: string;
  defaultSelected?: boolean;
};

export type PlanLikeItem = {
  relative_path: string;
  status: "new" | "changed" | "unchanged" | "rejected";
  reason?: string;
  size_bytes?: number;
};

const BLOCKED_DIRS = new Set([
  ".git",
  ".hg",
  ".svn",
  ".tox",
  ".venv",
  "__pycache__",
  "build",
  "coverage",
  "dist",
  "node_modules",
  "target",
  "vendor",
]);

const SENSITIVE_NAMES = [
  /^\.env(\..+)?$/i,
  /^id_rsa$/,
  /^id_dsa$/,
  /^id_ecdsa$/,
  /^id_ed25519$/,
  /credentials/i,
  /\.pem$/i,
  /\.key$/i,
  /\.p12$/i,
  /\.pfx$/i,
];

const LOCK_FILES = new Set([
  "package-lock.json",
  "pnpm-lock.yaml",
  "yarn.lock",
  "cargo.lock",
  "composer.lock",
  "gemfile.lock",
  "go.sum",
  "poetry.lock",
  "pipfile.lock",
]);

const BINARY_EXT = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".ico",
  ".zip",
  ".gz",
  ".7z",
  ".tar",
  ".exe",
  ".dll",
  ".so",
  ".dylib",
  ".bin",
  ".pdf",
  ".docx",
  ".pptx",
  ".xlsx",
  ".mp3",
  ".mp4",
]);

const DEFAULT_OFF_EXT = new Set([
  ".pdf",
  ".docx",
  ".pptx",
  ".xlsx",
  ".xls",
  ".csv",
  ".tsv",
  ".epub",
]);

export function normalizeRelativePath(path: string): string {
  let value = (path || "").replace(/\\/g, "/").trim();
  while (value.startsWith("./")) value = value.slice(2);
  value = value.replace(/^\/+/, "");
  return value;
}

export function preserveRootRelativePath(filePath: string, rootName: string): string {
  const rel = normalizeRelativePath(filePath);
  const root = normalizeRelativePath(rootName).split("/")[0] || rootName;
  if (!rel) return root;
  if (rel === root || rel.startsWith(`${root}/`)) return rel;
  // browser webkitRelativePath usually starts with root already
  const parts = rel.split("/");
  if (parts[0] === root) return rel;
  return `${root}/${rel}`;
}

export function isBlockedPath(relativePath: string): string | null {
  const rel = normalizeRelativePath(relativePath);
  const parts = rel.split("/");
  const base = parts[parts.length - 1] || "";
  if (parts.some((p) => BLOCKED_DIRS.has(p))) return "blocked directory";
  if (LOCK_FILES.has(base.toLowerCase())) return "lock file";
  if (SENSITIVE_NAMES.some((re) => re.test(base))) return "sensitive file";
  if (base.endsWith(".min.js") || base.endsWith(".map")) return "generated file";
  if (base.toLowerCase().endsWith(".ipynb")) return "notebook unsupported";
  const ext = base.includes(".") ? `.${base.split(".").pop()!.toLowerCase()}` : "";
  if (BINARY_EXT.has(ext) && DEFAULT_OFF_EXT.has(ext) === false && ext !== ".pdf") {
    // pure binaries blocked; office/pdf handled as default-off
    if ([".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".zip", ".gz", ".7z", ".tar", ".exe", ".dll", ".so", ".dylib", ".bin", ".mp3", ".mp4"].includes(ext)) {
      return "binary file";
    }
  }
  return null;
}

export function isDefaultSelectedPath(relativePath: string): boolean {
  const base = normalizeRelativePath(relativePath).split("/").pop() || "";
  const ext = base.includes(".") ? `.${base.split(".").pop()!.toLowerCase()}` : "";
  return !DEFAULT_OFF_EXT.has(ext);
}

export function classifyLocalCandidates(
  files: Array<{ relativePath: string; sizeBytes: number }>,
  rootName: string,
): ScanCandidate[] {
  return files.map((f) => {
    const relativePath = preserveRootRelativePath(f.relativePath, rootName);
    const blocked = isBlockedPath(relativePath);
    if (blocked) {
      return {
        relativePath,
        sizeBytes: f.sizeBytes,
        rejected: true,
        reason: blocked,
        defaultSelected: false,
      };
    }
    return {
      relativePath,
      sizeBytes: f.sizeBytes,
      rejected: false,
      defaultSelected: isDefaultSelectedPath(relativePath),
    };
  });
}

export function summarizePlan(items: PlanLikeItem[]) {
  const counts = { new: 0, changed: 0, unchanged: 0, rejected: 0 };
  for (const item of items) {
    counts[item.status] += 1;
  }
  const uploadable = items.filter((i) => i.status === "new" || i.status === "changed");
  const totalBytes = items.reduce((sum, i) => sum + (i.size_bytes || 0), 0);
  return { counts, uploadable, totalBytes };
}

export async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
