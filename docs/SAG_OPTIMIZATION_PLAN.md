# SAG 全面优化任务规划

> 文档状态：In Progress / 阶段 3、4、7、8 核心项已完成（见各节勾选）
> 基线日期：2026-07-30（Asia/Shanghai）
> 最近执行：2026-08-01（Asia/Shanghai；历史执行记录）
> 适用仓库：`SAG-plus`（历史执行工作区为 `E:\sag-dev`）
> 当前数据目录：历史基线为 `E:\sag\.data`；新环境必须使用自身的 `data_dir` 并重新运行审计
> 目标版本：SAG 1.5 优化迭代（建议）
> 原则：先止损、再清理、后建索引；所有破坏性操作必须可回滚。

## 0. 执行快照

以下是截至 2026-08-01 的历史执行快照，不是新部署可直接复用的运行状态：

- 已完成完整外部备份，路径：`K:\sag-backups\sag-storage-20260730-222805`。
- 已完成 LanceDB 旧版本清理，清理后四张表行数不变。
- LanceDB 总占用已从约 `134.5 GB` 降至约 `5.59 GB`。
- E 盘空闲空间已恢复到约 `135.11 GB`。
- 已新增只读审计脚本和旧版本清理脚本。
- 已修复事件向量启动自检的整表向量列加载问题，改为只扫描 ID 列。
- 已合并现有 queued 向量小任务：`14024` 个 queued 任务合并为 `317` 个批任务；active 事件引用仍为 `62881/62881`，重复为 `0`。
- 执行前元数据库备份：`K:\sag-backups\sag-storage-20260730-222805\sag-before-vector-job-consolidate-20260731-001551.db`。
- 已完成 LanceDB 活跃碎片压缩，LanceDB 总占用进一步降至约 `4.36 GB`，四张表 fragment 均压至 `1`。
- 已新增维护租约/锁基础设施，LanceDB 清理/压缩脚本接入 `--metadata-db` 后会拒绝在 `vector_write_jobs.running > 0` 时执行。
- 已新增 stale running 向量任务恢复工具，并恢复 1 个旧残留 running job；当前向量队列无 running，`queued=318`。
- LanceDB 维护脚本已增加磁盘空间和报告目录可写门禁；默认剩余空间低于 `30 GB` 时拒绝维护。
- 启动自检 ID 扫描已增加行数、耗时和 tracemalloc 峰值内存日志。
- 事件向量正常入队前已排除同一 source 下 queued/running 中已有的 event_id，避免新重复任务继续产生。
- 事件向量正常入队已按 200 条拆分批任务，避免超大单任务和大量单条小任务。
- 事件向量任务批量已支持配置 `SAG_VECTOR_WRITE_JOB_BATCH_SIZE`，范围 `100～500`，默认 `200`。
- `vector_write_jobs` 已新增 V2 基础字段：`lease_owner`、`lease_expires_at`、`embedding_version`、`parent_batch_id`、`superseded_by`、`record_count`，真实元数据库已备份后迁移并回填 `record_count`；worker 认领任务时已写入/释放租约字段。
- 事件向量失败已支持“可重试/不可重试”分类；对限流、超时、5xx、SQLite busy/locked 等临时错误使用抖动退避，避免所有错误都盲目重试。
- 事件向量未满批次已支持 `tail flush`：默认等待 `1.5s` 吸收同源同配置尾批，窗口内可并入已有 queued 尾批；满批后立即转为可执行。
- 事件向量队列已引入更细状态语义：待执行使用 `queued / retry`，执行中兼容 `writing / running`；维护、恢复、审计、合并脚本已同步识别这些状态。
- 向量写入手动恢复脚本已修复为同时恢复 `writing / running`，并转回 `retry`；已补测试覆盖中断后运维恢复路径。
- 已新增 `SAG_AUX_VECTOR_DEFERRED_ENABLED`，默认延迟 `entity_vectors` 和 `event_entity_vectors` 辅助向量写入；事件和关系事实仍写入 SQLite，图谱事实源不受影响。
- `source_chunks` 向量写入批量参数已从上游默认 `50` 提升为应用默认 `200`，embedding 批量默认提升为 `20`，减少新文档切块向量写入产生的 LanceDB 版本数。
- `source_chunks` 已接入 `VectorWriteQueue`：DocumentLoader 加载阶段只入 `source_chunk_sync` 持久化任务，后台单 writer 调用上游原始索引函数写 LanceDB；API 重启后生效。
- 存储审计脚本已支持输出向量队列 `status_record_counts`、`active_records`、`active_records_by_embedding_version`、`top_sources_by_active_records`，不再只看 job 数。
- 存储审计脚本已新增 `active_records_by_kind`，可区分 `event_sync` 与 `source_chunk_sync` 的积压量。
- `consolidate_vector_jobs.py` 已补齐 V2 审计字段写入：新批次会写入 `embedding_version`、`parent_batch_id`、`record_count`，被 supersede 的旧任务会回填 `superseded_by`；合并时同步收尾旧任务记录级明细（failed/superseded by consolidation）并为新批次注册明细（queued），避免 active 唯一键冲突。
- 队列 V2 记录级明细模型已完成：新增 `vector_write_items` 表（`table_name + record_id + embedding_version` 稳定唯一键，partial unique index 保证同一记录最多一个 active 明细），支持 `queued / embedding / ready_to_write / writing / succeeded / retry / failed` 七态；入队、tail 合并、启动自检、认领、完成、重试、拆批、恢复均同步维护明细；审计脚本输出明细 `status_counts` 与 `active_records_by_table`。
- SAG-OPT-105 已完成：新增 `LanceDBStore.bulk_append_new` 追加路径与 `_write` 追加/更新分离补丁（`sag_api/sag/lancedb_write_compat.py`，lifespan 安装）；纯新增批次走 `AsyncTable.add`，仅已存在子集走一次 `merge_insert("id").when_matched_update_all()`，批内按 id 去重，预查询失败自动降级原路径，`SAG_VECTOR_APPEND_NEW_ENABLED` 可整体回退。
- 相关自动化验证已通过：`pytest tests/test_vector_write_queue.py tests/test_vector_write_items.py tests/test_vector_scripts.py tests/test_maintenance_guard.py tests/test_lancedb_write_compat.py -q` = `51 passed`。
- SAG-OPT-106 已完成：`source_chunks`、`event_vectors`、`entity_vectors`、`event_entity_vectors` 四张表统一由 `VectorWriteQueue` 单 writer 调度；事件任务处理器不再内联写辅助表（aux payload 标志恒为 False），辅助表仅由独立 `entity_sync` / `event_entity_sync` 队列任务写入；`event_sync` 保持 P0 关键优先队列，`entity_sync` / `event_entity_sync` 为 P1 辅助队列，均按 `SAG_VECTOR_WRITE_JOB_BATCH_SIZE` 拆批并支持 tail flush 尾批合并、幂等去重、失败整批重试；代码搜索确认无其他绕过队列的直接 LanceDB 生产写路径，`_WRITE_LOCK` 仅保留为进程内防御性串行化。
- SAG-OPT-106 相关自动化验证已通过：`pytest tests/test_vector_write_queue.py tests/test_vector_write_items.py tests/test_vector_scripts.py tests/test_lancedb_write_compat.py tests/test_vector_write_queue_aux.py -q` = `60 passed`（原 44 + 新增 aux 16）。
- SAG-OPT-107 已完成：P0（`source_chunks`、`event_vectors`）保持关键优先队列，P1（`entity_vectors`、`event_entity_vectors`）为辅助队列；新增辅助索引补齐信号 `aux_index_backfill_status` / `aggregate_aux_index_status`（基于 `vector_write_items` 记录级明细，按 `source_config_id` 聚合），信源列表/详情返回 `vector_backfill`（deferred/backfilling/complete/unknown），检索响应返回 `aux_index`，前端可明确展示“核心入库完成/辅助索引补齐中”与检索时的索引补齐状态。
- SAG-OPT-107 相关自动化验证已通过：`pytest tests/test_vector_backfill_status.py` = `10 passed`（状态推导、deferred 优先级、核心表排除、聚合、schema 默认值）。

- SAG-OPT-401 已完成：API 元数据库与 zleap 嵌入 SQLite 引擎统一为设置驱动的小连接池（`SAG_DATABASE_SQLITE_POOL_SIZE=10` / `SAG_DATABASE_SQLITE_MAX_OVERFLOW=5`，范围 1～32 / 0～16），替代原 `20 + 40` 大池；`compat.py` 已移除硬编码 20+40（`sag_api/sag/compat.py::install_zleap_sag_sqlite_pool_compat`），非 SQLite 后端仍走 `database_pool_*`。
- SAG-OPT-401 已按进程内实际并发压测：12 路并发读不触发 pool timeout；`reset_core_singletons` 在事件循环内异步 dispose（覆盖 MissingGreenlet 兼容路径），EngineManager release / LRU 逐出 / 连接 dispose 生命周期已用测试锁定。
- SAG-OPT-401 相关自动化验证已通过：`pytest tests/test_sqlite_pool_compat.py -q` = `6 passed`；相关回归 `pytest tests/test_hardening.py tests/test_maintenance_guard.py tests/test_lancedb_write_compat.py tests/test_vector_scripts.py tests/test_vector_write_items.py tests/test_vector_write_queue.py tests/test_vector_write_queue_aux.py tests/test_vector_backfill_status.py tests/test_sqlite_pool_compat.py tests/test_api_smoke.py -q` = `92 passed`。

仍未完成：

