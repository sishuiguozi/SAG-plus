# SAG 全面优化分析 v2（2026-08，基于全量代码扫描）

> 分析范围：apps/api/sag_api 全部 107 个 .py + apps/web 全部 182 个非测试 .ts/.tsx + apps/desktop + zleap-sag 依赖。
> `mysag` 维护说明：本文记录本分支的实现状态；性能数字来自当时的本地基线，迁移到其他数据目录前必须重新评估。
> 本文档每个优化点均标注代码依据。优先级：P0=本次迭代实施，P1=下阶段，P2=可选/长期。

## 1. 现状基线（扫描确认）

### 后端（FastAPI + zleap-sag）
- 路由：auth/agents/attachments/documents/insights/jobs/openai/search/sources/system/translate/universe/activity
- 检索链路：`engine_manager.search/_search_raw/search_many/_search_chunk_vectors`（无缓存、每源锁、超时回退）
- **混合检索已有基础**：`services/retrieval_service.py` 的 `rerank_sections`（语义+词法融合、相关性门控）、`_lexical_sections`（词法通道）、`query_terms`（去噪）
- 向量写入：`vector_write_queue.py` 队列化、批处理、租约、幂等（SAG-OPT-103/105/106）
- 图谱/宇宙：`universe_service.py`(1487L) + `engine_manager` 的 universe_* 方法（singleflight + 缓存）
- 本地嵌入：`embedding_backend.py`（llama-cpp bge-m3 Q8_0，CPU）
- 结构感知分块：`chunking_compat.py`（代码块/表格保持完整，已完成）
- 应用层补丁共 9 个模块，详见 `docs/ARCHITECTURE_PATCHES.md`；升级上游依赖前先运行该表所列回归测试。

### 前端（Next.js + React + Three.js）
- 巨型单文件：`universe-scene-engine.ts`(7500L) `knowledge-universe.tsx`(4037L) `pet.tsx`(1628L) `pet-mini-workspace.tsx`(1493L) `orbital-graph-3d.tsx`(1466L) `conversation-runtime.ts`(1242L) `universe-timeline-window.ts`(1179L) `source-graph.tsx`(1069L) `detail-panel.tsx`(895L) `model-config-form.tsx`(863L)
- 已有优化：three/webgpu stub(-2.1MB)、虚拟滚动、图谱帧门控、代码分割、bundle 分析

### 桌面（Electron）
- dev 编排（dev.mjs）、EXE 运行时（runtime.ts）、启动等待 /ready、更新器

---

## 2. 优化点清单（含代码依据）

### A. 检索与 RAG 质量

| # | 优化点 | 代码依据/现状 | 方案 | 优先级 |
|---|---|---|---|---|
| A1 | ~~检索结果 TTL 缓存~~ **已完成** | `engine_manager` 新增 `_search_cache_*`，search/search_many 缓存 SearchOutcome，TTL 30s 可配（设置→知识库），深拷贝隔离 | — |
| A2 | ~~BM25 独立召回~~ **已完成** | 新增 `sag/lancedb_fts.py`：LanceDB FTS（tantivy）独立 BM25 召回 + 源过滤 + 索引自动维护；词法通道优先 FTS、失败回退 grep，且同步 FTS 操作转入 worker thread；设置页可开关（`lancedb_fts_enabled`） | — | — |
| A3 | ~~LLM Rerank~~ **已完成（默认关）** | `_llm_rerank`（候选编号重排+失败回退）；`search_llm_rerank_enabled/candidates` 设置项 | — | — |
| A4 | ~~父子分块~~ **已完成（增量启用）** | `chunking_compat` 只保证结构完整；无 parent_id 关联 | 新 `document_chunk_mode=parent_child`：父块聚合上下文 + 子块精确检索，`extra_data.parent_id` 关联（复用现有 JSON 字段，无 migration）；入库回填 + 检索父上下文（`sag_api/sag/parent_child.py`）；旧数据无标记自动跳过（无需重灌，仅新文档生效） | — |
| A5 | ~~检索评估集~~ **已完成** | `scripts/eval_retrieval.py`（内置用例、逐条延迟与命中率统计；当前 4 条基线为 3/4=75%） | — | — |
| A6 | ~~Regex 分块~~ **已完成** | `document_chunk_regex`（Python re）+ 设置页正则输入；代码块/表格仍受保护 | — | — |
| A7 | 图片/表格 VLM 理解 | 无多模态描述 | **已规划**：需 VLM 模型/API，属产品级改造 | P2 |

### B. 文档与入库

