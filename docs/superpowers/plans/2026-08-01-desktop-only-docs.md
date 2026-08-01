# SAG-plus 桌面优先文档实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将用户文档收敛为可运行的 SAG-plus 桌面开发入口，并突出已实现的检索与知识库优化。

**架构：** 根 README 是用户的唯一入口，中文版与英文版保持同一章节和命令；桌面 README 说明其实际运行条件。API、变更记录、贡献、优化与 Skill 文档保留各自职责，但不再提供 Docker、上游发布或独立服务启动说明。

**技术栈：** Markdown、npm、Node.js 20+、ripgrep、Git。

---

## 文件结构

- 修改：`README.md` 与 `README-CN.md` — 无媒体的项目概览、优化重点、唯一启动命令和常见问题。
- 修改：`apps/desktop/README.md` — 实际桌面开发与本地数据说明。
- 修改：`apps/api/README.md`、`CHANGELOG.md`、`CONTRIBUTING.md` — 删除独立部署和无效入口，链接至桌面说明。
- 修改：`docs/ARCHITECTURE_PATCHES.md`、`docs/SAG_OPTIMIZATION_2026.md`、`docs/SAG_OPTIMIZATION_PLAN.md`、`skills/sag/**/*.md` — 保留技术记录，标明桌面启动是唯一支持入口。

### 任务 1：以桌面入口重写根 README

**文件：**
- 修改：`README.md`
- 修改：`README-CN.md`
- 测试：Markdown 内容检查

- [x] **步骤 1：建立内容验收命令**

运行：`rg -n -i 'docker|compose|pypi|pip install zleap-sag|release|<img|\.gif|\.png|\.jpg|\.jpeg' README.md README-CN.md`

预期：当前文档匹配到过期部署、发布与媒体内容。

- [x] **步骤 2：重写两份根 README**

两份文档都只保留项目定位、优化摘要、环境要求、以下唯一命令、数据迁移提醒和常见问题：

```bash
cd /e/SAG-plus/apps/desktop
npm install
npm run dev
```

优化摘要必须涵盖 FTS/BM25、异步检索、写入队列、LanceDB 维护和父子分块。不要保留 `img` 标签、媒体文件链接、Docker、独立 API/Web、PyPI、发布或安装包文字。

- [x] **步骤 3：验证根 README**

运行：`git diff --check`，以及步骤 1 的 `rg` 命令。

预期：`git diff --check` 无输出，第二条命令不匹配。

### 任务 2：精简子项目与技术说明

**文件：**
- 修改：`apps/desktop/README.md`
- 修改：`apps/api/README.md`
- 修改：`CHANGELOG.md`
- 修改：`CONTRIBUTING.md`
- 修改：`docs/ARCHITECTURE_PATCHES.md`
- 修改：`docs/SAG_OPTIMIZATION_2026.md`
- 修改：`docs/SAG_OPTIMIZATION_PLAN.md`
- 修改：`skills/sag/SKILL.md`
- 修改：`skills/sag/references/mcp-tools.md`
- 修改：`skills/sag/references/search-strategies.md`
- 测试：文档命令与媒体检查

- [x] **步骤 1：重写桌面 README**

保留 Node.js 20+、`npm install`、`npm run dev`、端口冲突排查和本地数据目录；删除安装包、Release、签名、自动更新、发布 Secrets、环境和构建流程。

- [x] **步骤 2：同步辅助说明**

API README 改为桌面内置 API 说明；贡献指南只给出桌面验证命令；优化和 Skill 文档只保留实现记录与检索行为，不给出其他启动方式。

- [x] **步骤 3：验证没有失效入口或媒体嵌入**

运行：`rg -n -i 'docker|compose|github release|release-public|pypi|pip install zleap-sag|<img|\.gif|\.png|\.jpg|\.jpeg' README.md README-CN.md apps docs skills CHANGELOG.md CONTRIBUTING.md`，然后运行 `git diff --check`。

预期：用户说明没有不支持的启动或媒体内容；`git diff --check` 无输出。

### 任务 3：提交并推送文档收敛结果

**文件：** 任务 1、任务 2 的全部文档。

- [x] **步骤 1：复核唯一启动命令**

运行：`rg -n 'npm run dev|docker compose|uvicorn|next dev' README.md README-CN.md apps/desktop/README.md apps/api/README.md`

预期：用户指引只展示 `npm run dev`；源码描述中的脚本名称不作为启动命令出现。

- [ ] **步骤 2：提交文档变更**

运行：`git add README.md README-CN.md CHANGELOG.md CONTRIBUTING.md apps docs skills; git commit -m "docs: focus on desktop development"`

预期：提交只包含文档变更。

- [ ] **步骤 3：推送并验证远程**

运行：`git push origin main; git status --short; git ls-remote origin refs/heads/main`

预期：工作树干净，远程 `main` SHA 与本地 HEAD 一致。