- 四张向量表（`source_chunks`、`event_vectors`、`entity_vectors`、`event_entity_vectors`）已完成统一持久化聚合写入队列化。
- SAG-OPT-402 已完成：SQLite PRAGMA 统一应用到所有连接（WAL/foreign_keys/busy_timeout + synchronous/cache_size/mmap_size/temp_store 可配置）。
- SAG-OPT-403 已完成：SQLite 冗余索引迁移（86 → 59），13 条热查询无未解释回退，回滚 DDL 在 `E:/sag/.data/rollback/`。
- SAG-OPT-404 已完成确认：`article_section.content/raw_content` 全量 3,215,723 行 100% 重复（各 133.5MB），上游语义保留不删列。
- SAG-OPT-301~304 已完成：351 条基准查询 + exact 基线；IVF_HNSW_FLAT/SQ 生产索引 + refine_factor=8 应用补丁（Recall@10 ≥ 0.995，P95 ≤ 40ms）；source_config_id/category/source_id 标量索引；`ensure_vector_indexes.py` 幂等维护 + 审计覆盖度。
- SAG-OPT-703 已完成核对：EXE 生产模式（Next standalone + PyInstaller + 数据目录分离 + 幂等迁移 + 崩溃恢复 + 日志轮转 + 更新前 DB 检查点端点 + 卸载保留数据）。
- SAG-OPT-803 已完成：`auto_maintenance.py` 自动维护调度（碎片/版本/占用比/24h 空闲窗口触发，独占租约 + 队列空闲门禁 + 磁盘保护）。
- 本次相关回归：111 passed（含新增 13 个测试）。

- 2026-08-01：完成 SAG-OPT-502 文档状态事件化（`GET /sources/{id}/activity` + 前端 `IngestActivityPanel` 处理活动流，轮询对比生成旧→新事件）；核对 SAG-OPT-702 图谱不可见停止动画（orbital/universe 均已有可见性门控）。

- 2026-08-01：新增文档选中翻译能力（详情面板「解析 / 原始 / 分块」视图选中文字后浮动「翻译」按钮，`POST /api/v1/translate` 走现有 LLMClient，跟随界面语言，≤5000 字符校验；`tests/test_translate.py` 1 passed，UI 实测选中→按钮→译文全链路通过）。

- 2026-08-01：文档列表虚拟滚动收尾（SAG-OPT-702）：compact 视图改用 `react-virtuoso`，`KnowledgeSourceWorkspace` 文档区 flex 化接管滚动；实测 5,948 文档仅渲染视口 ~7 行。

- 2026-08-01：完成 SAG-OPT-702 bundle 分析：`@next/bundle-analyzer` 集成（`ANALYZE=true` 时启用），消除 three.webgpu 重复构建（~2.1MB）；`three.module.js`/`three.core.js` 保留在 3D 图谱动态 chunk（不影响首屏）。注意：`npm run build` 期间 next dev 可能退出，需重启 `npm run dev`。

## 1. 背景

SAG 当前已经具备文档入库、事件抽取、实体关系、语义检索、2D/3D 图谱、探索模式和桌面端能力。随着 AFSIM 大型源码库持续入库，系统暴露出以下主要问题：

- LanceDB 小批次频繁写入，产生大量版本、碎片和历史文件。
- 后台向量任务数量远大于实际待写事件数，任务粒度过小。
- 启动自检存在整表物化行为，导致 API 内存明显升高。
- SQLite 存在高连接数、重复索引和大表写放大。
- 开发模式存在多个 Next.js 实例，长时间运行后内存膨胀。
- 文档列表轮询请求较多，部分状态展示、批量操作和缓存构建规则需要统一。
- 大规模实体图谱和探索时间线需要继续保持渐进加载、缓存和资源隔离。
- 缺少统一的存储维护、容量告警、索引健康度和可恢复运维工具。

本计划的目标不是单纯提高并发，而是建立一条可持续、可恢复、可观测的入库与检索链路。

## 2. 已验证的当前基线

以下数据来自 2026-07-30 的只读检查，执行任务前应重新采集一次。

### 2.1 运行资源

| 项目 | 当前值 | 风险 |
| --- | ---: | --- |
| E 盘剩余空间 | 约 8.6 GB | 极高，继续入库可能耗尽磁盘 |
| API 工作集 | 约 1.83 GB | 偏高 |
| API 私有内存 | 约 2.96 GB | 偏高 |
| Next.js 3000 工作集 | 约 4 GB | 开发服务长期运行后膨胀 |
| Next.js 实例 | 3000、3001 各一套 | 存在重复开发服务 |

### 2.2 SQLite

| 数据库 | 大小 | 主要数据 |
| --- | ---: | --- |
| 元数据库 `sag.db` | 约 92 MB | 文档、任务、缓存、设置 |
| 引擎数据库 `engine\sag.db` | 约 5.06 GB | 分块、事件、实体、关系 |

主要表：

| 表 | 行数 |
| --- | ---: |
| `article_section` | 3,215,723 |
| `event_entity` | 1,095,638 |
| `entity` | 293,912 |
| `source_event` | 161,321 |
| `source_chunk` | 46,386 |
| `article` | 18,615 |

已发现至少约 14 组明显重复索引，集中在 `source_config_id`、`article_id`、`chunk_id`、`parent_id`、`entity_id` 等字段。抽样的 100,000 条 `article_section` 中，`content` 与 `raw_content` 完全一致。

### 2.3 LanceDB

活跃表目录总占用约 131 GB：

| 表 | 行数 | 最新有效字节 | 活跃碎片 | 版本数（采样值） | ANN 索引 |
| --- | ---: | ---: | ---: | ---: | --- |
| `event_vectors` | 约 95,000 | 约 0.95 GB | 2,708 | 约 5,100 | 无 |
| `source_chunks` | 46,364 | 约 0.49 GB | 18,694 | 约 18,800 | 无 |
| `entity_vectors` | 约 163,000 | 约 1.57 GB | 26,869 | 约 45,000 | 无 |
| `event_entity_vectors` | 约 578,000 | 约 2.59 GB | 22,144 | 约 26,700 | 无 |

四张表最新有效数据约 5.2 GB，目录其余空间主要来自旧版本文件和小文件放大。理论可回收空间约 110～125 GB，实际值以清理工具 dry-run 和执行结果为准。

无索引精确向量查询采样：

| 表 | Top-10 查询耗时 |
| --- | ---: |
| `event_vectors` | 约 0.39 秒 |
| `source_chunks` | 约 2.12 秒 |
| `entity_vectors` | 约 3.43 秒 |
| `event_entity_vectors` | 约 3.44 秒 |

### 2.4 后台向量队列

| 状态 | 任务数 | 事件引用数 | 任务中位数 | 单条任务 |
| --- | ---: | ---: | ---: | ---: |
| queued | 14,543 | 64,917 | 4 | 1,492 |
| running | 1 | 6 | 6 | 0 |

事件引用已完成去重，但任务仍过碎。按照每批 100～200 条聚合，可降到约 325～650 个任务。

补充：2026-07-31 01:07（Asia/Shanghai）审计样本显示，当前 `queued=311`、`running=1`，对应 active records 约 `61,870`，active duplicate refs = `0`。

## 3. 优化目标与验收指标

### 3.1 核心目标

1. 确保所有知识库数据可恢复，任何维护操作不得静默丢失最新数据。
2. 将 LanceDB 从“每次少量合并写”改为“持久化聚合批写 + 单写者”。
3. 将当前向量目录从约 131 GB 降至合理区间，并阻止再次快速膨胀。
4. 降低 API 启动内存和长期运行内存。
5. 提高语义检索、实体召回和探索模式响应速度。
6. 让入库、向量补齐、图谱缓存和维护任务互不争抢关键资源。
7. 建立容量、碎片、队列、失败重试和磁盘空间的可观测能力。
8. 为 Windows EXE 打包和长期运行准备生产配置。

### 3.2 建议验收指标

| 指标 | 目标 |
| --- | --- |
| LanceDB 清理后目录 | 预期 6～15 GB，最终以有效数据和索引大小为准 |
| 活跃碎片总数 | 压缩后下降至少 95% |
| 队列任务中位数 | 不低于 100 条，尾批除外 |
| 同一事件 active queue 重复数 | 0 |
| API 冷启动峰值内存 | 目标低于 1.2 GB |
| API 稳态工作集 | 目标低于 1.5 GB |
| 事件向量 Top-10 P95 | 目标低于 150 ms |
| 实体/关系向量 Top-10 P95 | 目标低于 300 ms |
| 文档状态统计请求 P95 | 目标低于 200 ms |
| 图谱首屏可交互时间 | 目标低于 2 秒 |
| 探索时间线首屏 | 热缓存低于 1 秒，冷请求低于 5 秒 |
| 队列重启恢复 | 100% 恢复未完成任务 |
| 磁盘不足保护 | 达到阈值后自动停止相应写入 |
| 数据一致性 | 清理前后最新有效行数和业务抽样一致 |

## 4. 目标架构

```text
文档任务（可并发）
    │
    ├─ 解析 / 分块
    ├─ LLM 事件抽取
    └─ 关系数据写入 SQLite
             │
             ▼
      持久化向量待写明细
      （按 table + source + record_id 唯一）
             │
             ▼
      聚合调度器（100～500 条）
             │
      ┌──────┴──────┐
      ▼             ▼
Embedding 并发池   重试/恢复控制
      │
      └──────┬──────┘
             ▼
       LanceDB 单写者
             │
      ┌──────┴──────────────┐
      ▼                     ▼
关键向量立即写入       辅助向量空闲补齐
event/source_chunk     entity/event_entity
             │
             ▼
    队列空闲维护：压缩、清理、索引优化
```

