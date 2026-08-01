# 本地嵌入健康检查实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在设置页对已保存的本地嵌入模型执行一次真实、无副作用的向量生成测试，并显示模型名、向量维度与耗时。

**架构：** FastAPI 新端点先检查当前运行配置及 llama-cpp/python、模型文件状态，再使用现有 `LocalEmbeddingClient.generate()` 生成固定短文本的向量。前端在已有本地模型管理状态旁计算测试按钮是否可用，并把成功指标或 API 返回的可操作错误呈现给用户。

**技术栈：** FastAPI、asyncio、`time.perf_counter`、llama-cpp-python、React、TypeScript、pytest、Vitest。

---

## 文件结构

- 修改：`apps/api/sag_api/api/v1/system.py` — 实现受认证保护的本地 embedding 健康检查端点。
- 修改：`apps/api/tests/test_local_model_manager.py` — 覆盖端点授权、前置条件和真实客户端调用契约。
- 修改：`apps/web/lib/types.ts` — 声明健康检查响应类型。
- 修改：`apps/web/lib/api.ts` — 声明测试端点调用。
- 修改：`apps/web/lib/local-model-manager.ts` — 提取测试按钮启用条件。
- 修改：`apps/web/lib/local-model-manager.test.ts` — 覆盖测试按钮启用条件。
- 修改：`apps/web/components/features/model-config-form.tsx` — 增加按钮、加载态和测试结果。
- 修改：`apps/web/messages/zh-CN.json`、`apps/web/messages/en-US.json` — 增加按钮、结果与失败文案。
- 修改：`README.md`、`README-CN.md` — 说明本地模型测试为真实推理、无写入操作。

### 任务 1：实现本地嵌入健康检查 API

**文件：**
- 修改：`apps/api/tests/test_local_model_manager.py`
- 修改：`apps/api/sag_api/api/v1/system.py`

- [ ] **步骤 1：编写失败的端点测试**

在现有认证测试旁增加三个行为：未认证仍为 401；API embedding 模式返回可读失败；本地模式下
模拟 `_local_client().generate()` 后返回维度、当前模型文件和非负耗时。

```python
result = await client.post("/api/v1/system/local-models/test", headers=headers)
assert result.status_code == 200
assert result.json() == {
    "ok": True,
    "model_file": settings.embedding_local_model_file,
    "dimensions": 3,
    "elapsed_ms": pytest.approx(result.json()["elapsed_ms"], abs=10_000),
}
```

`monkeypatch` 需将 `settings.embedding_provider` 设为 `local`，并将
`sag_api.sag.embedding_backend._local_client` 替换为 `async generate()` 返回 `[0.1, 0.2, 0.3]`
的最小假客户端；测试完成后恢复全局 settings。

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
cd apps/api
.venv/Scripts/python.exe -m pytest tests/test_local_model_manager.py -q
```

预期：失败，原因是 `POST /api/v1/system/local-models/test` 尚不存在（404）。

- [ ] **步骤 3：实现最少端点逻辑**

在 `system.py` 增加 `POST /local-models/test`。实现逻辑：

```python
from time import perf_counter
from sag_api.sag.embedding_backend import _local_client, local_embedding_status

if settings.embedding_provider != "local":
    return {"ok": False, "message": "请先选择本地嵌入并保存配置"}
status = local_embedding_status(settings)
if not status["model_exists"]:
    return {"ok": False, "message": "请先下载当前选中的本地模型"}
started = perf_counter()
try:
    vector = await _local_client().generate("SAG-plus local embedding health check")
except Exception as exc:
    return {"ok": False, "message": str(exc)}
