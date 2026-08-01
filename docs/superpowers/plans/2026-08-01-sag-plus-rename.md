# SAG-plus 仓库改名实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将个人维护优化分支完整改名为 `SAG-plus`，并确保 GitHub、Git 远程、本地目录与说明文档一致。

**架构：** GitHub 是仓库规范来源，先在 GitHub 重命名，再把本地工作树和 `origin` 指向新地址。文档仅替换本 fork 的标识与链接，保留上游归属和历史路径。

**技术栈：** Git、GitHub CLI、PowerShell、Markdown、ripgrep。

---

## 文件结构

- 修改：`README.md` — 英文项目名、徽标、克隆命令和仓库链接。
- 修改：`README-CN.md` — 中文项目名、徽标、克隆命令和仓库链接。
- 修改：`CHANGELOG.md`、`CONTRIBUTING.md` — fork 标识与贡献入口。
- 修改：`apps/api/README.md`、`apps/desktop/README.md` — 子项目说明中的 fork 标识。
- 修改：`docs/ARCHITECTURE_PATCHES.md`、`docs/SAG_OPTIMIZATION_2026.md`、`docs/SAG_OPTIMIZATION_PLAN.md` — 当前维护仓库标识；仅保留历史机器路径。
- 修改：`skills/sag/SKILL.md`、`skills/sag/references/*.md` — 服务能力说明中的项目名称。

### 任务 1：重命名 GitHub 规范仓库

**文件：** 无本地文件变更。

- [x] **步骤 1：确认 GitHub 仓库管理权限**

实际执行：在已登录 GitHub 网页会话中打开 `sishuiguozi/mysag` 的仓库设置。

结果：设置页可访问，确认账户拥有重命名权限；本机未安装 GitHub CLI。

- [x] **步骤 2：重命名远程仓库**

实际执行：在 GitHub 仓库设置中将名称改为 `SAG-plus` 并提交。

预期：命令成功；GitHub 将旧地址重定向至 `https://github.com/sishuiguozi/SAG-plus`。

- [x] **步骤 3：验证新仓库**

运行：`git ls-remote https://github.com/sishuiguozi/SAG-plus.git HEAD`

结果：新仓库存在且可读取 `HEAD`。

### 任务 2：同步本地 Git 与工作目录

**文件：** 无应用源文件变更。

- [x] **步骤 1：更新 origin**

运行：`git remote set-url origin https://github.com/sishuiguozi/SAG-plus.git; git remote -v`

预期：fetch 和 push 均指向新地址。

- [x] **步骤 2：确认工作树可安全迁移**

运行：`git status --short; git log -1 --oneline`

预期：工作树没有未提交改动，最新提交为本次改名规格提交。

- [ ] **步骤 3：移动并重新进入工作目录**

运行：`Move-Item -LiteralPath 'E:\mysag' -Destination 'E:\SAG-plus'; Set-Location 'E:\SAG-plus'; git rev-parse --show-toplevel`

预期：仓库根目录为 `E:/SAG-plus`；不删除任何文件。

### 任务 3：替换当前项目标识并验证文档

**文件：** 本计划「文件结构」中列出的 11 个当前说明文档。

- [x] **步骤 1：编写名称验证命令（先确认当前失败）**

运行：`rg -n -i --glob '*.md' 'mysag|sishuiguozi/mysag' README.md README-CN.md CHANGELOG.md CONTRIBUTING.md apps docs skills`

预期：在当前 fork 标识处匹配到 `mysag`；历史路径 `E:\sag-dev` 不属于替换目标。

- [x] **步骤 2：更新 fork 名称与仓库链接**

将当前 fork 标识改为 `SAG-plus`，将仓库 URL 改为 `https://github.com/sishuiguozi/SAG-plus`，并把克隆示例改为：

```bash
git clone https://github.com/sishuiguozi/SAG-plus.git
cd SAG-plus
```

保留 `Zleap-AI/SAG` 的上游链接；保留历史说明中的 `E:\sag-dev`。

- [x] **步骤 3：运行文档格式和名称检查**

运行：`git diff --check; rg -n -i --glob '*.md' 'mysag|sishuiguozi/mysag' README.md README-CN.md CHANGELOG.md CONTRIBUTING.md apps docs skills`

预期：`git diff --check` 无输出；匹配仅可能存在于本改名设计与计划的历史描述中，不出现在用户文档。

- [ ] **步骤 4：提交文档改名**

运行：`git add README.md README-CN.md CHANGELOG.md CONTRIBUTING.md apps/api/README.md apps/desktop/README.md docs skills; git commit -m "docs: rename fork to SAG-plus"`

预期：创建一个仅包含名称、URL 与克隆命令改动的提交。

### 任务 4：推送并完成远程验证

**文件：** 无新增文件。

- [ ] **步骤 1：推送当前 main**

运行：`git push origin main`

预期：规格提交与文档改名提交均已推送至 `sishuiguozi/SAG-plus`。

- [ ] **步骤 2：验证远程提交、远程地址和工作树**

运行：`git status --short; git remote -v; git ls-remote origin refs/heads/main; git log -1 --oneline`

预期：工作树干净，`origin` 为新地址，远程 `main` SHA 与本地最新提交一致。
