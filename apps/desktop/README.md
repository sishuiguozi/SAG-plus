# SAG-plus Desktop

桌面端是当前唯一支持的 SAG-plus 运行入口。`npm run dev` 负责协调本地
FastAPI、Next.js 和 Electron；不要分别启动这些服务。

## 运行

要求：

- Node.js 20 或更高版本。
- `apps/desktop/node_modules`、`apps/web/node_modules` 与
  `apps/api/.venv` 已经就绪。

在 Git Bash 中：

```bash
cd /e/SAG-plus/apps/desktop
npm run dev
```

启动器会复用可用的 API 与 Web 服务；否则启动 API（8000）、Web（3000 或
3001）和 Electron。按 `Ctrl+C` 会停止由该命令启动的进程。

## 排查

| 现象 | 操作 |
| --- | --- |
| API 等待超时 | 检查 `apps/api/.venv` 是否存在，以及 8000 端口是否被占用。 |
| Web 等待超时 | 检查 `apps/web/node_modules` 是否存在，以及 3000、3001 端口是否被占用。 |
| Electron 未出现 | 查看同一终端中 API 或 Web 的首个错误；修复后重新运行 `npm run dev`。 |
| 使用旧数据 | Git 不包含 `.data`、上传文件或模型缓存；先确定旧工作区的数据位置再迁移。 |

本文档只覆盖本地桌面开发及其检索、入库和存储优化。