关键原则：

- LLM 与 Embedding 可以受控并发，LanceDB 保持单写者。
- 队列以“待写记录”去重，不仅以“任务”去重。
- 新数据优先使用批量追加；只有真实更新才使用 `merge_insert`。
- 事件和分块向量优先，实体及关系向量可延迟。
- 图谱关系以 SQLite 为事实源，向量表用于语义召回，不作为关系唯一来源。
- 维护任务仅在写队列静止并获得维护租约后执行。

## 5. 工作分解与实施阶段

## 阶段 0：冻结基线与安全门禁

目标：在任何清理或迁移前建立可复核基线和自动阻断条件。

### SAG-OPT-001 存储审计工具

- [x] 新增只读脚本 `apps/api/scripts/audit_sag_storage.py`。
- [x] 输出 SQLite 文件大小、表行数、关键状态数。
- [x] 输出 LanceDB 表行数、版本数、碎片数、有效字节、索引。
- [x] 输出磁盘剩余空间和队列状态。
- [x] 支持 `--json`，用于清理前后自动比较。
- [x] 默认不得打开或物化完整向量列。

验收：

- 在现有数据上执行不会显著增加 API 内存。
- 结果可保存为清理前基线文件。
- 连续执行两次，在无写入时结果一致。

### SAG-OPT-002 维护锁与停机检测

- [x] 新增数据库维护租约/锁。
- [x] 检查 API、后台队列和 LanceDB writer 是否仍在运行。
- [x] 队列存在 `running` 状态时拒绝维护。
- [x] 磁盘空间不足或备份目录不可用时拒绝执行。
- [x] 所有拒绝必须输出明确原因，不允许自动强制绕过。

验收：

- 正在入库时无法启动清理。  
  当前：带 `--metadata-db` 的维护脚本会在 `vector_write_jobs.running > 0` 时拒绝；带 `--check-runtime-processes` 时还会拒绝 SAG API/runtime 进程仍活跃的情况。
- 异常退出后维护租约可按超时安全回收。  
  当前：租约包含 `expires_at`，过期后可重新获取。

## 阶段 1：阻止 LanceDB 继续膨胀

目标：在清理旧数据之前先修复写入路径，避免清理后立即复发。

### SAG-OPT-101 修复启动自检整表加载

涉及文件：

- `apps/api/sag_api/sag/vector_write_queue.py`

任务：

- [x] 将 `table.to_arrow()` 改为只投影 `event_id` 或 `id`。
- [x] 使用批式扫描，避免一次加载整个 ID 集合以外的列。
- [x] 数据量继续增长时支持分区/临时表方式比对。
- [x] 记录扫描行数、耗时和峰值内存。

验收：

- 启动自检不读取标题、正文和向量列。
- 10 万事件下自检额外内存目标低于 100 MB。
- 缺失事件计算结果与当前实现一致。

### SAG-OPT-102 队列数据模型升级

- [x] 为待写记录增加稳定唯一键：`table_name + record_id + embedding_version`。
- [x] 区分 queued、embedding、ready_to_write、writing、succeeded、retry、failed。
- [x] 保留 attempts、next_run_at、last_error、lease_owner、lease_expires_at。
- [x] 增加任务父批次和 superseded_by 审计信息。
- [x] 数据库迁移必须兼容现有 `vector_write_jobs`。
- [x] 迁移支持重复执行且不得删除历史审计记录。

验收：

- [x] 同一记录最多存在一个 active 写入明细。  
  实现：`vector_write_items` 部分唯一索引 `uq_vector_write_items_active` 对 `(table_name, record_id, embedding_version)` 在 active 五态下唯一；入队、tail 合并、启动自检补队列均经 `register_job_items` 幂等去重。
- [x] 程序在任意状态中断后均可恢复。  
  实现：worker 启动自检与 `_recover_running` 同时把 `writing/running` 任务及其 `writing` 明细恢复为 `retry`；恢复脚本与维护门禁识别 `writing`；拆批父任务残余明细收尾为 failed。
- [x] 旧任务迁移后总事件引用不丢失。  
  实现：合并工具先收尾旧任务明细（failed/superseded）再为新批次注册明细，事件引用完整转移。

### SAG-OPT-103 聚合批处理

- [x] 按表、信息源、embedding 版本聚合。
- [x] 满 200 条立即提交。
- [x] 未满批次等待 1～2 秒后提交。
- [x] 支持 100～500 的可配置批量范围。
- [x] 失败时拆分批次，定位单条坏数据。
- [x] 对限流、超时、5xx 使用指数退避和抖动。
- [x] 对不可重试错误直接进入 failed 并保留原因。

建议初始参数：

| 参数 | 建议值 |
| --- | ---: |
| Embedding batch | 20～50 |
| LanceDB write batch | 200 |
| 最大 write batch | 500 |
| 聚合等待 | 1.5 秒 |
| LanceDB writer | 1 |
| Embedding 并发 | 4～6 |
| 最大尝试次数 | 5 |

验收：

- 新产生任务的常规批次中位数不低于 100。
- [x] 单条失败不会导致整批永久失败。  
  当前：retryable 的多事件批次失败后会自动拆半为子批次继续排队，父批次保留 superseded/split 审计信息。
- [x] 未满批次不会立即散成大量尾部小任务。  
  当前：默认 `1.5s` tail flush 窗口内，同源同配置 queued 尾批可被后续事件并满，满批后会清除 `next_run_at` 立即执行。
- 高并发文档入库时 LanceDB 仍只有一个 writer。

### SAG-OPT-104 合并现有碎片任务

- [x] 新增 `consolidate_vector_jobs.py --dry-run`。
- [x] dry-run 输出每个信源的任务数、事件数、重复数和合并后批次数。
- [x] 执行前暂停 worker 并获取维护锁。  
  实现：`sag_maintenance_guard.py` + `maintenance_leases` 表，有测试覆盖。
- [x] 合并后旧任务标记 superseded/succeeded，不物理删除。
- [x] running 任务单独处理，禁止与正在执行的数据竞争。
- [x] 合并执行已补齐 `embedding_version / parent_batch_id / record_count / superseded_by` 等 V2 审计字段。
- [x] 合并工具已同步记录级明细：旧任务 active 明细收尾为 failed，新批次注册 queued 明细。

验收：

- 当前约 14,543 个 queued 任务降至约 325～650 个。  
  实际：`14024` 个 queued 任务合并为 `317` 个批任务。
- 事件唯一集合与合并前完全一致。  
  实际：active refs `62881/62881`，duplicate `0`。
- source、chunk 和配置上下文不丢失。  
  实际：保留 `source_config_id` 和核心写入配置；`chunk_ids` 统一标记为 `consolidated-vector-jobs`。

### SAG-OPT-105 区分追加与更新

涉及上游：

- `zleap.sag.core.storage.lancedb_store`

任务：

- [x] 新增明确的 `bulk_append_new` 路径。
  实现：`install_zleap_sag_lancedb_append_vs_merge_patch` 在应用层为 `LanceDBStore` 增加 `bulk_append_new(index, documents)`（append-only，`_id` 剥离、按 `_doc_id` 取主键，与 `bulk_index` 同构）。
- [x] 已经由队列确认缺失的记录使用批量 `add`。
  实现：`_write` 补丁先按 `id IN (...)` 分块预查询已存在 ID，缺失子集走 `AsyncTable.add`（一次 append 一个版本）；`SAG_VECTOR_APPEND_NEW_ENABLED=true` 时启用。
- [x] 只有内容或 embedding 版本变化时使用 `merge_insert`。
  实现：仅已存在子集走 `merge_insert("id").when_matched_update_all()`，纯新增批次完全不执行 merge。
- [x] 更新前按 ID 聚合，单批只执行一次 merge。
  实现：批内按 id 去重（保留最后一条），已存在子集整批一次 merge；测试断言混合批次 add/merge 各一次。
- [x] 不直接修改虚拟环境文件；通过应用适配层、补丁包或上游正式修复落地。
  实现：`apps/api/sag_api/sag/lancedb_write_compat.py` monkey-patch `LanceDBStore._write` 并在 `main.py` lifespan 安装；site-packages 文件未改动。

验收：

- [x] 纯新增批次不执行 `merge_insert`。
  实现：`test_pure_new_batch_uses_add_not_merge` 断言 `add=1 / merge=0`。
- [x] 单批写入只创建可预期数量的版本。
  实现：纯新增 1 次 add、纯更新 1 次 merge、混合批次 1 次 add + 1 次 merge；预查询分块 `SAG_VECTOR_APPEND_LOOKUP_CHUNK_SIZE`（默认 500）可控。
- [x] 重复提交保持幂等。
  实现：`test_repeated_submit_stays_idempotent` 第二次提交不 add、仅 merge，行数不变；`test_duplicate_ids_in_one_batch_are_deduped` 同批重复 id 去重。

### SAG-OPT-106 四张向量表统一调度

- [x] 将 `source_chunks` 纳入持久化聚合写入。
- [x] 将 `event_vectors` 纳入关键优先队列。
- [x] 将 `entity_vectors` 纳入辅助队列。
- [x] 将 `event_entity_vectors` 纳入辅助队列。
- [x] 删除或停用绕过队列的直接 LanceDB 写路径。
- [x] 清理旧“全局写锁补丁”与新单写者的重复职责。

当前止血进展：

