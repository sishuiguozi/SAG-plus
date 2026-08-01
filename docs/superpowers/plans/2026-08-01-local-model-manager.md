# SAG-plus 自动依赖与本地模型管理实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让桌面 `npm run dev` 自动补齐运行依赖，并在设置页按需安装 llama.cpp 后端和下载多个 bge-m3 GGUF 版本。

**架构：** 桌面启动脚本同步准备三套运行依赖后再启动服务。API 使用一个受认证保护、进程内状态明确的本地模型管理器执行后端安装和文件下载；Web 通过轮询该状态显示多选下载与每项进度。

**技术栈：** Node.js、npm、Python 3.11+、FastAPI、asyncio、urllib、React、TypeScript、pytest、Vitest。

---

## 文件结构

- 创建：`apps/desktop/scripts/bootstrap-dev.mjs` — 检测和安装 Desktop/Web/API 运行依赖。
- 修改：`apps/desktop/package.json` — `predev` 调用启动准备脚本。
- 测试：`apps/desktop/scripts/bootstrap-dev.test.mjs` — 依赖判定和命令构造测试。
- 创建：`apps/api/sag_api/sag/local_model_manager.py` — llama.cpp 安装、GGUF 目录、下载状态和原子文件提交。
- 修改：`apps/api/sag_api/api/v1/system.py` — 模型管理状态与动作端点。
- 修改：`apps/api/sag_api/schemas/system.py` — 下载请求与响应模式。
- 测试：`apps/api/tests/test_local_model_manager.py` — 下载校验、去重、安装命令和 API 授权测试。
- 修改：`apps/web/lib/types.ts`、`apps/web/lib/api.ts` — 模型管理 API 类型和调用。
- 修改：`apps/web/components/features/model-config-form.tsx` — 后端安装按钮、五版本多选、进度和当前模型选择。
- 修改：`apps/web/messages/en-US.json`、`apps/web/messages/zh-CN.json` — 中英文状态、动作和错误文案。
- 测试：`apps/web/lib/local-model-manager.test.ts` — 多选与下载可用性规则。
- 修改：`README.md`、`README-CN.md`、`apps/desktop/README.md` — 自动依赖准备与显式模型下载说明。

### 任务 1：实现桌面启动依赖准备

**文件：**
- 创建：`apps/desktop/scripts/bootstrap-dev.mjs`
- 修改：`apps/desktop/package.json`
- 测试：`apps/desktop/scripts/bootstrap-dev.test.mjs`

- [x] **步骤 1：编写依赖判定测试**

测试 `needsDesktopInstall`、`needsWebInstall` 和 `needsApiInstall`：缺少 Desktop `node_modules/.bin/tsc`、Web `node_modules/next`、API `.venv/Scripts/python.exe` 或 API 包时返回 true；完整路径时返回 false。

- [x] **步骤 2：运行测试确认失败**

运行：`node --test apps/desktop/scripts/bootstrap-dev.test.mjs`

预期：FAIL，报错 `ERR_MODULE_NOT_FOUND` 或所测导出不存在。

- [x] **步骤 3：实现准备脚本**

实现纯 Node 模块，导出依赖判定函数，并顺序执行：

```js
await run("npm", ["ci"], { cwd: desktopRoot });
await run("npm", ["ci"], { cwd: webRoot });
await run(python, ["-m", "venv", ".venv"], { cwd: apiRoot });
await run(venvPython, ["-m", "pip", "install", "-e", ".[dev]"], { cwd: apiRoot });
```

Python 解析必须拒绝低于 3.11 的版本；失败时保留子进程输出并返回非零退出码。

- [x] **步骤 4：接入 `predev` 并验证**

在 `apps/desktop/package.json` 新增 `"predev": "node ./scripts/bootstrap-dev.mjs"`，然后运行：

`node --test apps/desktop/scripts/bootstrap-dev.test.mjs && npm --prefix apps/desktop run typecheck`

预期：测试通过，TypeScript 无错误。

### 任务 2：实现受控本地模型管理 API

**文件：**
- 创建：`apps/api/sag_api/sag/local_model_manager.py`
- 修改：`apps/api/sag_api/api/v1/system.py`
- 修改：`apps/api/sag_api/schemas/system.py`
- 测试：`apps/api/tests/test_local_model_manager.py`

