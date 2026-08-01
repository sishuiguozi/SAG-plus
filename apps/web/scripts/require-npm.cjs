#!/usr/bin/env node

const userAgent = process.env.npm_config_user_agent ?? "";
const manager = userAgent.split(" ", 1)[0]?.split("/", 1)[0] ?? "unknown";

if (manager !== "npm") {
  console.error([
    "",
    "This project uses npm and package-lock.json as its only package manager.",
    "Do not run pnpm or yarn in apps/web.",
    "Restore dependencies with: npm ci",
    "",
  ].join("\n"));
  process.exit(1);
}