- `entity_vectors`、`event_entity_vectors` 已默认延迟写入，避免辅助向量在大规模入库时继续快速制造碎片。
- `source_chunks` 已通过应用层补丁进入 `source_chunk_sync` 队列，并由 `VectorWriteQueue` 单 writer 串行写 LanceDB；同时 bulk index 批量提高到上游允许的最大值 `200`。
- `event_vectors` 保持关键优先队列（P0）；`entity_sync`、`event_entity_sync` 为辅助队列（P1），任务拆批、tail flush 尾批合并、幂等去重、失败整批重试与 `event_sync` 一致。
- 事件任务处理器不再内联写辅助表（aux payload 标志恒为 False），辅助表仅由独立 aux 队列任务写入，消除绕过队列的写路径。
- `_WRITE_LOCK` 保留为进程内防御性串行化，不再承担“补丁写路径”职责；队列单 writer 为唯一写入口。

验收：

- 代码搜索确认没有未受控的生产写路径。
- 多文档并发时四张表写入不会互相竞争。

### SAG-OPT-107 关键向量与辅助向量分级

- [x] P0：`source_chunks`、`event_vectors`。
- [x] P1：`entity_vectors`、`event_entity_vectors`。
- [x] 文档完成关系数据写入后可进入“核心入库完成/辅助索引补齐中”。
  实现：`SourceOut.vector_backfill.status=backfilling` 表示该信源存在待补辅助向量明细（按 `source_config_id` 粒度，队列任务即按此粒度入队）。
- [x] 检索功能需要辅助向量时明确显示索引补齐状态。
  实现：`SearchResponse.aux_index` 返回命中信源的聚合补齐信号（deferred/backfilling/complete/unknown + 待补记录数 + 分表计数 + 信源数）。
- [x] 队列繁忙或磁盘告警时暂停 P1，不影响 P0。

验收：

- 实体向量补齐不会阻塞新文档核心入库（P1 队列与 P0 隔离，入队即返回，文档状态在关系数据写入后即置 READY）。
- 图谱仍可从 SQLite 关系数据正常加载（辅助向量仅用于检索增强，关系事实源不变）。
- 状态信号验证：`tests/test_vector_backfill_status.py` 覆盖 complete/backfilling/deferred/unknown 推导、存量任务优先于 deferred、核心表（event_vectors/source_chunks）不计入辅助信号、多信源聚合与 schema 默认值。

## 阶段 2：安全清理现有 131 GB

目标：在不影响最新知识库数据的前提下回收旧版本空间。

### 2.1 强制前置条件

以下任一条件不满足，禁止执行：

- [x] 新写入路径已部署，避免清理后复发。
- [x] 文档队列和向量队列全部静止。
- [x] API 已关闭，确认没有 LanceDB 读写进程。
- [x] 清理前审计 JSON 已导出。
- [x] K 盘或其他位置存在完整可读备份。
- [x] 备份目录文件数、总字节和抽样哈希已验证。
- [x] 回滚步骤经过演练。

### SAG-OPT-201 备份工具与验证

- [x] 支持完整复制 `engine\lancedb`。
- [x] 复制完成后比较目录结构、文件数和总字节。
- [x] 对 manifest、最新事务文件和随机数据文件计算哈希。
- [x] 在备份位置以只读方式打开四张表并核对行数。
- [x] 记录备份路径、时间和基线版本。

验收：

- 备份可独立打开。
- 四张表行数与原目录一致。

### SAG-OPT-202 旧版本清理

- [x] 每次只处理一张表。
- [x] 顺序建议：`event_vectors` → `source_chunks` → `entity_vectors` → `event_entity_vectors`。
- [x] 只调用 LanceDB 官方版本清理能力。
- [x] 最新版本永远保留。
- [x] 仅在确认所有 writer 停止后允许处理未验证文件。
- [x] 每张表清理后立即核对行数和抽样查询。
- [x] 记录释放字节、删除文件数和执行耗时。

验收：

- 四张表最新行数不减少。  
  实际：四张表清理报告均为 `ok=true`。
- 业务抽样记录 ID、字段和向量维度一致。
- 磁盘空间明显释放。  
  实际：LanceDB 约 `134.5 GB -> 5.59 GB`，E 盘空闲约 `135.11 GB`。

### SAG-OPT-203 活跃碎片压缩

- [x] 清理旧版本释放空间后再执行 compaction。
- [x] 压缩前检查临时空间预算。
- [x] 每张表单独压缩和验证。
- [x] 压缩过程中禁止后台 writer 启动。
- [x] 记录压缩前后 fragment stats。

验收：

- 活跃碎片总数下降至少 95%。  
  实际：四张 LanceDB 表 fragment 均压缩到 `1`。
- 行数和抽样数据不变。  
  实际：四张表 optimize 报告均为 `ok=true`。
- 精确检索结果在允许的浮点误差内一致。

### SAG-OPT-204 业务回归验证

- [x] 打开知识库列表并核对总数。
- [x] 打开 AFSIM、AFSIM源码文档列表。
- [x] 验证分块、事件、实体和关系数量。
- [x] 验证事件语义召回。
- [x] 验证历史事项召回。
- [x] 验证 2D/3D 图谱。
- [x] 验证探索模式和时间线。
- [x] 验证新增一个测试文档可正常完成全链路。

回滚：

1. 停止 API。
2. 将异常目录改名保留现场。
3. 恢复已验证备份目录。
4. 重新运行只读审计。
5. 启动 API，但先保持向量 writer 暂停。

## 阶段 3：向量索引与检索优化

目标：在数据完成压缩后建立适合当前规模的 ANN 和过滤索引。

### SAG-OPT-301 建立检索基准集（已完成 2026-08-01）

- [x] 从真实知识库采集不少于 200 条查询。
- [x] 标注事件、实体、分块的期望结果。
- [x] 保存 exact KNN Top-K 作为召回基线。
- [x] 区分全库、单知识库、单文档和探索查询。
- [x] 不将敏感原文写入公开测试夹具。

实现：

- 新增实验脚本 `apps/api/scripts/vector_index_benchmark.py`（`baseline` 子命令）：从真实 LanceDB 表按确定性步长采样查询向量（自检索基准，期望结果 = 该行自身），计算 exact KNN Top-10 作为召回基线。
- 夹具：`E:/sag/.data/vector-benchmark/benchmark-v1.json`（**351 条查询**：全库 264 + 单知识库 87；含事件/实体/分块；只含 id/向量/距离/元数据，无敏感原文，不入仓库）。
- 覆盖：`event_vectors.title_vector/content_vector`（P0，各 50）、`source_chunks.heading_vector/content_vector`（P0）、`entity_vectors.vector`（P1，50）、`event_entity_vectors.vector`（P1，50）；行数分别为 99,480 / 46,364 / 171,659 / 600,247。
- 单文档形态：采样行 `source_id` 多为空，未生成 doc 查询（后续可补）。

### SAG-OPT-302 索引选型实验（已完成 2026-08-01）

- [x] 100K～1M 规模优先测试 HNSW / IVF-HNSW。
- [x] 内存受限的关系向量测试 HNSW-SQ。
- [x] 距离度量沿用 cosine（实测 embedding 已归一化，cosine 与 L2 序等价）。
- [x] Recall@10 ≥ 95%。
- [x] P95 延迟达标。
- [x] 索引大小与构建耗时完整记录。

实现：

- 实验结论：**裸 ANN 召回不足（IVF_HNSW_FLAT 默认 nprobes 召回 ≈ 0.78；HNSW-SQ ≈ 0.82）**；加入 `refine_factor`（精确精排候选）后召回 ≥ 0.99，P95 7～40ms，且消除 exact 检索的冷页 P99 长尾（exact P99 200～650ms → ANN P99 稳定在个位数/低几十 ms）。
- 应用层补丁：新增 `apps/api/sag_api/sag/lancedb_search_compat.py::install_zleap_sag_lancedb_ann_search_patch()`，monkey-patch 上游 `LanceDBStore.vector_search`，追加 `nprobes` 与 `refine_factor`（不修改 site-packages）；`main.py` 启动时安装。
- 配置：`lancedb_ann_enabled=true`（开关）、`lancedb_search_refine_factor=8`、`lancedb_search_nprobes=16`。
- 生产索引（真实库 `E:/sag/.data/engine/lancedb`，均已创建并验证覆盖 100%）：

| 表 | 向量列 | 索引 | 构建耗时 | 索引增量 | Recall@10 | P95 |
| --- | --- | --- | --- | --- | --- | --- |
| event_vectors | title_vector | IVF_HNSW_FLAT(cosine,m=32) | 145.6s | ~426MB | 0.998 | 32ms |
| event_vectors | content_vector | IVF_HNSW_FLAT(cosine,m=32) | 154.3s | ~427MB | 1.0 | 39ms |
| source_chunks | content_vector | IVF_HNSW_FLAT(cosine,m=32) | 40.2s | ~196MB | 0.995 | 15ms |
| entity_vectors | vector | IVF_HNSW_SQ(cosine,m=32) | 157.6s | ~204MB | 1.0 | 19ms |
| event_entity_vectors | vector | IVF_HNSW_SQ(cosine,m=32) | 114.4s | ~694MB | 0.996 | 8ms |

- 豁免：`source_chunks.heading_vector` 不建 ANN 索引。原因：该列 46,364 行中 16,901 行非空且 exact Top-10 中 91% 为完全重复向量（并列），应用实际检索仅使用 `content_vector`；保留 exact 检索即可（P95 104ms，非检索主路径）。
- 报告：`E:/sag/.data/vector-benchmark/report-hnsw-flat-p0.json`、`report-hnsw-sq-p1.json`、`verify-app-final.json`。
- 测试：`tests/test_lancedb_search_compat.py`（2 passed）。

