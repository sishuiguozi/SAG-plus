# 草稿本地嵌入测试实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 允许用户不保存模型配置，直接对本地嵌入表单中当前选择的 GGUF 与推理参数执行真实健康检查。

**架构：** API 接收受限的模型文件名、上下文长度和线程数；从受控模型目录建立一次临时 `LocalEmbeddingClient` 并生成固定短文本的向量，绝不修改全局 embedding 单例或 settings。设置页把表单草稿传给端点，按钮启用状态以草稿和模型管理状态计算。

**技术栈：** FastAPI、Pydantic、asyncio、llama-cpp-python、React、TypeScript、pytest、Vitest。

---

## 文件结构

- 修改：`apps/api/sag_api/schemas/system.py` — 定义 `LocalEmbeddingTestRequest` 输入约束。
- 修改：`apps/api/sag_api/api/v1/system.py` — 接收草稿参数、验证受控模型状态、临时推理。
- 修改：`apps/api/tests/test_local_model_manager.py` — 覆盖草稿模型和参数被临时客户端使用。
- 修改：`apps/web/lib/types.ts` — 定义测试请求类型。
- 修改：`apps/web/lib/api.ts` — 将草稿传给测试端点。
- 修改：`apps/web/lib/local-model-manager.ts` — 用表单草稿判断测试按钮可用性。
- 修改：`apps/web/lib/local-model-manager.test.ts` — 覆盖未保存的本地草稿可以测试、API 草稿不可测试。
- 修改：`apps/web/components/features/model-config-form.tsx` — 发送当前草稿，而非已保存 `cfg`。
- 修改：`README.md`、`README-CN.md` — 说明测试无需保存且不改配置。

### 任务 1：让 API 以草稿参数创建临时客户端

**文件：**
- 修改：`apps/api/sag_api/schemas/system.py`
- 修改：`apps/api/sag_api/api/v1/system.py`
- 修改：`apps/api/tests/test_local_model_manager.py`

- [x] **步骤 1：编写失败测试**

将现有健康检查测试改为发送：

```python
json={"model_file": "bge-m3-Q6_K.gguf", "n_ctx": 4096, "n_threads": 6}
```

模拟 `LocalEmbeddingClient` 构造器，断言它收到模型管理器所列的 Q6 路径、`n_ctx=4096`、
`n_threads=6`，而不是从 `settings` 读取 Q8。另加未知 `model_file` 返回 422 的断言。

- [x] **步骤 2：运行失败测试**

运行：

```bash
cd apps/api
.venv/Scripts/python.exe -m pytest tests/test_local_model_manager.py -q
```

预期：失败，因为端点当前不接受请求体并仍调用全局 `_local_client()`。

- [x] **步骤 3：增加请求 schema 与临时客户端实现**

在 `system.py` schema 中定义：

```python
class LocalEmbeddingTestRequest(BaseModel):
    model_file: str = Field(min_length=1, max_length=200)
    n_ctx: int = Field(ge=256, le=8192)
    n_threads: int = Field(ge=0, le=128)
```

端点签名改为 `body: LocalEmbeddingTestRequest`。从 `_get_local_model_manager().status()` 找到与
`body.model_file` 相同且 status 为 `ready` 的条目；否则返回 `{ "ok": false, "message": "请先下载当前选择的本地模型" }`。
用 `LocalEmbeddingClient(active_model["model_path"], n_ctx=body.n_ctx, n_threads=body.n_threads or None)`
创建局部变量并调用 `await client.generate("SAG-plus local embedding health check")`。未知目录项抛出
`ValidationError("Unsupported local embedding model")`。不引用 `_local_client()`，不读或写 `settings.embedding_*`。

- [x] **步骤 4：运行 API 验证**

运行：

```bash
cd apps/api
.venv/Scripts/python.exe -m pytest tests/test_local_model_manager.py tests/test_embedding_backend.py -q
```

预期：全部通过。

- [x] **步骤 5：提交 API 变更**

```bash
git add apps/api/sag_api/schemas/system.py apps/api/sag_api/api/v1/system.py apps/api/tests/test_local_model_manager.py
git commit -m "feat(api): test draft local models"
```

### 任务 2：将设置页测试切换为草稿

**文件：**
- 修改：`apps/web/lib/types.ts`
- 修改：`apps/web/lib/api.ts`
- 修改：`apps/web/lib/local-model-manager.ts`
- 修改：`apps/web/lib/local-model-manager.test.ts`
- 修改：`apps/web/components/features/model-config-form.tsx`

- [x] **步骤 1：编写失败的草稿条件测试**

让 `isLocalEmbeddingTestDisabled` 接受 `embeddingProvider` 和 `modelFile` 字符串而不是 `cfg`。
断言下列未保存草稿仍可测试：

```ts
expect(isLocalEmbeddingTestDisabled("local", "bge-m3-Q6_K.gguf", readyQ6, null, false)).toBe(false);
```

并保留 API 模式、后端未就绪、草稿文件未就绪和操作进行中时禁用的断言。

- [x] **步骤 2：运行失败测试**

运行：

```bash
cd apps/web
npm run test:unit -- lib/local-model-manager.test.ts
```

预期：失败，因为函数目前读取 `cfg` 对象。

- [x] **步骤 3：实现草稿请求与启用条件**

在 `types.ts` 定义：

```ts
export interface LocalEmbeddingTestRequest {
  model_file: string;
  n_ctx: number;
  n_threads: number;
}
```

将 `api.testLocalEmbedding` 改为接受该对象并发送 JSON。将 helper 签名改为：

```ts
isLocalEmbeddingTestDisabled(
  embeddingProvider: "api" | "local",
  modelFile: string,
  status: LocalModelManagerStatus,
  action: LocalModelAction,
  testing: boolean,
): boolean
```

组件使用 `embProvider`、`embLocalModelFile`、`embLocalNCtx`、`embLocalNThreads` 调用 helper 和 API；
结果回退模型名也使用 `embLocalModelFile`。

- [x] **步骤 4：运行 Web 验证**

运行：

```bash
cd apps/web
npm run test:unit -- lib/local-model-manager.test.ts
npm run typecheck
npm run lint
npm run i18n:check
```

预期：全部通过。

- [x] **步骤 5：提交 Web 变更**

```bash
git add apps/web
git commit -m "feat(web): test unsaved local models"
```

### 任务 3：同步说明和回归

**文件：**
- 修改：`README.md`
- 修改：`README-CN.md`

- [x] **步骤 1：更新使用说明**

将“测试本地模型”说明改为：它测试当前下拉选择和推理参数，不需保存，也不修改已保存配置、
知识库或远程服务。

- [x] **步骤 2：完成跨层验证与推送**

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
git push origin main
```

预期：所有检查通过，`main` 推送到 GitHub。
