import assert from "node:assert/strict";
import test from "node:test";

import {
  needsApiInstall,
  needsDesktopInstall,
  needsWebInstall,
} from "./bootstrap-dev.mjs";

test("desktop dependencies require the TypeScript executable", () => {
  assert.equal(needsDesktopInstall((path) => path === "node_modules/.bin/tsc"), false);
  assert.equal(needsDesktopInstall(() => false), true);
});

test("web dependencies require the Next.js executable", () => {
  assert.equal(needsWebInstall((path) => path === "node_modules/next/dist/bin/next"), false);
  assert.equal(needsWebInstall(() => false), true);
});

test("API dependencies require both the virtualenv Python and SAG package", () => {
  assert.equal(
    needsApiInstall((path) => path === ".venv/Scripts/python.exe" || path === "sag_api/__init__.py"),
    false,
  );
  assert.equal(needsApiInstall((path) => path === ".venv/Scripts/python.exe"), true);
});