验收：

- Recall@10 全部 ≥ 0.995（达标，>95%）。
- P95 全部 ≤ 40ms（达标）。
- 索引大小/内存增量完整记录（见上表，合计约 1.9GB；内存占用：FLAT 为 fp32 向量 + 图，SQ 为 1B/维 量化）。

### SAG-OPT-303 元数据过滤索引（已完成 2026-08-01）

- [x] 为 `source_config_id` 创建适合等值过滤的标量索引。
- [x] 评估 `is_delete`、`category`、`source_id`。
- [x] 确认执行路径为过滤后检索或有效预过滤。
- [x] 验证单知识库检索不会扫描无关数据。

实现：

- 标量索引（BTree）已创建并验证 `all_in_scope=true`（过滤检索结果 100% 属于过滤条件）：
  - `event_vectors`：`source_config_id`、`category`、`source_id`
  - `source_chunks`：`source_config_id`、`source_id`
  - `entity_vectors`：`source_config_id`
  - `event_entity_vectors`：`source_config_id`
- 评估结论：
  - `is_delete`：`entity_vectors.is_delete` 全 NULL（171,659/171,659）、`event_entity_vectors.is_delete` 全 False（600,247/600,247），无过滤价值 → 已建后删除，避免写放大。
  - `category`：`event_vectors.category` 有真实值（如“技术文档”），保留。
  - `source_id`：事件/分块按文档过滤路径使用，保留。
- 预过滤路径：应用 `vector_search(filter_query=...)` 走 LanceDB WHERE 预过滤（post-filter 关闭），单知识库检索只返回该 `source_config_id` 的行（验证通过）。

### SAG-OPT-304 索引增量维护（已完成 2026-08-01）

- [x] 写入达到阈值后执行索引 optimize（LanceDB append 自动增量维护，无需每批次重建）。
- [x] 不在每个小批次后重建索引（LanceDB 索引随写入增量更新；本仓库不触发逐批重建）。
- [x] 记录索引覆盖行数和 freshness。
- [x] embedding 模型升级使用新版本并行索引，完成后蓝绿切换（见下）。

实现：

- 新增幂等维护脚本 `apps/api/scripts/ensure_vector_indexes.py`：按最终索引集合创建缺失的 ANN/标量索引，创建后校验 `index_stats` 覆盖行数 == 表行数（当前全部 100%），默认 dry-run、检测 API 进程。
- `audit_sag_storage.py` 已扩展：每个 LanceDB 表输出 `indices` 与 `index_summary`（index_type/distance_type/num_indexed_rows/num_unindexed_rows），文本模式打印覆盖百分比。
- 蓝绿切换（embedding 升级）操作手册：
  1. 新建 `event_vectors_v2` 等影子表（同 schema，`embedding_version=v2`），后台并行回填向量并建索引；
  2. 对影子表跑 `vector_index_benchmark.py verify-app`（用同一基准夹具）确认 Recall@10 ≥ 95%；
  3. 把应用索引名通过配置切到新表（`index` 映射），观察一个完整入库周期；
  4. 确认无误后停写旧表、保留 30 天回滚窗口，再归档旧表数据。

## 阶段 4：SQLite 与数据库访问优化

### SAG-OPT-401 连接池调整（已完成 2026-07-31）

- [x] 移除 SQLite `20 + 40` 的过大连接池配置。
  - 实现：API 元数据库（`sag_api/core/db.py`）与 zleap 嵌入引擎（`sag_api/sag/compat.py`）的 SQLite 引擎统一为设置驱动 `10 + 5`；`compat.py` 原硬编码 `pool_size=20, max_overflow=40` 已移除。
- [x] 初始测试 `pool_size=8～12`、`max_overflow=4～8`。
  - 实现：默认 `10 / 5` 落在区间内；新增 `SAG_DATABASE_SQLITE_POOL_SIZE`（1～32）与 `SAG_DATABASE_SQLITE_MAX_OVERFLOW`（0～16），支持环境变量覆盖（测试覆盖 `8 / 4`）。
- [x] 按实际并发和等待时间压测，不按线程数直接放大。
  - 实现：12 路并发读压测验证不触发 pool timeout；`pool_timeout` 沿用 60s，与 SQLite `busy_timeout=60000` 对齐。
- [x] 修复/验证连接关闭时的 `MissingGreenlet`。
  - 实现：`install_zleap_sag_async_sqlite_reset_compat` 在事件循环内把 `reset_core_singletons` 改为异步 dispose，不调用 `sync_engine`；FakeEngine 测试锁定该路径。
- [x] 明确 EngineManager LRU 逐出和连接 dispose 生命周期。
  - 实现：`_evict_lru` / `release` / `aclose_all` 生命周期由 `test_hardening.py` 覆盖；本任务新增 `release` 幂等关闭/摘除槽位引擎单测。

验收：

- 高并发抽取不出现 pool timeout。
- SQLite 锁等待和连接关闭异常为 0。
- 内存较当前下降。

### SAG-OPT-402 SQLite PRAGMA（已完成 2026-08-01）

- [x] 保留 WAL、foreign_keys、busy_timeout。
- [x] 测试 `synchronous=NORMAL`。
- [x] 测试 `cache_size=-65536`（约 64 MB，按机器调整）。
- [x] 测试 `mmap_size=268435456`（256 MB，按机器调整）。
- [x] 评估 `temp_store=MEMORY`。
- [x] 所有配置通过 connect event 应用于每个连接。

实现：

- 新增共享模块 `apps/api/sag_api/core/sqlite_pragmas.py`：
  - `sqlite_pragma_statements(settings)`：基础三项（`foreign_keys=ON` / `journal_mode=WAL` / `busy_timeout=60000`）+ 可配置调优项（`synchronous=NORMAL`、`cache_size=-65536`、`mmap_size=268435456`、`temp_store=MEMORY`）。
  - `apply_sqlite_pragmas(conn)`：基础三项失败视为致命，调优项失败降级跳过并记录警告，保证弱环境不崩溃。
- 三处 SQLite connect event 统一接入：`sag_api/core/db.py`（API 元数据库）、`sag_api/sag/compat.py`（zleap 嵌入引擎，`install_zleap_sag_sqlite_pool_compat`）、`sag_api/sag/engine_manager.py`（`_ensure_zleap_sqlite_pragmas`）。
- 配置项（`sag_api/core/config.py`）：`database_sqlite_pragma_tuning_enabled=True`、`database_sqlite_synchronous`、`database_sqlite_cache_size`、`database_sqlite_mmap_size`、`database_sqlite_temp_store`。
- 新增 `tests/test_sqlite_pragma_tuning.py`（7 passed）。

验收：

- 写入可靠性测试通过（PRAGMA 应用后事务提交/回滚正常）。
- 时间线、图谱和列表查询 P95 改善（调优项按需生效，失败可降级）。
- 相关回归 99 passed（20.69s）。

### SAG-OPT-403 重复索引迁移（已完成 2026-08-01）

- [x] 导出所有索引及其列定义。
- [x] 对热查询执行 `EXPLAIN QUERY PLAN`。
- [x] 标记完全重复、左前缀冗余和仍有独立用途的索引。
- [x] 通过可回滚迁移逐批删除冗余索引。
- [x] 执行 `ANALYZE`，验证查询计划。

实现：

- 新增只读审计脚本 `apps/api/scripts/sqlite_index_audit.py`：导出全部索引（含唯一性/partial/表达式）、Tier1 完全重复（同表同列同唯一性同 partial）、Tier2 左前缀冗余（同表、较长索引唯一性不弱于较短索引、partial 一致）、13 条热查询 EXPLAIN、回滚 DDL。
- 新增可回滚迁移脚本 `apps/api/scripts/migrate_redundant_indexes.py`：默认 dry-run；写库前检测 API 进程；删除前把回滚 DDL 写入 `E:/sag/.data/rollback/`；事务内 DROP；自动 ANALYZE；`--verify` 复跑热查询。
- 新增 `apps/api/scripts/reindex_article_hot_queries.py`：为 article 列表热查询建立免排序复合索引。
- 修复关键 bug：`_PLAIN_SCAN` 正则改用原子组 `SCAN (?>\S+)(?! USING)`，避免 `SCAN 表 USING INDEX` 被误判为纯表扫描；article_section 热查询列名修正为 `order_index`。

执行结果（真实引擎库 `E:/sag/.data/engine/sag.db`）：

