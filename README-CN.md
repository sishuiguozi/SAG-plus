# SAG-plus

SAG-plus 是基于 [Zleap-AI/SAG](https://github.com/Zleap-AI/SAG) 的本地个人优化分支。
当前仓库定位为桌面开发工作区，唯一支持的启动方式是 Electron 开发模式。

## 启动桌面应用

前提条件：

- Node.js 20 或更高版本。
- Desktop、Web、API 的依赖已经安装；尤其需要存在 `apps/api/.venv` 和
  `apps/web/node_modules`。

在 Git Bash 中运行：

```bash
cd /e/SAG-plus/apps/desktop
npm run dev
```

桌面脚本会启动或复用本地 API（`127.0.0.1:8000`）、Web（`127.0.0.1:3000`
或 `3001`）和 Electron 窗口。按 `Ctrl+C` 停止。

如果项目从 `E:\sag-dev` 迁移而来，请先继续使用已有的本地数据和开发依赖，
再有计划地迁移。`.data`、上传文件、数据库与模型缓存均为本地数据，不能提交到 Git。

## SAG-plus 的重点优化

| 方向 | 本分支的实现 |
| --- | --- |
| 检索 | 语义检索叠加 LanceDB FTS/BM25、重排、结果缓存与字面检索回退；FTS 在异步事件循环外执行。 |
| 上下文 | 父子分块以子块完成精确召回、以父块提供完整上下文，并自动消除重复命中。 |
| 入库 | 持久化批量向量写入协调器为 LanceDB 提供单写者、重试、恢复和幂等工作项。 |
| 存储 | SQLite 连接与 PRAGMA 调优、磁盘保护、LanceDB 清理、索引维护和读写可观测性降低本地存储压力。 |
| 评估 | 仓库包含检索评估样例与运行耗时输出，可基于本地数据衡量优化效果。 |

实现细节见 [2026 优化状态](docs/SAG_OPTIMIZATION_2026.md) 与
[架构补丁](docs/ARCHITECTURE_PATCHES.md)。

## 日常使用

1. 使用 `npm run dev` 启动桌面应用。
2. 在 **设置 → 知识库** 中新增或配置知识源。
3. 等待入库完成，再在搜索或对话中获得可溯源的回答。
4. 对新入库文档选择「父子分块」，即可获得子块精确召回与父块完整上下文；
   旧文档需要重新处理才会拥有父子关系。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| API 没有启动 | 确认 `apps/api/.venv` 存在，且 8000 端口未被无关进程占用。 |
| Web 没有启动 | 确认 `apps/web/node_modules` 存在；桌面启动器会使用 3000 或 3001 端口。 |
| Electron 立即退出 | 确认命令在 `E:\SAG-plus\apps\desktop` 执行，并查看终端中最先失败的 API 或 Web 进程。 |
| 旧知识库没有显示 | Git 不会迁移本地数据；创建新索引前先确认实际使用的数据目录。 |

## 开发参考

- [桌面开发](apps/desktop/README.md)
- [API 架构](apps/api/README.md)
- [优化状态](docs/SAG_OPTIMIZATION_2026.md)
- [优化计划](docs/SAG_OPTIMIZATION_PLAN.md)

本项目使用 [MIT](LICENSE) 协议；上游归属保留给
[Zleap-AI/SAG](https://github.com/Zleap-AI/SAG)。
