// SAG-OPT-701：dev 服务状态检查（npm run dev:status）
// 只读：探测 API / Web / Electron 是否在运行，并给出长期 dev 内存与重启建议。
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";

const require = createRequire(import.meta.url);

async function reachable(url) {
  try {
    const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(3000) });
    return response.ok;
  } catch {
    return false;
  }
}

function detectElectron() {
  if (process.platform !== "win32") return null;
  const out = spawnSync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command",
     "Get-CimInstance Win32_Process -Filter \"Name='electron.exe'\" | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
    { encoding: "utf8", timeout: 10000 },
  );
  try {
    const raw = JSON.parse(out.stdout || "[]");
    const rows = Array.isArray(raw) ? raw : [raw];
    return rows
      .filter((r) => String(r.CommandLine || "").includes("sag"))
      .map((r) => ({ pid: r.ProcessId, cmd: String(r.CommandLine || "").slice(0, 120) }));
  } catch {
    return null;
  }
}

const apiUrl = "http://127.0.0.1:8000/api/v1/system/ready";
const webPorts = [3000, 3001];
const rows = [];

rows.push(["API", `http://127.0.0.1:8000`, (await reachable(apiUrl)) ? "运行中 ✅" : "未运行 ❌"]);
for (const port of webPorts) {
  rows.push(["Web", `http://127.0.0.1:${port}`, (await reachable(`http://127.0.0.1:${port}`)) ? "运行中 ✅" : "未运行 ❌"]);
}
const electron = detectElectron();
rows.push(["Electron", "sag-desktop", electron && electron.length ? `运行中 ✅ (pid=${electron.map((e) => e.pid).join(",")})` : "未运行 ❌"]);

const pad = (s, n) => String(s).padEnd(n);
console.log("SAG 开发服务状态");
console.log("------------------");
console.log(`${pad("服务", 10)}${pad("地址/说明", 34)}状态`);
for (const [name, addr, state] of rows) {
  console.log(`${pad(name, 10)}${pad(addr, 34)}${state}`);
}
console.log("");
console.log("提示：");
console.log("  - 长时间运行 next dev / uvicorn 可能占用较多内存；建议每 1~2 天重启一次。");
console.log("  - 端口冲突时先停掉占用 8000/3000/3001 的进程，再运行 npm run dev。");
console.log("  - 数据目录：E:\\sag\\.data（引擎库）与 AppData/Roaming/SAG（用户配置）");

const anyUp = rows.some(([, , state]) => state.includes("运行中"));
process.exit(anyUp ? 0 : 1);