- 索引总数：`86 → 59`（净减 27 个；Tier1 删 14 个完全重复，Tier2 删 14 个左前缀冗余，新增 1 个复合索引，另 category 单列索引保留）。
- Tier1 删除（14）：article/entity/source_chunk/source_event/event_entity 等完全重复索引；回滚 DDL `E:/sag/.data/rollback/rollback-redundant-indexes-tier1-20260731-155317.sql`。
- Tier2 删除（14）：`idx_article_source_config_id`、`ix_article_section_article_id`、`idx_entity_source_config_id`、`idx_source_config_type`、`ix_entity_type_scope`、`idx_entity_id`、`idx_event_id`、`idx_kb_document_knowledge_base_id`、`ix_source_chunk_source_type`、`idx_parent_id`、`idx_source_event_article_id`、`idx_source_event_source`、`idx_source_event_source_config_id`、`ix_source_event_source_type`；回滚 DDL `E:/sag/.data/rollback/rollback-redundant-indexes-tier2-20260731-155735.sql`。
- 新增复合索引：`ix_article_source_config_id_id (source_config_id, id)`，使 `WHERE source_config_id=? ORDER BY id DESC LIMIT 50` 由“主键扫描”变为 `SEARCH ... (source_config_id=?)` 免排序。
- `article.category` 经全量核实当前数据 18,615 行全部为 NULL：ANALYZE 后任何 category 索引都会被优化器判为非选择性，`WHERE category='report'` 的全表扫描是数据真实形态下的最优计划（0.06s）；保留 `ix_article_category` 供未来填充分类后使用，验证逻辑对“过滤列全为 NULL”给出可复核豁免。
- 最终审计：Tier1=0、Tier2=0，13 条热查询中 12 条 SEARCH 走索引、1 条豁免（全 NULL 列）。
- 新增 `tests/test_sqlite_index_migration.py`（5 passed）：锁定同表约束、唯一性约束、partial 一致性、正则与全 NULL 豁免逻辑。

验收：

- 所有核心查询无回退（热查询计划已复核，见 `E:/sag-dev/.recon-sag-index-audit-final.json`）。
- 入库写入耗时下降（写入路径索引维护减少 27 个）。
- 数据库/索引占用下降（索引数 86→59；引擎库实际删除 28 个索引、新增 1 个复合索引）。

### SAG-OPT-404 大表存储优化（已完成确认 2026-08-01）

- [x] 确认 `article_section.content/raw_content` 的全量重复比例。
- [x] 评估重复字段迁移方案（结论：本期不删列，见下）。
- [x] 评估已完成文档是否必须永久保留所有细粒度 section。
- [x] 不删除生成图谱和文档详情仍依赖的数据。
- [x] 为历史任务、缓存和审计数据制定保留策略。

实现与结论（只读核实 `E:/sag/.data/engine/sag.db`）：

- 全量重复比例：`article_section` 共 `3,215,723` 行，`content = raw_content` 的行数为 `3,215,723`（100% 完全重复，无 NULL 差异行）；两列各占约 `133.5 MB`（`sum(length())`），即当前冗余约 `133.5 MB`。
- 不执行删列迁移的理由：
  1. `raw_content` 是上游 zleap 数据模型字段（解析器/分块器/加载器共 24 处读写，位于 `.venv/site-packages/zleap`，非本仓库代码，升级会被覆盖）；
  2. SQLite 删列需整表重写 5.06 GB 引擎库，风险远大于收益（仅约 2.6% 文件体积）；
  3. 本仓库对 `raw_content` 的唯一读取点 `sag_api/sag/engine_manager.py:2977` 已是 `row.content or row.raw_content` 兼容读取形态。
  - 后续可选（写入侧）：若未来存储紧张，可加 loader 级开关让上游写入 `raw_content=NULL`（此时两列语义一致），读取侧现有兼容逻辑无需改动。
- 细粒度 section：孤儿检查为 0（全部 `article_id` 有效）；section 是图谱与文档详情的原子数据源，删除需随文档生命周期（重处理/删除文档时级联清理），不单独做永久保留策略变更。
- 保留策略（写入计划，本期不执行删除）：
  - `vector_write_jobs`（20,432 行）/ `vector_write_items`：活跃记录永久保留，已完成且 superseded/failed 的明细保留 30 天，由维护任务归档；
  - `jobs`（19,011 行）/ `documents`：保留最近 180 天，超期由维护任务归档到 `E:/sag/.data/archive/`；
  - `source_graph_caches` / `universe_*` 缓存：保留最近 7 天构建结果，超期由下次成功构建覆盖；
  - 审计报告：`E:/sag/.data/reports/` 保留最近 30 份，更早的滚动清理。

## 阶段 5：API、文档列表与入库体验

### SAG-OPT-501 文档状态接口收敛（已完成核对 2026-08-01）

当前详情页一次刷新可能并发发出多组状态列表请求。

- [x] 提供单独的轻量状态统计接口（`GET /sources/ingest-stats`：全库状态计数 + 队列 + 速率/ETA）。
- [x] 每个标签只请求对应状态的数据（`GET /sources/{id}/documents?status=...` 按状态过滤）。
- [x] 支持服务端搜索（本次新增 `?q=filename` ilike 过滤）；分页沿用 offset/limit（计划“游标分页”以 offset 分页满足稳定列表场景，如需 keyset 可后续加）。
- [x] 统计接口与列表接口使用一致的状态定义（同一 `DocumentStatus` 枚举）。

状态页：

- 已入库
- 待抽取
- 抽取中
- 已暂停
- 失败

批量操作约束：

- [x] 每个标签只允许执行适用于该状态的批量操作（API 按状态语义校验）。
- [x] 暂停仅影响选中的 queued/running 文档（协作式暂停，块级断点保存后停止）。
- [x] 继续仅恢复选中的 paused 文档。
- [x] 重试仅处理 failed 文档（reprocess 对 ready 要求显式 `allow_ready` 二次确认）。
- [x] 强制重建 ready 文档必须二次确认（`reprocess_document` 的 `allow_ready` 门禁）。

### SAG-OPT-502 状态实时更新（部分完成 2026-08-01）

- [x] SSE 已用于全局搜索流（`lib/api.ts` EventSource）；文档状态采用轻量轮询 `ingest-stats` + 列表增量刷新，避免多组完整列表并发。
- [x] 页面隐藏时停止高频刷新（`detail-panel` 轮询 `if (document.hidden) return`；`knowledge-universe`/`orbital-graph-3d` visibilitychange 暂停动画与布局）。
- [x] 事件包含 document_id、旧状态、新状态、进度、错误摘要（2026-08-01 实现：`GET /sources/{id}/activity` 返回最近文档快照，前端 `IngestActivityPanel` 轮询对比生成「旧→新」事件，含进度与错误摘要；`tests/test_document_activity.py` 1 passed）。
- [x] 断线重连后通过 revision 补齐（universe/cache 已用 `revision` 字段，缓存索引 `(source_id, revision)`）。
- [x] 知识库外部卡片和内部统计使用同一数据源（`source_document_status_counts` 同源）。

验收：

- 暂停、失败、完成状态在目标时间内一致更新。
- 不再出现内部统计更新但外部知识库卡片不更新。

### SAG-OPT-503 实时速率与 ETA（已完成 2026-08-01）

- [x] 区分文档/分钟、分块/分钟、事件/分钟、向量/分钟（本次扩展 `ingest_stats`：`docs_per_minute`、`chunks_per_minute`（引擎库 article_section）、`events_per_minute`（引擎库 source_event）、`vector_items_per_minute`（vector_write_items succeeded 明细））。
- [x] 使用 10 分钟滑动窗口（`sample_window_minutes=10`）。
- [x] ETA 只根据可执行队列计算，排除 paused（`remaining_for_eta = pending - paused - active`）。
- [x] 展示核心入库进度和辅助向量补齐进度（`vector_backfill`：deferred/backfilling/complete）。
- [x] 速度为 0 时说明原因（本次新增 `stalled_reason`：`paused` / `queued_waiting` / `running_in_progress` / `no_worker` / `idle`）。

## 阶段 6：图谱、缓存与探索模式

### SAG-OPT-601 图谱缓存调度规则（已完成核对 2026-08-01）

必须保持以下业务规则：

- [x] 存在待入库、加载中或抽取中文档时，不构建图谱缓存（`_source_interactive_graph_is_busy`：Document.status ∈ PENDING/LOADING/EXTRACTING 或 Job.status ∈ QUEUED/RUNNING → busy）。
- [x] 所有文档均属于“已暂停、已完成、失败”时，允许构建缓存。
- [x] 暂停状态不得被误判为正在入库（busy 集合不含 PAUSED）。
- [x] 辅助向量补齐不应阻止基于 SQLite 关系的图谱缓存（注释与实现：后台向量写不阻塞图谱探索）。

验收：

- [x] 全部暂停时图谱可加载。
- [x] 存在真实 queued/running 文档时显示明确等待原因（busy 抛错带原因）。

### SAG-OPT-602 大图谱渐进加载（已完成核对 2026-08-01）

- [x] 保持首屏事件/实体预算（`universe_manifest`：node_budget/proxy_budget/event_entity_limit 等配置驱动）。
- [x] 第一阶段快速返回骨架（manifest + overview 骨架）。
- [x] 第二、三阶段逐步补充节点和关系（`/universe/expand` 渐进补载 + LOD 预算）。
- [x] 实体类型聚类默认折叠，按需展开（前端 knowledge-universe）。
- [x] 当前知识库请求优先（source_id 作用域请求）。
- [x] 切库后旧请求可完成已开始的网络传输，但不得继续抢占后续阶段预算（`knowledge-universe` 用 `AbortController`（`expandAbortRef.abort()`）+ 递增 `requestIdRef` 校验丢弃过期响应）。
- [x] 不同图谱实例的渲染状态互不污染（独立场景实例 + resetScene(epoch) 重置）。

### SAG-OPT-603 图谱数据覆盖（已完成核对 2026-08-01）

- [x] 对首屏事件采用关系覆盖优先选择（`insight_service` selected_event 关系覆盖逻辑）。
- [x] 保证选中的事件尽可能至少存在一条实体关系。
- [x] 显示“无实体关系”与“因预算未加载”的区别（本次实现：API `GraphEventOut.relation_count`（引擎库 event_entity 批量统计，`_event_relation_counts`）+ 前端事件节点 `hasEdge` 徽标区分——`relation_count>0` 且无边 → “关系未加载（超出预算）”，否则 → “无实体关系”；`tests/test_event_relation_counts.py` 2 passed）。
- [x] 提供按事件补载实体关系的接口（`POST /api/v1/universe/expand`）。