- [x] **步骤 1：编写模型管理失败测试**

测试固定五文件目录、拒绝未知文件、同文件下载请求去重、`.part` 未完成文件不计为可用模型、内容长度或 ETag 不符时删除临时文件，以及未认证请求不能触发安装/下载。

- [x] **步骤 2：运行测试确认失败**

运行：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_local_model_manager.py -q`

预期：FAIL，因为模型管理模块与 API 端点尚不存在。

- [x] **步骤 3：实现模型目录与后台状态**

在管理器中定义五个固定 `gpustack/bge-m3-GGUF` 文件；状态项包含 `file_name`、`status`、`downloaded_bytes`、`total_bytes`、`progress`、`error`、`model_path`。下载使用 `urllib.request`，写入 `*.part`，核对 `Content-Length` 和响应 `ETag`，再以 `Path.replace()` 原子提交。

- [x] **步骤 4：实现 llama.cpp 安装**

只使用 `sys.executable` 对应的 API 虚拟环境运行：

```python
[sys.executable, "-m", "pip", "install", "llama-cpp-python>=0.3.34", "--extra-index-url", "https://abetlen.github.io/llama-cpp-python/whl/cpu", "--only-binary", ":all:"]
```

安装状态显示未安装、安装中、已安装或失败。重复安装请求复用正在执行的任务。

- [x] **步骤 5：添加认证端点并验证**

增加状态、安装后端和批量下载端点，使用现有 `get_current_user`。运行：

`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_local_model_manager.py tests/test_settings_system_config.py -q`

预期：全部通过。

### 任务 3：在设置页提供多选操作

**文件：**
- 修改：`apps/web/lib/types.ts`
- 修改：`apps/web/lib/api.ts`
- 修改：`apps/web/components/features/model-config-form.tsx`
- 修改：`apps/web/messages/en-US.json`
- 修改：`apps/web/messages/zh-CN.json`
- 测试：`apps/web/lib/local-model-manager.test.ts`

- [x] **步骤 1：编写前端失败测试**

在 `local-model-manager.test.ts` 覆盖多选不重复、未安装后端/没有选择/已有请求时下载禁用。

- [x] **步骤 2：运行测试确认失败**

运行：`npm --prefix apps/web run test:unit -- lib/local-model-manager.test.ts`

预期：FAIL，因为管理器 API 和控件尚不存在。

- [x] **步骤 3：实现前端类型、API 和控件**

实现 `LocalModelManagerStatus`、`installLocalInferenceBackend()` 和 `downloadLocalModels(files)`；在本地 embedding 区域加入后端状态按钮、五版本复选框、批量下载按钮和 1 秒状态轮询。当前模型文件输入替换为已完成模型的下拉选择，保留现有未列出文件的兼容显示。

- [x] **步骤 4：添加中英文文案并验证**

添加下载、安装、排队、进度、校验失败、空间提示和重试文案。运行：

`npm --prefix apps/web run test:unit -- lib/local-model-manager.test.ts && npm --prefix apps/web run typecheck && npm --prefix apps/web run lint && npm --prefix apps/web run i18n:check`

预期：全部通过。

### 任务 4：更新使用文档并完成回归

**文件：**
- 修改：`README.md`
- 修改：`README-CN.md`
- 修改：`apps/desktop/README.md`

- [x] **步骤 1：更新启动与下载说明**

说明 `npm run dev` 会在缺少运行依赖时自动准备环境；说明模型权重默认不下载，用户在设置页主动安装后端并多选下载模型。

- [x] **步骤 2：运行跨层验证**

运行：

`cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_local_model_manager.py -q`

`npm --prefix apps/desktop run typecheck`

`npm --prefix apps/web run typecheck && npm --prefix apps/web run lint && npm --prefix apps/web run i18n:check`

`git diff --check`

预期：全部通过。

- [x] **步骤 3：提交并推送**

运行：

`git add apps README.md README-CN.md docs; git commit -m "feat: add local model manager"; git push origin main`

预期：工作树干净，远程 `main` 与本地 HEAD 一致。
