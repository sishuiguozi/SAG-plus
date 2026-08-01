# SAG-plus 仓库改名设计

## 目标

将个人维护优化分支从 `mysag` 统一改名为 **SAG-plus**，使 GitHub 仓库、开发目录、远程地址、文档名称和克隆命令保持一致。

## 范围

1. 将 GitHub 仓库重命名为 `sishuiguozi/SAG-plus`。
2. 将本地工作目录从 `E:\mysag` 更名为 `E:\SAG-plus`。
3. 将 `origin` 同步为新的 HTTPS 地址。
4. 更新所有用户可见 Markdown 文档、技能文档和克隆示例中的 `mysag` 名称与仓库链接。
5. 保留上游 `Zleap-AI/SAG` 的归属说明；它不是本次改名对象。

## 非目标

- 不修改应用包名、数据库表名、环境变量前缀或 API 路径。
- 不启用桌面端发布工作流；文档继续如实说明该流程尚未迁移到此 fork。
- 不改写历史优化基线中表示旧工作目录的 `E:\sag-dev`。

## 执行顺序

1. 通过 GitHub API 重命名远程仓库。
2. 验证 GitHub 对旧地址的重定向和新地址可访问性。
3. 更新并验证工作目录、`origin`、全部名称引用和 Markdown 链接。
4. 提交文档与仓库元数据变更，推送到新的 `main` 远程。

## 验收标准

- `git remote -v` 指向 `https://github.com/sishuiguozi/SAG-plus.git`。
- 所有当前文档中不再把本 fork 称为 `mysag`，历史路径说明除外。
- README 的克隆示例和仓库链接可用。
- 工作树干净，最新提交已推送到新仓库的 `main`。
