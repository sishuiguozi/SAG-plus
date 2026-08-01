# SAG-plus 桌面优先文档设计

## 目标

将 SAG-plus 的用户文档收敛为一个已确认可运行的桌面开发入口：在 `apps/desktop` 运行 `npm run dev`。文档重点说明本 fork 的检索、写入、存储与父子分块优化。

## 用户入口

唯一支持的启动流程是：

```bash
cd /e/SAG-plus/apps/desktop
npm run dev
```

首次运行前在同一目录执行 `npm install`。需要 Node.js 20 或更高版本；桌面脚本负责协调 Web、API 和 Electron。

## 文档边界

保留并更新：

- `README.md` 与 `README-CN.md`：项目定位、优化能力、前置条件、唯一启动方式、数据迁移提醒与常见问题。
- `apps/desktop/README.md`：桌面依赖、安装、启动、排查与本地数据位置。
- `CHANGELOG.md`、`CONTRIBUTING.md`、`apps/api/README.md`、优化与 Skill 文档：删除失效入口，保持与桌面优先定位一致。

移除：

- README 中的产品截图、GIF、论文图片、架构图片和社区二维码。
- Docker Compose、单独 API/Web、PyPI 安装、Postgres 部署、上游 Release、发布签名、自动更新和安装包流程。
- 未在本 fork 中启用的 GitHub Release、发布环境和签名说明。

## 非目标

- 不删除仓库中的图片资源或部署文件，只从用户说明中移除它们。
- 不修改桌面、API 或 Web 源码及其 npm 脚本。
- 不移除优化计划与架构补丁的技术历史；它们仍作为实现记录存在。

## 验收标准

- 用户说明中只给出 `npm run dev` 作为启动命令。
- `README.md`、`README-CN.md` 和桌面 README 不再嵌入图片、GIF、视频或二维码。
- 用户说明中不再推荐 Docker、单独 API/Web、PyPI 或 GitHub Release。
- 文档准确描述 FTS/BM25、异步检索、写入队列、LanceDB 维护和父子分块优化。
