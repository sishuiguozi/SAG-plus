import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const webRoot = path.resolve(desktopRoot, "../web");
const apiRoot = path.resolve(desktopRoot, "../api");
const apiUrl = "http://127.0.0.1:8000/api/v1/system/ready";
const webUrl = "http://127.0.0.1:3000";
const webAltUrl = "http://127.0.0.1:3001";
const children = [];
let stopping = false;

// 开发模式下与 Electron 使用同一份数据位置配置（{userData}/data-root.json），
// 用户通过 设置 → 系统 → 知识库数据位置 保存后，重启 dev 会注入给 API。
function devDataRootOverride() {
  try {
    const productName = require(path.join(desktopRoot, "package.json")).productName || "SAG";
    const userDataDir = path.join(process.env.APPDATA || "", `${productName} Development`);
    const file = path.join(userDataDir, "data-root.json");
    const parsed = JSON.parse(readFileSync(file, "utf8"));
    if (parsed && typeof parsed.root === "string" && parsed.root.trim()) {
      return parsed.root.trim();
    }
  } catch {
    // 未保存过数据位置：继续使用 apps/api/.env 的默认路径。
  }
  return null;
}

async function reachable(url) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitFor(url, timeoutMs = 60_000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await reachable(url)) return;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function waitForAny(urls, timeoutMs = 90_000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    for (const url of urls) {
      if (await reachable(url)) return url;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out waiting for ${urls.join(" or ")}`);
}

function start(name, command, args, options) {
  const child = spawn(command, args, {
    ...options,
    stdio: "inherit",
    shell: process.platform === "win32",
    detached: process.platform !== "win32",
  });
  children.push({ name, child });
  child.once("exit", (code) => {
    if (!stopping && code && code !== 0) {
      console.error(`${name} exited with code ${code}`);
      stopAll(code);
    }
  });
  return child;
}

function stopChild(child) {
  if (!child.pid || child.killed) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      stdio: "ignore",
    });
    return;
  }
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    child.kill("SIGTERM");
  }
}

function stopAll(exitCode = 0) {
  if (stopping) return;
  stopping = true;
  for (const { child } of children.reverse()) stopChild(child);
  process.exit(exitCode);
}

process.once("SIGINT", () => stopAll(0));
process.once("SIGTERM", () => stopAll(0));

if (!(await reachable(apiUrl))) {
  const python = path.join(
    apiRoot,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
  start(
    "API",
    python,
    [
      "-m",
      "uvicorn",
      "sag_api.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      "8000",
    ],
    {
      cwd: apiRoot,
      env: {
        ...process.env,
        ...(devDataRootOverride()
          ? { SAG_DATA_ROOT: devDataRootOverride() }
          : {}),
        SAG_ENVIRONMENT: "dev",
      },
    },
  );
}

const reusedWebUrl = (await reachable(webUrl)) ? webUrl : ((await reachable(webAltUrl)) ? webAltUrl : null);
if (!reusedWebUrl) {
  start("Web", "npm", ["run", "dev"], {
    cwd: webRoot,
    env: {
      ...process.env,
      NEXT_PUBLIC_API_BASE: "http://127.0.0.1:8000",
      NEXT_PUBLIC_ENABLE_WINDOW_SCALING: "false",
    },
  });
}

const resolvedWebUrl = reusedWebUrl
  ? reusedWebUrl
  : await waitForAny([webUrl, webAltUrl]);
await Promise.all([waitFor(apiUrl), Promise.resolve(resolvedWebUrl)]);

const electronPath = require("electron");
const electron = start("Electron", electronPath, [desktopRoot], {
  cwd: desktopRoot,
  env: {
    ...process.env,
    SAG_DESKTOP_DEV_WEB_URL: resolvedWebUrl,
  },
});
electron.once("exit", (code) => stopAll(code ?? 0));