| # | 优化点 | 依据 | 方案 | 优先级 |
|---|---|---|---|---|
| B1 | 解析降级原因可见 | 文档详情 `document-parsing-details.tsx` 已展示解析器/进度/错误 | **基本已有**，可增强耗时展示 | P1 |
| B2 | ~~批量删除~~ **已完成** | `document-list.tsx` 新增批量删除（一次密码确认，逐文档删除并统计成败） | — | — |
| B3 | 断点续解析状态可见 | 文档详情已有 progress/状态展示 | **基本已有**，可增强恢复提示 | P2 |
| B4 | PDF 坐标溯源跳转 | 有 chunk 原文 | **已规划**：需 MinerU 坐标链路改造 | P2 |

### C. 性能与架构

| # | 优化点 | 依据 | 方案 | 优先级 |
|---|---|---|---|---|
| C1 | 后端大文件拆分 | 同上 | **已规划**：拆 engine/lifecycle/search/universe 子模块（重构风险高，需独立排期） | P1 |
| C2 | 前端巨型组件拆分 | `universe-scene-engine.ts`(7500L) 等 10 个 800L+ 文件 | **已规划**：按引擎/渲染/交互拆分（需独立排期） | P1 |
| C3 | ~~补丁治理~~ **已完成** | `docs/ARCHITECTURE_PATCHES.md` 集中登记 9 处补丁、回归测试和治理约定 | — | — |
| C4 | ~~ONNX 模型缓存清理~~ **已完成** | 已清理未使用的 Xenova 缓存，释放约 5.5GB；当前使用 llama-cpp 本地嵌入后端 | — | — |
| C5 | ~~检索延迟监控~~ **已完成** | engine 检索耗时计入 `performance_ring`，`/system/performance-metrics` 聚合 | — | — |
| C6 | 前端 bundle 继续瘦身 | 已修 three/webgpu | 分析 pet/echarts 等大依赖 | P2 |

### D. 可靠性与运维

| # | 优化点 | 依据 | 方案 | 优先级 |
|---|---|---|---|---|
| D1 | 测试覆盖 | 后端 ~60 用例 | 补检索缓存/分块/向量队列基准 | P1 |
| D2 | ~~备份自动化~~ **已完成** | `scripts/backup_data.py`（快照+保留 N 份+跳过可重建缓存） | — | — |
| D3 | ~~模型版本化~~ **已完成** | `local_embedding_status` 增加 `model_file`/`model_fingerprint`（size+mtime） | — | — |
| D4 | 崩溃自愈报告 | 队列恢复有 | **基本已有**（启动自检+恢复日志），UI 报告已规划 | P2 |

### E. UX 与功能

| # | 优化点 | 依据 | 方案 | 优先级 |
|---|---|---|---|---|
| E1 | ~~搜索命中高亮~~ **已有** | `search-panel-sections.tsx` 用 `highlightMatches` 高亮命中词 | — | — |
| E2 | ~~检索页 URL 状态化~~ **已完成** | `search-provider` 提交查询同步 `?q=`，进入页面自动恢复 | — | — |
| E3 | 文档标签 | 无标签体系 | **已规划**：需 DB schema+UI 设计 | P2 |
| E4 | ~~快捷键~~ **已有** | app-shell `Ctrl/Cmd+K`（搜索）`Ctrl/Cmd+J`（跳转） | — | — |

---

## 3. 路线图

### P0（本次迭代）✅
1. **A1 检索结果 TTL 缓存** — 已完成（`test_search_cache.py` 4 passed，设置页可配）
2. **C4 ONNX 缓存清理** — 已完成（释放 5.5GB）

### 已完成的优化（累计）
- A1 检索 TTL 缓存 / C4 ONNX 清理(5.5GB) / A2 词法通道修复 / B2 批量删除 / **A2b BM25 FTS 独立召回**
### 本批已完成（A3/A4/A5/A6/C5/D2/D3/E2 + 确认 E1/E4/B1/B3/D4 已有）
### 剩余（已规划，需独立排期/条件）
- ~~A4 父子分块~~ **已完成（增量启用）**；**A7 VLM 图片理解**（需模型）、**B4 坐标溯源**（MinerU 链路）
- **C1/C2 大文件拆分**（重构）、**E3 文档标签**（schema 设计）、**C6 bundle 分析**、**A2c BM25 中文分词优化**

### P1（下阶段，需规划评审）
A2 BM25+RRF / A3 Rerank / A5 评估集 / B1 / B2 / C1 / C2 / C3 / D1 / E1

### P2（长期）
A6 / A7 / B3 / B4 / C5 / C6 / D2 / D3 / D4 / E2 / E3 / E4

## 4. 验收标准
- A1：相同查询二次命中缓存（日志/指标验证），检索 P95 显著下降；开关可禁用
- C4：`models` 目录占用 ≤ 1GB
- 全部回归测试通过，检索/入库链路行为不变