return {
    "ok": True,
    "model_file": settings.embedding_local_model_file,
    "dimensions": len(vector),
    "elapsed_ms": round((perf_counter() - started) * 1000),
}
```

端点保留 `get_current_user` 依赖。使用 `local_embedding_status` 让缺后端与文件状态沿用现有错误
文本；不写入数据库、不访问检索引擎、不改 settings。

- [ ] **步骤 4：运行端点测试确认通过**

运行：

```bash
cd apps/api
.venv/Scripts/python.exe -m pytest tests/test_local_model_manager.py tests/test_embedding_backend.py -q
```

预期：全部通过。

- [ ] **步骤 5：提交后端行为**

```bash
git add apps/api/sag_api/api/v1/system.py apps/api/tests/test_local_model_manager.py
git commit -m "feat(api): test local embeddings"
```

### 任务 2：提供设置页测试控制与状态

**文件：**
- 修改：`apps/web/lib/types.ts`
- 修改：`apps/web/lib/api.ts`
- 修改：`apps/web/lib/local-model-manager.ts`
- 修改：`apps/web/lib/local-model-manager.test.ts`
- 修改：`apps/web/components/features/model-config-form.tsx`
- 修改：`apps/web/messages/zh-CN.json`
- 修改：`apps/web/messages/en-US.json`

- [ ] **步骤 1：编写失败的测试按钮条件测试**

在 `local-model-manager.test.ts` 定义 `isLocalEmbeddingTestDisabled`。它接受当前
`ModelConfig`、`LocalModelManagerStatus`、进行中的 `LocalModelAction` 和测试中布尔值；
在 API 模式、后端非 ready、当前保存文件不为 ready、已有安装/下载/测试请求时返回 true。

```ts
expect(isLocalEmbeddingTestDisabled(localConfig, readyStatus, null, false)).toBe(false);
expect(isLocalEmbeddingTestDisabled(apiConfig, readyStatus, null, false)).toBe(true);
expect(isLocalEmbeddingTestDisabled(localConfig, missingCurrentFile, null, false)).toBe(true);
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
cd apps/web
npm run test:unit -- lib/local-model-manager.test.ts
```

预期：失败，原因是 `isLocalEmbeddingTestDisabled` 尚未导出。

- [ ] **步骤 3：实现前端类型、API 与纯判断函数**

在 `types.ts` 增加：

```ts
export interface LocalEmbeddingTestResult {
  ok: boolean;
  message?: string;
  model_file?: string;
  dimensions?: number;
  elapsed_ms?: number;
}
```

在 `api.ts` 增加 `testLocalEmbedding: () => request<LocalEmbeddingTestResult>("/api/v1/system/local-models/test", { method: "POST" })`。

在 `local-model-manager.ts` 实现：

```ts
export function isLocalEmbeddingTestDisabled(
  config: Pick<ModelConfig, "embedding_provider" | "embedding_local_model_file">,
  status: LocalModelManagerStatus,
  action: LocalModelAction,
  testing: boolean,
): boolean {
  const active = status.models.find((model) => model.file_name === config.embedding_local_model_file);
  return testing || action !== null || config.embedding_provider !== "local" ||
    status.backend.status !== "ready" || active?.status !== "ready";
}
```

- [ ] **步骤 4：接入表单按钮与结果**

在 `model-config-form.tsx` 添加 `localEmbeddingTestResult` 和 `testingLocalEmbedding` 状态。按钮
调用 `api.testLocalEmbedding()`；成功时显示 `{model_file} · {dimensions} dims · {elapsed_ms} ms`，
失败时用 destructive 文本显示 `message`。按钮放在下载/刷新按钮旁，且调用
`isLocalEmbeddingTestDisabled(cfg, localModels, localModelAction, testingLocalEmbedding)`。

在两个 message JSON 的 `ModelConfig` 下新增 `localModelTest`、`localModelTesting`、
`localModelTestFailed`、`localModelTestResult`、`localModelTestPrerequisite` 的中英文文本。

- [ ] **步骤 5：运行 Web 验证**

运行：

```bash
cd apps/web
npm run test:unit -- lib/local-model-manager.test.ts
npm run typecheck
npm run lint
npm run i18n:check
```

预期：单测、类型检查、lint 和 i18n 检查全部通过。

- [ ] **步骤 6：提交前端行为**

```bash
git add apps/web
git commit -m "feat(web): test local embedding model"
```

### 任务 3：更新说明并完成跨层回归

**文件：**
- 修改：`README.md`
- 修改：`README-CN.md`

- [ ] **步骤 1：更新本地嵌入使用说明**

在现有“安装后端、下载模型、保存配置”说明后补充：点击“测试本地模型”会生成一次临时向量，
显示模型、维度与耗时；不会上传文本、访问 API 或写入知识库。

- [ ] **步骤 2：运行跨层回归**

运行：

```bash
cd apps/api
.venv/Scripts/python.exe -m pytest tests/test_local_model_manager.py tests/test_embedding_backend.py -q
cd ../web
npm run test:unit -- lib/local-model-manager.test.ts
npm run typecheck
npm run lint
npm run i18n:check
cd ../desktop
node --test scripts/bootstrap-dev.test.mjs
npm run typecheck
cd ../..
git diff --check
```

预期：全部通过。

- [ ] **步骤 3：提交并推送完整功能**

```bash
git add README.md README-CN.md apps docs
git commit -m "feat: add local embedding health check"
git push origin main
```

预期：本地 `main` 推送至 `origin/main`，工作树干净。