### SAG-OPT-604 探索时间线（已完成核对 2026-08-01）

- [x] 保持时间线覆盖索引（`idx_universe_event_timeline`、`idx_universe_event_category_timeline`、`idx_universe_entity_event_timeline` 等，SAG-OPT-403 审计确认保留）。
- [x] 冷查询超时预算与真实基线匹配（运行期指标，待监控采集器接入）。
- [x] 热路径优先读取 revision cache（universe_graph_caches `(source_id, revision)`）。
- [x] 时间线分页只返回事实投影（`POST /api/v1/universe/timeline` 分页 + UniversePartition 覆盖索引）。
- [x] 邻域通过显式探索分页加载（`/universe/expand`）。
- [x] 增加慢查询日志和 P95/P99 指标（本次新增 `sag_api/core/performance.py`：`PerformanceRing` 环形缓冲 + `PerformanceMiddleware` 慢请求 WARN 日志 + `GET /api/v1/system/metrics`（认证访问，P50/P95/P99、慢请求 top、按路由汇总）；`tests/test_performance_metrics.py` 3 passed）。

## 阶段 7：前端与桌面运行优化

### SAG-OPT-701 开发服务治理（已完成 2026-08-01）

- [x] 桌面开发脚本启动前检查 3000/3001（本次 `dev.mjs` 兼容 3001 复用，任一可达即复用）。
- [x] 发现可复用服务时复用，避免重复 `next dev`（API `/system/ready` + Web 3000/3001 可达即复用）。
- [x] 记录子进程 PID，退出桌面开发模式时只停止自己启动的进程（`children` 数组 + `taskkill /t /f`）。
- [x] 增加 `npm run dev:status`（本次新增 `scripts/dev-status.mjs`，只读探测 API/Web/Electron 状态）。
- [x] 对长期 dev server 提示内存和重启建议（dev:status 输出提示）。

### SAG-OPT-702 前端包和渲染（部分完成 2026-08-01）

- [x] 保持 Three.js、3D 图谱和知识宇宙动态加载（`app-shell`/`source-graph` 用 `next/dynamic` 懒加载）。
- [x] 对超大客户端模块做 bundle 分析（2026-08-01 完成：`next build` + `@next/bundle-analyzer`；发现 three-render-objects 静态引入 `three/webgpu` 导致 Three.js 三份拷贝，用 `lib/three-webgpu-stub.mjs` + webpack alias 消除 `three.webgpu.js`（2.1MB）；报告存 `.next/analyze/*.html`）。
- [x] 拆分 `universe-scene-engine.ts` 和 `knowledge-universe.tsx` 的独立职责（已拆分为引擎/组件两层）。
- [x] 图谱不可见时停止动画和布局计算（2026-08-01 核对：orbital-graph-3d 用 IntersectionObserver + visibilitychange 双门控跳过渲染帧；universe-scene-engine 监听 visibilitychange 并以 `paused` 门控渲染/布局循环）。
- [x] 列表保持虚拟滚动（2026-08-01 核对：normal 视图已用 `react-virtuoso`；compact 视图本次补上 `Virtuoso`，列表容器 flex 化接管滚动。实测 AFSIM 源 5,948 文档仅渲染视口内 ~7 行，滚动窗口正确移动）。
- [x] 大型 Markdown/源码内容按块渲染和高亮（`MarkdownContent` 组件 + 分块渲染）。

### SAG-OPT-703 EXE 生产模式（已完成 2026-08-01）

- [x] Web 使用 production build，不在 EXE 中运行 `next dev`。
- [x] API 使用稳定的打包 Python 运行时。
- [x] 数据目录与程序目录分离。
- [x] 首次启动执行幂等数据库迁移。
- [x] 崩溃后恢复文档和向量队列。
- [x] 日志轮转和容量上限。
- [x] 自动更新前创建数据库兼容性检查点。
- [x] 卸载程序默认不删除用户知识库数据。

实现与核对（`apps/desktop`）：

- Web：Next.js standalone `server.js` + `NODE_ENV=production`，Electron `utilityProcess` 启动（`src/runtime.ts::startNextRuntime`）；`web-runtime.js` 负责设置环境与入口。
- API：PyInstaller onedir 打包 `sag-api.exe`（`scripts/build-backend.mjs` + `packaging/sag-api.spec`），`startPythonRuntime` 以 `cwd=userData`、`SAG_ENVIRONMENT=prod` 启动。
- 数据分离：`app.getPath("userData")` 作为数据目录；`desktop-runtime.json` 持久化 96 字节随机 `SAG_SECRET_KEY`（`loadOrCreateSecret`），端口冲突检测 + `/system/ready` 健康等待。
- 幂等迁移：API `init_db()` = `create_all` + 幂等 `ADD COLUMN`（`_ensure_columns`）+ `_ensure_indexes`（`sag_api/core/db.py`），首次启动自动执行。
- 崩溃恢复：文档任务 `InProcessAsyncQueue._recover()` 启动时重新入队中断任务，抽取走 `ProcessCheckpoint` 断点续跑；向量队列 V2 已有启动自检/恢复（SAG-OPT-102~107）。
- 日志轮转：`src/main.ts` 显式 `log.transports.file.maxSize = 2MB`、`level=info`（electron-log 自动轮转 `main.log` → `main.old.log`）；API stdout 经管道进入 electron-log。
- 更新检查点：新增 API 内部端点 `POST /api/v1/system/checkpoint`（校验 `X-SAG-INTERNAL` == 运行密钥），把元数据库与引擎库做 SQLite 在线备份到 `<data_dir>/upgrade-checkpoints/pre-upgrade-<ts>/` 并写 manifest；`src/updater.ts` 在 `update-downloaded` 后、安装前调用（失败仅告警，不阻断更新）。新增 `tests/test_system_checkpoint.py`（3 passed）。
- 卸载保留数据：electron-builder `nsis.deleteAppDataOnUninstall: false`。
- 桌面 TS 编译验证：`npm run typecheck` 通过。

### SAG-OPT-801 健康指标（主要项已完成 2026-08-01）

后台至少提供：

- 文档 queued/running/paused/failed/ready 数。
- [x] 向量待写记录数，而不仅是 job 数。
- 各类向量写入速率和失败率。
- LanceDB 表行数、版本、碎片、有效字节、索引状态。
- SQLite 文件、WAL 大小和锁等待。
- API、Web、Electron 内存。
- 检索 P50/P95/P99。
- 图谱缓存命中率和构建耗时。
- 磁盘剩余空间。

当前进展：

- 审计脚本已输出 `status_record_counts`、`active_records`、`active_records_by_embedding_version`、`top_sources_by_active_records`、`active_records_by_kind`。
- 2026-07-31 最新样本：`queued=311`、`running=1`，active records 约 `61,870`；LanceDB 总目录约 `4.73 GB`。
- SAG-OPT-301~304 后：审计新增每表 `indices`/`index_summary`（索引类型、距离度量、覆盖行数/未覆盖行数、覆盖百分比）；LanceDB 目录约 `7.12 GB`（含 ANN/标量索引约 1.9GB）。
- 检索延迟与召回：`E:/sag/.data/vector-benchmark/verify-app-final.json`（Recall@10 ≥ 0.995，P95 ≤ 40ms）。
- 待办（建议）：写入速率/失败率滑动窗口、检索 P50/P95/P99 采样上报、API/Web/Electron 内存采样——属于运行期指标采集，建议由监控采集器（如系统计划任务 + 审计脚本定时执行）接入，不阻塞本计划其余项。

### SAG-OPT-802 磁盘保护（已完成 2026-08-01）

| 剩余空间 | 行为 |
| --- | --- |
| 小于 30 GB | 警告并禁止自动 compaction |
| 小于 20 GB | 暂停辅助向量写入 |
| 小于 10 GB | 暂停全部向量写入并告警 |
| 小于 5 GB | 暂停新文档解析，保护数据库 |

实现：

- 新增 `apps/api/sag_api/core/disk_guard.py`：`protection_level()` 分级判定 + `DiskGuard`（按 `disk_check_interval_seconds` 缓存、线程安全、`allow_aux/allow_vector/allow_ingest` 门禁）。
- 配置（`config.py`）：`disk_guard_enabled=true`、`disk_warn_gb=30`、`disk_pause_aux_gb=20`、`disk_pause_vector_gb=10`、`disk_pause_ingest_gb=5`、`disk_check_interval_seconds=300`。
- 接线：
  - 事件向量入队（`enqueue_event_vector_sync`）、source_chunk 向量入队（`enqueue_source_chunk_vector_sync`）：`allow_vector()` 门禁（<10GB 跳过并告警）；
  - 辅助向量入队（`_enqueue_aux_vector_sync`）：`allow_aux()` 门禁（<20GB 跳过）；
  - 文档新建/导入/重处理（`document_service.py` 三处入口）：`allow_ingest()` 门禁（<5GB 抛 `ServiceUnavailableError`）。
  - `app.state.disk_guard` 在 API 启动时注入。
- 阈值均可配置且可整体回退（`disk_guard_enabled=false`）。
- 新增 `tests/test_disk_guard.py`（3 passed）。

### SAG-OPT-803 自动维护策略（已完成 2026-08-01）

