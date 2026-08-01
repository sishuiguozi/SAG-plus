import { readFileSync } from "node:fs";
import { createServer } from "node:http";
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
let restartingApi = false;

// 维护重启控制（SAG-OPT-803）：桌面端“立即维护清理”在 dev 模式下
// 通过这个本地端口请求重启 API，让新 API 启动早期执行 pending 的维护。
const CONTROL_PORT = 43827;
const CONTROL_PATH = "/__sag_restart__";

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
    // 维护重启时会主动停掉 API，不要触发整组退出。
    if (!stopping && !restartingApi && code && code !== 0) {
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

function startApi() {
  const python = path.join(
    apiRoot,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
  return start(
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

async function waitForApiDown(timeoutMs = 30_000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (!(await reachable(apiUrl))) {
      // 进程已停止响应后再留一点余量，避免端口未释放导致重启失败。
      await new Promise((resolve) => setTimeout(resolve, 300));
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("Timed out waiting for API to stop");
}

async function restartApiForMaintenance() {
  if (restartingApi) return;
  restartingApi = true;
  try {
    const index = children.findIndex((entry) => entry.name === "API");
    if (index === -1) {
      console.warn("[dev] 未找到 API 子进程，直接启动新的");
      startApi();
    } else {
      console.log("[dev] 收到维护重启请求：停止 API…");
      const [entry] = children.splice(index, 1);
      stopChild(entry.child);
      await waitForApiDown();
      console.log("[dev] API 已停止，重新启动…");
      startApi();
    }
    await waitFor(apiUrl, 120_000);
    console.log("[dev] API 已重启，维护将在启动早期执行");
  } catch (error) {
    console.error("[dev] API 重启失败：", error);
  } finally {
    restartingApi = false;
  }
}

// 维护重启控制端点：桌面端「立即维护清理」请求后，停掉 API 并重启，
// 让新 API 在启动早期执行 pending 的 LanceDB 维护清理。
const controlServer = createServer((req, res) => {
  let url;
  try {
    url = new URL(req.url || "/", `http://127.0.0.1:${CONTROL_PORT}`);
  } catch {
    url = null;
  }
  if (req.method === "POST" && url && url.pathname === CONTROL_PATH) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true }));
    void restartApiForMaintenance();
    return;
  }
  res.writeHead(404, { "Content-Type": "text/plain" });
  res.end("not found");
});
controlServer.on("error", (error) => {
  console.warn(`[dev] 维护控制端口 ${CONTROL_PORT} 不可用：${error.message}`);
});
controlServer.listen(CONTROL_PORT, "127.0.0.1");

if (!(await reachable(apiUrl))) {
  startApi();
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
