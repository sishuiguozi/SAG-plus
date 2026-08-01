# 代码文件夹入库指南

## 启动方式

唯一支持的开发启动方式：

```bash
cd /e/SAG-plus/apps/desktop
npm run dev
```

## 能力概览

- Tree-sitter 全语言代码解析与符号级父子分块
- 本地代码文件夹递归扫描、安全排除与增量上传
- 每知识库三档 LLM 抽取：`off` / `comments`（默认）/ `all`
- 代码更新时旧版本持续可检索，成功后按 hash 过滤旧块并异步清理
- 检索返回“紧凑父上下文 + 精确子源码”

## 设置位置

| 能力 | 位置 |
| --- | --- |
| 文档解析（MarkItDown / MinerU） | 设置 → 模型 → 解析模型 |
| 代码解析语言包（Tree-sitter） | 设置 → 模型 → 解析模型 → 代码解析（Tree-sitter） |
| 代码抽取策略（off/comments/all） | 知识库详情页 → 代码抽取 |
| 导入代码文件夹 | 知识库详情页 → 导入代码文件夹 |

## 使用步骤

1. 启动桌面应用。
2. 打开目标知识库。
3. 单文件上传：明确识别的源码走 Tree-sitter；普通 HTML/JSON 仍走 MarkItDown。
4. 代码文件夹导入：
   - 选择本地根目录
   - 前端扫描并计算 SHA-256
   - 调用 plan 得到 new/changed/unchanged/rejected
   - 仅上传 new/changed
5. 在知识库卡片中配置代码抽取策略（只影响后续入库/重处理）。
6. 在 **设置 → 模型 → 解析模型 → 代码解析（Tree-sitter）** 查看资源卡，下载/暂停/继续/修复语言包（约 500MB）。语言包齐全后按钮显示“已下载/无需修复”，不会整包重下。

## 文件路由

| 类型 | 单文件上传 | 代码文件夹 |
| --- | --- | --- |
| `.py/.ts/.java/...` 源码 | Tree-sitter | Tree-sitter |
| `.html/.json` | MarkItDown | Tree-sitter |
| Markdown | 原 Markdown 路由 | 原 Markdown 路由 |
| 普通文本 / AFSIM 文本扩展 | 文本路由 | 文本路由 |
| PDF/Office | 现有路由 | 默认不选，可手动选 |
| `.ipynb` | 不支持 | 不支持 |

## 安全与增量

- 拒绝 `.env`、私钥、凭据、锁文件、压缩/二进制、source map、`.gitignore` 命中项
- 本地缺失文件**不会**自动删除
- 同路径同 hash 幂等跳过；hash 变化走安全 replacement 发布

## 抽取策略

- `off`：不调用 LLM，仍完成解析/分块/索引
- `comments`：默认，仅把注释/docstring 送入 LLM
- `all`：仅对代码子块抽取，跳过紧凑父块

## 故障恢复

- 解析/抽取失败时，旧版本保持可检索
- 新版本成功发布后，旧 revision 被检索层过滤，并排队清理
- Tree-sitter 下载中断可继续；校验失败可修复；已就绪时下载/修复为 no-op；Windows 下 DLL 占用不会把完整安装误标为失败；测试环境禁止真实联网下载