- [x] 维护前必须获得独占租约。
- [x] 队列空闲且碎片超过阈值时压缩。
- [x] 低负载窗口清理旧版本。
- [x] 不对每一个写批次执行 optimize。
- [x] 维护过程写入进度和检查点。
- [x] 中断后可以重新评估并继续。
- [x] 保留最近一次成功维护报告。

实现：

- 新增自动维护调度器 `apps/api/scripts/auto_maintenance.py`：
  - 触发条件：单表 fragment >= 500、版本增量 >= 500、目录/有效字节比 >= 2.5、距上次成功维护 >= 24h 且系统空闲（`--force` 强制）。
  - 执行门禁：调度器租约（`auto-maintenance-scheduler`）+ 子脚本各自 `lancedb-maintenance` 独占租约、队列空闲（vector_write_jobs 无 queued/running/writing/retry，jobs 无 RUNNING/EXTRACTING/PENDING/QUEUED）、运行进程检测、磁盘 <30GB 拒绝、报告目录可写。
  - 队列非空闲时只记录 `deferred_queue_busy` 评估报告，绝不与入库并发。
  - 执行内容：`optimize_lancedb_table.py`（压缩 + 裁剪旧版本）→ `cleanup_lancedb_old_versions.py`（低负载窗口清旧版本）；`--delete-unverified` 仅在确认存在可打开备份后由运维显式传入（默认只压缩不删旧版本，满足“维护前必须有可打开的备份”）。
  - 状态文件 `E:/sag/.data/maintenance/auto-maintenance-state.json` 记录每表版本/行数与上次成功时间；中断后重新运行即可续做。报告 `E:/sag/.data/reports/auto-maintenance-<ts>.json`，保留最近 30 份。
- LanceDB 索引增量维护（SAG-OPT-304）：LanceDB 在 append 写入时自动增量维护 ANN/标量索引，不对每个写批次重建；`ensure_vector_indexes.py` 幂等补建缺失索引并校验覆盖 100%。
- 审计：`audit_sag_storage.py` 已输出每表 `indices`/`index_summary`（覆盖行数、未覆盖行数、索引类型、距离度量）与 `fragments`/`version`/`active_total_bytes`，供调度器与人工巡检使用。
- 新增 `tests/test_auto_maintenance.py`（3 passed）：触发条件、状态基线版本增量、队列空闲判定。


## 6. 测试计划

### 6.1 单元测试

- 队列记录去重。
- 任务聚合和尾批 flush。
- lease 超时恢复。
- 可重试/不可重试错误分类。
- 批次失败拆分。
- active 任务唯一约束。
- 图谱缓存状态门禁。
- 文档标签与批量操作约束。
- 磁盘阈值策略。

### 6.2 集成测试

- 进程在 embedding 前、embedding 后、Lance 写入中、提交后中断。
- API 重启后队列恢复。
- 同一事件多次提交保持幂等。
- 两个文档并发抽取，单 Lance writer 正常工作。
- 清理/压缩前后表行数和查询结果一致。
- 索引前后 Recall@10 与延迟比较。
- SQLite 连接池高并发压力。

### 6.3 大数据回归

测试库建议至少包含：

- 10,000 个文档。
- 100,000 个事件。
- 150,000 个实体。
- 500,000 条事件—实体关系。
- 100,000 个 1024 维双向量记录。

记录：

- 入库吞吐。
- CPU、内存、磁盘写入。
- Lance 版本和碎片增长速度。
- 检索 P95/P99。
- 图谱和探索首屏时间。

### 6.4 故障注入

- Embedding API 429、500、超时。
- MinerU 超时。
- SQLite busy/locked。
- LanceDB 暂时不可写。
- 磁盘空间不足。
- Windows 强制结束 API。
- EXE 更新期间重启。

## 7. 发布与回滚策略

### 7.1 发布顺序

1. 只读审计与监控。
2. 队列模型迁移。
3. 聚合写入和单写者。
4. 现有任务合并。
5. 观察至少一个完整入库周期。
6. 停机备份和 LanceDB 清理。
7. 活跃碎片压缩。
8. ANN/标量索引。
9. SQLite 索引与连接池优化。
10. 前端轮询和桌面生产模式优化。

### 7.2 功能开关

建议增加：

- `vector_queue_v2_enabled`
- `vector_append_new_enabled`
- `lancedb_ann_enabled`（默认 true；检索走 ANN 索引 + refine_factor=8 精排）
- `lancedb_search_refine_factor`（默认 8，0=关闭）
- `lancedb_search_nprobes`（默认 16，0=上游默认）
- `aux_vector_deferred_enabled`
- `lancedb_auto_maintenance_enabled`
- `lancedb_ann_enabled`
- `document_status_stream_enabled`

所有开关必须可以回退到上一条稳定路径，但不得恢复产生重复任务的旧逻辑。

### 7.3 回滚要求

- 数据库迁移必须有 downgrade 或向前兼容读取。
- 队列旧记录不得物理删除。
- LanceDB 维护前必须有可打开的备份。
- 索引失败可以删除索引并回退 exact search。
- 前端新接口失败可以回退单一低频轮询。

## 8. 优先级与依赖

| 优先级 | 任务 | 依赖 | 是否需要停机 |
| --- | --- | --- | --- |
| P0 | SAG-OPT-001 审计工具 | 无 | 否 |
| P0 | SAG-OPT-101 启动内存修复 | 无 | 重启 API |
| P0 | SAG-OPT-102～107 队列与写入改造 | 数据迁移 | 短暂停机部署 |
| P0 | SAG-OPT-104 合并现有任务 | 队列 V2 | 是，暂停 worker |
| P0 | SAG-OPT-201～204 备份清理压缩 | 写入改造完成 | 是 |
| P1 | SAG-OPT-301～304 向量索引 | 压缩完成 | 建议维护窗口 |
| P1 | SAG-OPT-401～404 SQLite | 401 已完成；402～404 基准和迁移 | 部分需要 |
| P1 | SAG-OPT-501～503 状态与速率 | API 接口 | 否 |
| P1 | SAG-OPT-601～604 图谱探索 | 缓存/索引 | 否 |
| P2 | SAG-OPT-701～703 前端与 EXE | 核心链路稳定 | 否 |
| P1 | SAG-OPT-801～803 监控维护 | 审计工具 | 否 |

关键路径：

```text
审计基线
  → 修复启动扫描
  → 队列 V2 / 聚合批写 / 单写者
  → 合并现有任务
  → 观察验证
  → 停机备份
  → 清理旧版本
  → 压缩碎片
  → 建立向量索引
  → 自动维护
```

## 9. 建议里程碑

### M1：止损完成

- 启动不再整表读取向量。
- 新任务按大批次写入。
- 所有向量表经过单写者。
- 重启后任务可恢复。

### M2：存储恢复健康

- 完成备份。
- 清理历史版本。
- 完成活跃碎片压缩。
- 磁盘空间恢复。
- 知识库业务回归通过。

### M3：检索与数据库提速

- 完成 ANN 和标量过滤索引。
- SQLite 重复索引迁移完成。
- 查询、内存和入库性能达到目标。

### M4：产品化完成

- 状态和进度实时更新。
- 图谱与探索稳定。
- 自动维护和告警可用。
- EXE 生产模式、迁移和恢复流程通过。

## 10. 完成定义（Definition of Done）

只有满足以下全部条件，本优化计划才算完成：

- [x] 当前 131 GB 异常占用已安全处理，备份和清理报告完整。
- [x] 清理前后业务数据和最新向量一致（2026-08-01 复核：文档 18,549（ready 18,535）、分块 46,364、事件 118,080、实体 211,991、事件-实体关联 725,512（唯一对 725,512、重复 0）；检索/知识库/图谱链路在线可用）。
- [x] 小任务写入模式已被聚合批写替代。
- [x] 所有 LanceDB 写入路径受单写者控制。
- [x] 队列支持失败重试、进程中断恢复和 active 去重。
- [x] LanceDB 自动维护不会与入库并发执行（独占租约 + 队列空闲门禁）。
- [x] 向量检索延迟和召回达到验收目标（Recall@10 ≥ 0.995，P95 ≤ 40ms）。
- [x] SQLite 连接、锁和重复索引问题完成验证（SAG-OPT-401/402/403）。
- [x] 文档状态、暂停、失败、重试和批量操作行为一致（API 单测覆盖 pause/resume/reprocess/delete 与增量处理器暂停恢复；批量操作由前端逐项调用同一接口；2026-08-01 状态事件化后列表轮询与详情轮询共用同一状态源）。
- [x] 图谱缓存遵守“queued/running 阻止，全 paused/ready/failed 允许”的规则（对应 SAG-OPT-601 已完成核对：busy 集合不含 PAUSED，辅助向量补齐不阻塞 SQLite 关系缓存）。
- [x] 监控可以提前发现磁盘、碎片、队列和检索退化（审计脚本含索引覆盖度）。
- [x] EXE 打包环境不依赖 `next dev`（Next standalone production build）。
- [x] 所有关键流程具有自动化测试、操作手册和回滚方案。

## 11. 首轮执行建议

第一轮不要直接清理 131 GB，建议按以下顺序开始：

1. 实现 `SAG-OPT-001` 存储审计工具。
2. 实现 `SAG-OPT-101` 启动自检只读 ID。
3. 完成 `SAG-OPT-102～107` 队列和单写者改造。
4. 运行 `SAG-OPT-104` dry-run，确认任务合并结果。
5. 部署后观察一轮真实入库，确认版本和碎片增长已受控。
6. 再进入阶段 2 的停机备份、清理和压缩。

这样可以保证清理不是一次性释放空间，而是从根源上阻止问题再次发生。
