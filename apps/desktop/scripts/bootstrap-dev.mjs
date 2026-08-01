import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const webRoot = path.resolve(desktopRoot, "../web");
const apiRoot = path.resolve(desktopRoot, "../api");
const isWindows = process.platform === "win32";

export function needsDesktopInstall(exists = existsSync) {
  return !exists("node_modules/.bin/tsc");
}

export function needsWebInstall(exists = existsSync) {
  return !exists("node_modules/next/dist/bin/next");
}

export function needsApiInstall(exists = existsSync) {
  return !exists(".venv/Scripts/python.exe") || !exists("sag_api/__init__.py");
}

function run(command, args, options) {
  const result = spawnSync(command, args, {
    ...options,
    stdio: "inherit",
    shell: isWindows,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} ${args.join(" ")} exited with ${result.status}`);
}

function pythonVersion(command) {
  const result = spawnSync(command, ["--version"], { encoding: "utf8", shell: isWindows });
  const text = `${result.stdout || ""}${result.stderr || ""}`;
  const match = text.match(/Python\s+(\d+)\.(\d+)/i);
  if (result.status !== 0 || !match) throw new Error("Python 3.11 or newer is required for SAG-plus development.");
  if (Number(match[1]) !== 3 || Number(match[2]) < 11) throw new Error(`Python 3.11 or newer is required; found ${text.trim()}.`);
}

function hasApiPackage(python) {
  const result = spawnSync(python, ["-c", "import sag_api"], { stdio: "ignore", shell: isWindows });
  return result.status === 0;
}

export function prepareDevDependencies({ exists = existsSync } = {}) {
  if (needsDesktopInstall((relative) => exists(path.join(desktopRoot, relative)))) {
    console.log("[SAG-plus] Installing desktop dependencies…");
    run("npm", ["ci"], { cwd: desktopRoot });
  }
  if (needsWebInstall((relative) => exists(path.join(webRoot, relative)))) {
    console.log("[SAG-plus] Installing web dependencies…");
    run("npm", ["ci"], { cwd: webRoot });
  }

  const venvPython = path.join(apiRoot, ".venv", isWindows ? "Scripts/python.exe" : "bin/python");
  if (!exists(venvPython)) {
    const python = process.env.SAG_PYTHON || "python";
    pythonVersion(python);
    console.log("[SAG-plus] Creating API virtual environment…");
    run(python, ["-m", "venv", ".venv"], { cwd: apiRoot });
  }
  if (!hasApiPackage(venvPython)) {
    console.log("[SAG-plus] Installing API dependencies…");
    run(venvPython, ["-m", "pip", "install", "-e", ".[dev]"], { cwd: apiRoot });
  }
}

const currentFile = fileURLToPath(import.meta.url);
if (process.argv[1] && path.resolve(process.argv[1]) === currentFile) {
  try {
    prepareDevDependencies();
  } catch (error) {
    console.error(`[SAG-plus] Dependency setup failed: ${error.message}`);
    process.exitCode = 1;
  }
}
