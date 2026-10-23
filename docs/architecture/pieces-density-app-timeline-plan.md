# Pieces Density App Timeline：技术审计与实施方案

状态：Phase 0–5 与 production graph v5 cutover 已完成

规划分支：`plan/timeline-app-icons-audit`

实施分支：按阶段递进；当前为两个仓库的 `feat/timeline-app-icons-phase5`

审计日期：2026-10-23

Phase 0 实施记录：

- event-logger `0d492af`：为全局 minute range、source-pack replacement 和 acquisition
  watermark 增加索引，并用 `EXPLAIN QUERY PLAN` 锁定 reader/ingestion/delta refresh 路径。
- yasb-fork `f185db9e`：增加 graph feature detection、v4 heatmap-only 兼容合同，以及由
  acquisition rowid/observed-at 双水位驱动的增量 minute refresh。
- production graph 尚未由本分支执行写迁移；索引会在 graph service 使用新版本首次进行
  正常 writable initialization 时幂等创建。
- 只读 production-scale 基线为 39,138 条 minute facts：旧计划的 7 日查询为全表扫描并建立
  临时 GROUP BY B-tree，热缓存约 23–27ms；相同数据的内存副本创建新索引后改为 range
  search、无临时 B-tree，约 2–4ms，查询结果一致。

Phase 1 实施记录：

- event-logger graph schema 升至 v5，增加规范 App、identity key、安装变体、窗口上下文、
  前台 span、icon version 和 acquisition-bound per-App minute facts。
- ingestion 在 semantic inference 前以同一 transaction 写入 App facts；重试先按 source-pack
  替换，保持幂等，semantic failure 不会丢失确定性活动事实。
- v1 attribution 采用事件自身 App、linked focused frame、既有一秒 focus boundary 的严格顺序；
  未来 app switch 不会反向覆盖旧点击，focus/switch 自身始终为零 effort。
- v1 score 保存 raw typed counts、unioned engaged milliseconds、bounded burst bonus、confidence
  和 scoring version；纯 passive frame 的 score 为零。
- `timeline_app_minutes_v1` 是 YASB 的稳定消费 view；graph v4 仍由 YASB 作为 heatmap-only
  source 支持。
- graph merge 已覆盖 App identities/facts 并通过重复 merge 幂等测试；Phase 1 没有迁移生产库。

Phase 2 实施记录：

- event-logger `2b8f2c9`：新增独立 WinEvent hook/message-pump 与一秒批处理 resolver；native
  callback 只向 bounded channel 投递原始 handle/event/time，metadata、图标和 SQLite 工作均在
  resolver thread 完成。
- resolver 按 PID + process creation time 缓存快照，保存 AUMID 或 executable-file identity、
  variant、bounded window context 和 foreground span；source-local 名称可双向升级到已有强身份。
- input attribution 已加入 WinEvent foreground span，优先级位于 linked frame 之后、弱
  Screenpipe focus boundary 之前；collector 启动、解析或持久化失败均只降级，不阻断 acquisition。
- 图标按 HWND、class、EXE 顺序解析，归一化为 128×128 alpha PNG，并在 DB transaction 前发布
  到 SHA-256 CAS；current manifest 使用 Windows replace-existing/write-through 原子替换。
- Rust lib tests 316/316 通过，`cargo clippy --all-targets -- -D warnings` 通过；Phase 2 仍未迁移
  production graph。

Phase 3 实施记录：

- yasb-fork `5304b6c8`：增加 v5 timeline view 的只读 range query 与 acquisition-watermark
  delta cache；v4、Pieces fallback、view 缺失和 App reader failure 均保持 heatmap-only。
- worker 线程构建稳定阶段、完成像素压缩并校验/预取 CAS PNG；paint path 不读取 SQLite、文件、
  EXE 或 Screenpipe API，也不进行 PNG decode/resize。
- renderer 使用面积感知的平方根尺寸、最多 5 个 App、相近权重 diagonal stack、可见阶段正倒
  三角交替、stack opacity 衰减和 confidence opacity cap；过密且不相似的阶段保留事实但隐藏图标。
- scaled pixmap 以 `(icon_sha256, logical_size, DPR)` 为 key，LRU 上限 256；tooltip 附带 App、
  direct inputs、engaged seconds 和 confidence。
- 相关测试 32 项（另含 2 subtests）通过，7 日/10,080 minute 合成输入阶段构建约 10.8ms、
  layout 约 1ms；self-contained frozen dev bundle 构建通过。Phase 3 未修改用户 config/theme。

Phase 4 实施记录：

- event-logger `5a71ca9`：将历史 App 投影拆为既有 bounded raw replay（exact）和显式
  `backfill-app-activity-approx`（只有 microblock 时）两条路径。
- approximate facts 以 `projection_kind=historical_approx`、`interaction_level=uncertain`、低
  confidence、零 direct/engaged score 保存；命令幂等，后续 exact replay 会按 source pack
  删除并替换 approximate facts。
- YouTube Screenpipe acquisition 为每个 evidence microblock 附最多 3 个 engaged App，并贯穿
  SourcePack evidence、lexical retrieval 和 compact review source；contract 固定标记
  `authority=context_only` 与 `semantic_node_authority=false`。
- Rust all-target 共 356 项测试通过，Clippy `-D warnings` 通过；Phase 4 仍未触碰 production
  graph，也没有自动运行 approximate backfill。

Phase 5 实施记录：

- event-logger `9d86da8`：新增 `screenpipe-graph-bundle` v1 及 `archive-bundle`、
  `verify-bundle`、`restore-bundle`、`merge-bundle` 命令；SQLite online snapshot、所有 timeline
  facts 可达的 icon versions、icon manifest 与 bundle hash 在同一目录发布和验证。
- verifier 只读检查 manifest/hash、SQLite size/hash/quick-check/foreign keys、DB-to-manifest
  bindings、PNG signature/content hash 与规范 CAS 路径；拒绝覆盖目标、篡改 icon 和非 CAS 文件
  引用。merge 复用或复制 CAS，沿用 graph 幂等 merge，并重写目标 current icon manifest。
- event-logger all-target 共 363 项测试及 Clippy `-D warnings` 通过；其中 6 项覆盖旧 schema 空
  icon 集、完整 archive/verify/restore、整包 offline/remount、tamper rejection、路径 confinement
  和重复 merge。
- YASB consumer regression 验证 SQLite 与相邻 icon CAS 随 bundle root 一起移动：root offline
  后 heatmap 与 icon 同时不可用，换根 restore 后二者均从新根恢复；YASB 不保存绝对 icon 路径。
- cold rotation、storage contract 与 consumer inventory 已切换到 bundle v1。所有 Phase 均未
  修改用户 config/theme。
- 2026-10-23 production cutover：先在 N: 保存并验证 v4 bundle，再通过 restore 副本演练后将
  active graph 从 v4 幂等迁移到 v5；39,240 条 density minute facts 保持不变。10,130 个
  pre-receipt windows 以 `completeness=inaccessible` 补齐兼容 envelope（保留 206,644 source
  refs，不提升证据权限），随后为 21,168 个历史 packs 写入 129,269 条零 direct/engaged 的
  `historical_approx` facts。post-v5 bundle 含 reachable icon 并通过 hash/quick-check/FK 验证。
- YASB Dev 已从当前分支重建启动；production consumer verifier 为 5 healthy、0 failing，
  `pieces_density` runtime 已确认使用 `screenpipe_graph`。Screenpipe 24h entitlement 仍显式标记
  restricted，不被 migration 或 approximate facts 掩盖。

## 1. 结论

该功能应当作为现有 Screenpipe incremental graph 的确定性活动投影来实现，而不是在
YASB 内重新抓取窗口，也不应把 App 当作 semantic graph 的 `tool` node。

采用以下技术边界：

- `event-logger` 的 Rust `screenpipe-graph-service` 是 App 身份、前台上下文、活动事实和
  图标资源的唯一写入者。
- 继续使用现有 continuous graph SQLite；增加独立的关系表和 schema migration，不新增
  第二个 timeline 数据库。
- 图标存为 graph bundle 旁的 SHA-256 content-addressed PNG；SQLite 只保存关系、hash 和
  相对路径，不保存 PNG BLOB。
- YASB/PyQt6 保持只读，只查询 minute facts 和 identity/icon manifest，在 worker thread
  中完成阶段聚合，在 GUI thread 中完成 `QPixmap` 转换和绘制。
- Screenpipe raw 仍只能通过 typed API 或已登记的 immutable legacy generation 读取；不得
  为图标时间轴直接打开 live hybrid index/Parquet。
- 前台状态是上下文证据，不是投入。图标大小由直接交互形成的 versioned engaged score
  决定；`window_focus`、`app_switch` 和单纯保持焦点不会增加主要分数。

这条路径无需新增常驻进程、HTTP 服务、WebView 或 Python 依赖。

## 2. 已确认的产品不变量

1. 只有归属于前台上下文的交互可以贡献主要 App 权重。
2. 纯后台 App 不显示；纯前台但没有直接输入的 App 最多以最小尺寸、低透明度出现。
3. 同一阶段默认最多显示 5 个 App；空间不足时先叠放、降 rank，再在语义相近时合并阶段。
4. 阶段内按权重从大到小排列；相邻阶段交替采用“大图标在上”和“大图标在下”。
5. 权重接近的图标允许斜向叠放，叠放深度越大透明度越低。
6. 大图标总体比小图标更不透明，但来源置信度可以对最终透明度施加上限。
7. 只要某日期的 graph heatmap 可以读取，其引用的图标也必须随 graph bundle 可读取。
8. YASB 不在 paint、hover 或历史查询路径中打开 EXE、枚举窗口或访问 Screenpipe API。
9. App 信息分层保存，常用展示不必读取窗口标题、路径和原始来源证据。
10. 同一套 deterministic facts 应可供 YASB、YouTube summary 和后续检索共同使用。

## 3. 现状审计

### 3.1 YASB `pieces_density`

现有实现已经具备合适的只读骨架：

- `src/core/widgets/yasb/pieces_density_source.py` 通过 SQLite URI `mode=ro` 读取
  `source_activity_minutes`，并保留 Pieces 作为诊断 fallback。
- 数据以 1 分钟 bucket 返回，随后用 ±5 分钟窗口做 density integration。
- `FetchWorker` 在 `QThread` 中读取，GUI thread 只接收 Python 数据并重绘。
- closed interval 可完整缓存；live Screenpipe interval 会重新验证，因为 graph window 可能
  在首次查询之后才提交。
- `DensityOverlay.paintEvent` 当前只绘制一个覆盖整个高度的平滑热力曲线和刻度。
- 当前 dev 配置 `widget_height: 100`，实际 overlay 因硬编码 `-15` 为 85px 高，轮询周期 10s。

发现的性能缺口：

- graph 只有 `(source_generation_id, minute_start)` 索引，但 YASB 的连续多世代查询只按
  `minute_start` 过滤。对当前数据库执行 `EXPLAIN QUERY PLAN` 会扫描整个
  `source_activity_minutes` 并建立临时 GROUP BY B-tree。
- 新 schema 必须添加以 `minute_start` 为首列的索引；现有 density 表也应同时修正。
- live interval 不宜在每 10 秒为 App facts 重读完整直播历史。应以
  `source_acquisitions.observed_at` 为 watermark，只重算新 acquisition 触及的 minute。

### 3.2 YASB workspace 图标实现

`src/core/widgets/komorebi/workspaces.py` 与 `src/core/utils/win32/app_icons.py` 已证明：

- HWND icon、AUMID icon、class icon 和 EXE icon 都可取得；
- PIL RGBA 到 `QImage/QPixmap` 的 DPI 路径可工作；
- workspace preview 已处理 owned overlay、多显示器坐标、透明度和紧凑布局；
- 非焦点 workspace 图标目前使用 `200/255` 的 opacity；
- 当前 cache key 是 `(HWND, DPR)`，适合短寿命 workspace，却不适合历史 timeline；HWND
  会失效、复用，而且内存字典没有持久资源身份。

可复用的是 Windows 提取顺序、alpha 处理、DPI 和 overlay 经验；不可复用的是 HWND cache
及“YASB 负责解析图标”的所有权模型。

### 3.3 event-logger incremental graph

当前仓库为 Rust 2024，已经依赖 `windows`、`rusqlite`、`sha2`、Tokio：

- graph schema 当前为 v4；
- `Observation` 已保存 `source_generation_id/source/source_id/frame_id/timestamp/app/window/kind`；
- typed Screenpipe OCR/accessibility rows会丢弃明确的 `focused=false`；
- legacy frame 查询只保留 `focused=1 OR focused IS NULL`；
- input 会丢弃 `move`，保留 key/click/scroll/text/clipboard/app_switch/window_focus；
- `derived_observation_keep_mask` 已抑制重复 accessibility、匿名 click、快速标题动画 focus；
- acquisition 在 semantic inference 之前提交；semantic failure 不会丢失 density facts；
- `source_activity_minutes` 保存 raw/effective/suppressed frame 和 UI event 数；
- microblock JSON 已包含 App/窗口/event type 的计数，但没有稳定 App ID，也没有精确的
  per-minute per-App 投影。

生产 continuous graph 的只读审计结果：

- SQLite 大小约 626 MB；
- `source_activity_minutes` 约 39k 行；
- `microblocks` 约 40.5k 行；
- microblock-App pairs 约 77.6k；平均每 block 1.94 个 App，最大 13 个。

因此 per-minute per-App facts 的规模可控，不需要另建 OLAP 服务。

### 3.4 Screenpipe 现场字段

本机 Screenpipe engine v0.4.52 的 UI recorder 处于 `full` 模式，input tap 与 app event
recorder 均在运行。只读抽样确认：

- click/key/scroll 通常携带事件发生时的 `app_name` 和 `window_title`；
- click/scroll 经常携带 `frame_id`；
- 部分 `text` 事件的 App、窗口和 frame 都为空；
- `app_switch/window_focus` 紧邻实际输入，但它们是边界而非 effort；
- 切换边界附近存在“点击仍属于旧 App、随后 app_switch 指向新 App”的正常顺序。

所以直接事件自带的 App 应优先；缺失时再依次用 linked focused frame、WinEvent span 和
邻近 Screenpipe focus boundary 补全。不能把时间上最近的 app_switch 无条件覆盖 click。

### 3.5 Screenpipe timeline 的可借鉴点

当前 Screenpipe 前端实现：

- 按连续 frame 的主 App 分组；浏览器还会按 domain change 切段；
- metadata 缺失时会 carry forward 上一个 App；
- group 宽度不超过 30px 时不显示图标；
- 30–60px 只显示 1 个，超过 60px 最多显示 2 个；
- 图标固定 20px，第二个用 `marginTop: -6px` 重叠约 30%；
- 全局按 App name 预加载图标，缓存 loaded/error/in-flight 状态，避免滚动闪烁。

来源：

- [Screenpipe timeline grouping](https://github.com/screenpipe/screenpipe/blob/main/apps/screenpipe-app-tauri/components/rewind/timeline/timeline.tsx#L792-L891)
- [Screenpipe icon rendering](https://github.com/screenpipe/screenpipe/blob/main/apps/screenpipe-app-tauri/components/rewind/timeline/timeline.tsx#L1644-L1771)

我们采用其“连续阶段、像素阈值、预加载、有限重叠”，但不采用 name-keyed identity、固定
尺寸和仅两个图标的限制。

### 3.6 Windows metadata 先例

`E:\MCP\Projecto\clipboardx` 已包含可借鉴的 Windows 实现：

- `SetWinEventHook(EVENT_SYSTEM_FOREGROUND/EVENT_OBJECT_FOCUS)`；
- foreground-event coalescer；
- `GetGUIThreadInfo` 的 active/focus HWND；
- HWND → PID → limited process handle → EXE path；
- title/window class/focused control class；
- `Icon.ExtractAssociatedIcon` 与持久 icon cache。

InputTip 和 AHK v2 Window Spy 进一步证明进程名、窗口类、焦点控件类、标题和路径适合作为
诊断字段。它们是实现参考，不是 runtime dependency。ClipboardX 的 SQLite icon BLOB 不应
复制到本项目。

## 4. 目标架构

```text
Screenpipe typed observations               Windows WinEvent
  focused frame + input events        foreground/focus transition
                 \                          /
                  \                        /
                   deterministic attribution
                     + identity resolver
                              |
          +-------------------+-------------------+
          |                                       |
  SQLite layered facts                     PNG SHA-256 CAS
  app/minute/span/provenance          assets/app-icons/sha256/..
          |                                       |
          +---------------- graph bundle ---------+
                              |
                  +-----------+-----------+
                  |                       |
           YASB read-only           summary/search readers
           stage + layout
```

### 4.1 进程和模块边界

不增加新 daemon。建议在 `event-logger/src/screenpipe/` 增加：

- `windows_context.rs`：WinEvent hook、message pump、process-instance snapshot；
- `app_identity.rs`：identity key、alias、variant 和 canonicalization；
- `app_activity.rs`：事件归因、burst、engaged score、minute materialization；
- `icon_store.rs`：Windows icon candidates、PNG normalization、CAS 和 manifest；
- `timeline_projection.rs`：面向消费者的稳定查询/导出 contract。

`screenpipe-graph-service` 启动一个 WinEvent OS thread。native callback 只读取事件自带参数
和单调时钟、将最小 tuple 放入 bounded channel；路径、签名、AUMID、图标、SQLite 均不得在
callback 中执行。

事件时间优先使用 WinEvent 的 `dwmsEventTime` 与系统 uptime 换算，而不是 callback 实际被
调度的墙钟时间。这样服务短暂繁忙时仍可和 Screenpipe timestamp 对齐。

### 4.2 前台 collector 的持久化策略

- `EVENT_SYSTEM_FOREGROUND` 形成 top-level foreground span。
- `EVENT_OBJECT_FOCUS` 只更新 child focus metadata，不为每个控件创建 span。
- 相同 root HWND/PID 的突发事件在 250ms 内 coalesce。
- PID 必须和 process creation time 配对；裸 PID/HWND 永远不是 App identity。
- 写入采用同进程异步 batch：最多每秒一次或累计 128 条即提交。
- graph service 停止期间不会伪造 WinEvent history；那段数据退回 Screenpipe attribution，并
  明确降低 confidence。

### 4.3 分层数据模型

next graph schema 建议为 v5。表名可在实施时按现有命名微调，但层级和关系不变。

#### L0：消费摘要

`timeline_app_minutes_v1` 作为稳定 view/query contract，输出：

```text
minute_start, canonical_app_id, display_name, icon_sha256,
engaged_score_milli, engaged_milliseconds, foreground_milliseconds,
direct_event_count, interaction_level, attribution_confidence_milli
```

YASB 只依赖此 contract 和现有 density facts。

#### L1：规范 App

`app_entities`

```text
app_id TEXT PRIMARY KEY
canonical_app_id TEXT              -- flattened alias target
display_name TEXT
publisher TEXT NULL
app_kind TEXT                      -- win32, packaged, pwa, source_local
identity_confidence_milli INTEGER
first_seen_at / last_seen_at TEXT
```

`app_identity_keys`

```text
key_type                            -- aumid, package_family, signer_product,
                                    -- executable_file, source_local
key_value
app_id
valid_from / valid_to
confidence_milli
provenance
```

identity 升级时 flatten `canonical_app_id`，facts 不必重写，消费者只需一次 join。

#### L2：安装/文件变体

`app_variants` 保存：

- AUMID、package family；
- process/executable name 和路径；
- PE `ProductName`、`FileDescription`、`OriginalFilename`；
- publisher/signature identity；
- volume/file ID、size、mtime；
- first/last seen、resolution status 和 access-denied reason。

路径属于详细层，不参与 timeline 的常用查询。

#### L3：活动事实

`source_app_activity_minutes`

```text
source_generation_id
minute_start
app_id
source_pack_sha256
frame_count / effective_frame_count / suppressed_frame_count
key_count / click_count / scroll_count / text_count / clipboard_count
interaction_burst_count
engaged_milliseconds
foreground_milliseconds
engaged_score_milli
interaction_level                 -- direct, passive, uncertain
attribution_confidence_milli
scoring_version
PRIMARY KEY(source_generation_id, minute_start, app_id, source_pack_sha256)
```

保留 acquisition key 以维持 audit/merge 幂等性；消费 view 再按 minute + canonical App 聚合。

#### L4：窗口上下文

`foreground_context_spans`：start/end、App/variant/process-instance、root window context、来源和
confidence。`window_contexts` 去重保存 bounded title、title hash、top-level class、focused
class、monitor 和 capture method。

HWND/PID 可以作为诊断证据保存，但必须标记为 ephemeral，不得用于历史 join key。

#### L5：来源证据

继续使用 `source_acquisitions`、`source_pack_sha256`、generation-scoped source refs。新增事实
引用 acquisition，不复制 screen text 或 Screenpipe payload。

### 4.4 索引

至少增加：

```sql
CREATE INDEX idx_source_activity_minutes_minute
  ON source_activity_minutes(minute_start);

CREATE INDEX idx_source_app_activity_minutes_minute
  ON source_app_activity_minutes(minute_start, app_id);

CREATE INDEX idx_source_app_activity_minutes_pack
  ON source_app_activity_minutes(source_pack_sha256);

CREATE INDEX idx_foreground_context_spans_time
  ON foreground_context_spans(started_at, ended_at);

CREATE INDEX idx_source_acquisitions_observed
  ON source_acquisitions(observed_at);
```

## 5. 身份解析

强到弱的 identity ladder：

1. AUMID + Package Family；
2. signer/publisher + PE ProductName + OriginalFilename；
3. executable file identity + version metadata；
4. machine-local executable path/name alias；
5. `screenpipe:<source_generation_id>:<normalized_app_name>`。

source-local 只应覆盖：旧历史、远端设备、进程已退出、protected/elevated 查询失败或服务停机
缺口。获得更强证据后新增 temporal alias 并 flatten canonical target，不删除原始 identity。

浏览器普通网页在 v1 中仍归属于浏览器 App。只有可靠 AUMID 的 installed PWA 才独立；
favicon/domain 属于未来 `context_icon`，不混入 App icon identity。

## 6. 图标资源合同

### 6.1 目录

```text
data/screenpipe-graph/
  activity-memory-continuous-v2.sqlite3
  assets/
    app-icons/
      sha256/
        ab/
          abcdef....png
      manifest-v1.json
```

数据库只保存相对 graph bundle root 的路径。

### 6.2 解析优先级

1. 事件发生时 HWND 的 `WM_GETICON`；
2. AUMID/package icon；
3. window class icon；
4. EXE embedded icon；
5. 无 icon，等待后续证据；不把 generic application icon 写成该 App 的永久身份。

候选图标在后台 worker 中解析。按 `(PID, process_start_time)` 缓存 process snapshot，按
file identity/mtime 缓存 PE、签名和 icon candidate。

### 6.3 CAS 写入

- 标准化为带 alpha 的 128×128 PNG，并保留一致 optical padding；
- 编码后计算 SHA-256；
- 同目录临时文件写入、flush、原子 rename；
- DB transaction 只在文件成功发布后绑定 hash；
- 同内容自然 deduplicate；App 更新图标时新增 version，不覆盖旧文件；
- `app_icon_versions` 保存 valid_from/valid_to、source kind、variant/file fingerprint。

Rust 已有 `sha2`；建议增加小型 PNG encoder，并扩展现有 `windows` crate features，不引入
Electron、WebView 或单独图标服务。

### 6.4 冷热和备份

- continuous graph 热库仍能显示旧 minute facts 时，其所有 reachable icons 必须留在热 CAS。
- graph checkpoint/archive 必须包含 SQLite、reachable icon manifest 和对应 CAS 文件。
- restore/merge 先按 hash 复制并验证 CAS，再事务化 merge DB relations。
- archive health check 验证所有 active icon binding 均有文件、hash、尺寸和 PNG signature。
- YASB 不因文件缺失回退到当前 EXE；那会把历史图标悄悄替换为今天的版本。
- 文件缺失属于 bundle integrity error：记录诊断并跳过该 icon，不能影响 heatmap 曲线。

## 7. 前台交互归因和评分

### 7.1 归因优先级

对每个 direct input observation：

1. observation 自带非空 `app_name`：最高优先级；
2. linked `frame_id` 对应的 focused frame App；
3. timestamp 覆盖的 WinEvent foreground span；
4. ±1 秒内、方向一致的 Screenpipe `app_switch/window_focus` boundary；
5. 无法解析则保留 unattributed，不为任何 App 增加主要分数。

若两个来源冲突，直接事件自己的 App 不被随后切换边界覆盖；记录 conflict flag 并降低
confidence。纯 frame/accessibility 只形成 passive foreground evidence。

### 7.2 v1 engaged score

必须同时保存 raw counts、`engaged_milliseconds`、score 和 `scoring_version`，便于后续重算。

建议 v1：

1. 对直接事件建立 `[t-0.5s, t+2.5s]` 的 active windows，在每个 App/minute 内求 union，
   得到 `engaged_milliseconds`，天然限制键盘重复风暴。
2. key/text 在 500ms 内合为一个 burst；scroll 在 750ms 内合为一个 burst；click 和
   clipboard 保留独立 burst，但每分钟设上限。
3. burst bonus 使用秒等价值：key/text 2.0、click 2.0、scroll 1.0、clipboard 3.0；每
   App/minute bonus 上限 20 秒。
4. `engaged_score_seconds = engaged_ms / 1000 + bounded_bonus_seconds`。
5. focus/switch 的 bonus 恒为 0；`foreground_ms` 只进入 tooltip 和 passive 判定。

纯 passive App 不随保持焦点时间变大。它最多获得 renderer 的 minimum visual size；长时间
离开电脑不会形成越来越大的 logo。

## 8. 阶段切分和合并

YASB worker 从 minute vectors 构建阶段，不在 paint 中计算。

### 8.1 初始阶段

- 每分钟计算 dominant direct App、top App vector 和 interaction level；
- dominant change 连续稳定 2 分钟后切段，避免一分钟噪声；
- 2 分钟无 direct interaction 形成 idle boundary；
- 日界、canonical interval 边界强制切段；
- 相邻阶段 dominant 相同、cosine similarity ≥0.72 且 gap ≤2 分钟时合并。

stage anchor 使用该阶段 engaged score 的时间重心，而不是简单中点。

### 8.2 像素空间压缩

根据 overlay width 把时间映射为 x，再按如下顺序消除碰撞：

1. 权重比 `min/max >= 0.75` 的相邻 rank 组成 diagonal stack；
2. visible rank 从最多 5 降到 3；
3. dominant 相同或 cosine similarity ≥0.55 的相邻阶段合并；
4. 不相似阶段保留，缩到 minimum icon size，并移除最低 rank；
5. 仍放不下时隐藏整个过窄阶段的 icon，但不合并其时间事实。

这避免仅因屏幕窄就把语义完全不同的工作阶段合为一个。

Screenpipe 的 `groupWidth > 30px` 规则可作为首版 minimum footprint；最终阈值按 DPR 和
icon size 计算，不写死为 CSS pixel。

## 9. 图标布局和透明度

### 9.1 尺寸

默认 logical size 12–24px。为符合视觉感知，**面积**与 stage 内归一化 engaged score 成
比例，因此边长使用平方根映射；保留配置项允许后续实验 linear edge mapping。

```text
size = min_size + (max_size - min_size) * sqrt(score / stage_max_score)
```

纯 passive/uncertain 使用 minimum size，不因 foreground duration 增长。

### 9.2 顺序

- 偶数可见阶段：最大图标在上，其余从上到下递减；
- 奇数可见阶段：最大图标在下，其余从下到上递减；
- 排列方向由最终可见阶段序号决定，阶段合并后重新计算 parity；
- diagonal stack 保持最大图标在前景，后续图标向时间轴外侧和右侧偏移约自身尺寸的 28%。

### 9.3 opacity

建议 renderer 规则：

- direct primary：0.95–1.00；
- direct secondary：按 score 映射到 0.78–0.94；
- 每增加一层 stack depth 减 0.07，floor 0.58；
- passive foreground：0.42–0.58；
- uncertain：0.30–0.42；
- background/unattributed：不显示。

PNG 自身 alpha 保留，renderer opacity 与 source alpha 相乘。identity/attribution confidence
是 opacity 上限，因此“大但不确定”的图标不会呈现成完全实心。

## 10. YASB 实现边界

### 10.1 数据对象

将 worker signal 从多个 positional primitives 改为一个 snapshot dataclass：

```text
DensityTimelineSnapshot
  interval
  density_buckets
  app_minutes
  stages
  source_status
  asset_root
  watermark
```

纯函数模块建议拆为：

- `pieces_density_source.py`：SQLite contract、watermark delta cache；
- `pieces_density_timeline.py`：score vectors、stage segmentation、collision compaction；
- `pieces_density_icons.py`：bundle path validation、bounded image cache；
- `pieces_density.py`：widget lifecycle 和 painting。

### 10.2 图像缓存

- key：`(icon_sha256, logical_size, DPR)`，不是 app name/HWND/path；
- worker 读取 PNG 为 `QImage` 或 bytes；GUI thread 生成 `QPixmap`；
- bounded LRU，首版上限 256 个 scaled pixmap；
- worker 预取当前可见 stage 的 icon，paint 不做文件 I/O、PNG decode、resize 或 alpha copy；
- draw 时用 `QPainter.setOpacity`，不为每种透明度复制 pixmap。

### 10.3 overlay band

保持当前 85px 实际高度作为兼容起点。`icon_band_height` 是图标组的最大纵向占用高度，图标组
整体以主刻度线顶端上方 2px 为底部基线；它可以覆盖 heatmap，但不覆盖刻度线。删除几何中的
magic `-15` 前，先用 regression screenshot 固定当前位置。

建议新增配置，但默认即可工作：

```yaml
show_app_timeline: true
max_stage_icons: 5
icon_min_size: 12
icon_max_size: 24
icon_band_height: 50
icon_min_gap: 3
stack_similarity_ratio: 0.75
stack_offset_ratio: 0.28
show_passive_foreground: true
```

本计划不修改用户的 `config.yaml` 或 theme；实施完成后再给出可选配置片段。

## 11. 历史数据

新增功能上线前的记录分三类：

1. raw generation 可访问：从 registered immutable source 或 typed history 精确 backfill；
2. raw 不可访问但 microblock 在 continuous graph：按 block 时间重叠和 `apps` 计数生成
   `historical_approx` facts，使用 uncertain opacity；
3. 连 microblock 都不存在：只保留 heatmap，不伪造 App logo。

approximate backfill 不得写成 direct event truth。若以后冷盘挂载并完成 exact replay，同一
范围以更高 generation/projection revision 替换 approximate projection。

## 12. Summary pipeline 接入

YouTube summary 第一阶段只增加确定性辅助上下文，不让 App 数量决定 storyline：

- SourcePack/microblock 可附 `dominant_app_id/display_name`、direct/passive level 和 confidence；
- App switch 可帮助识别阶段边界，但不自动创建 semantic tool node；
- summary selector 可以按 engaged score 找回高投入窗口；
- source refs 和原始证据约束保持不变。

该接入应在 YASB timeline 验证后单独提交，避免 UI 与 summary contract 同时迁移。

## 13. 实施阶段

### Phase 0：现有读性能与 contract 基线

event-logger：

- 增加全局 `minute_start` index；
- 建立 v4 production-like fixture 和 query benchmark；
- 记录 `EXPLAIN QUERY PLAN` regression。

YASB：

- 为 graph schema/version feature detection 增加测试；
- 保持旧 v4 graph 仍只画 heatmap。

### Phase 1：schema v5 与 deterministic App projection

- 增加 identity、variant、activity minute、foreground context、icon manifest 表；
- 扩展 graph initialize/migration、assign-generation、merge 和 foreign-key tests；
- 在 acquisition transaction 中生成 per-App raw/effective facts；
- 保存 scoring version 和 attribution confidence；
- 为 existing source-local app names 建立最小 identity。

### Phase 2：Windows resolver 与 icon CAS

- 增加 WinEvent message-pump thread 和 coalescer；
- 加入 PID+creation-time process cache；
- 实现 AUMID/PE/file/signer identity ladder；
- 实现 HWND/AUMID/class/EXE icon candidates、PNG normalization、CAS；
- 为 elevated/protected/exited process 添加 failure-state tests；
- graph ingestion 在 resolver 失败时必须继续运行。

### Phase 3：YASB reader、stage engine 与 renderer

- 扩展 read-only query 和 watermark delta cache；
- 实现 stage segmentation、pixel compaction 和 alternate triangle layout；
- 实现 hash/DPR LRU 和预取；
- tooltip 展示 App、direct events、engaged time、foreground time和 confidence；
- 保留 graph v4/missing asset/no App facts 的 heatmap-only fallback。

### Phase 4：历史 backfill 与 summary consumer

- exact raw replay 与 microblock approximate backfill 分离；
- summary pack 加入 bounded App context；
- 不改变 semantic node authority 和 source-reference gate。

### Phase 5：archive/restore 和运维合同

- graph checkpoint 工具收集 reachable icons；
- manifest/hash/quick-check 一起核验；
- 更新 storage consumer inventory、cold rotation 文档和 verifier；
- 验证 N: 未挂载、挂载、restore 和 merge 四种状态。

## 14. 测试矩阵

### Rust

- Screenpipe direct App、linked frame、WinEvent span、focus boundary 四级归因；
- click 后立即 app_switch 不被错误改归新 App；
- null text event 的 bounded fallback；
- background frame 不进入 App projection；
- key repeat/scroll storm 的 score 有上限；
- focus-only duration 不扩大 score；
- PID reuse、process exit、access denied；
- identity alias flatten 和 icon version rollover；
- CAS duplicate/atomic failure/hash mismatch；
- schema v4→v5、graph merge 重复执行幂等、foreign-key check；
- semantic provider failure仍提交 activity facts。

### Python/PyQt

- 阶段稳定窗口、idle boundary、相似阶段合并；
- 不相似窄阶段不被错误合并；
- 3/5 icon top-K、similar-score diagonal stack；
- 正倒三角 parity；
- opacity confidence cap 和 stack floor；
- DPR 1.0/1.25/1.5/2.0；
- 1920、2560、3840 width；
- closed/live cache、late acquisition invalidation；
- missing/corrupt/offline asset；
- graph v4 heatmap-only fallback；
- 双显示器位置、bar hide/show、配置 reload。

### 性能预算

- WinEvent callback：只入队，目标 <100µs，无磁盘 I/O；
- metadata/icon resolver：不阻塞 acquisition 或 semantic cursor；
- foreground batch：正常最多 1 SQLite transaction/s；
- 24h initial timeline query：目标 <100ms，7 日 <250ms；
- live delta refresh：目标 <20ms，不随直播总时长线性增长；
- paint：4K overlay 常态 <8ms，hover 不触发 DB/icon decode；
- scaled pixmap cache：默认最多 256 entries；
- persistent facts 不因 paint width 或 top-K 截断，显示限制只发生在 YASB。

## 15. 验收标准

1. 同一直播中可直观看到 App 阶段，图标不跨刻度明显碰撞。
2. 高交互 App 更大、更实；后台 App 不出现。
3. 长时间离开但窗口仍保持焦点，不会让其 logo 持续变大。
4. 相近权重 App 可斜叠；相邻阶段的排列方向交替。
5. graph service 或 icon resolver 失败不会阻断 Screenpipe acquisition/semantic graph。
6. YASB paint path没有 EXE、Win32 metadata、Screenpipe API 或文件 decode。
7. v4 graph、missing App facts 或 missing icon 时，现有 heatmap 仍正常显示。
8. 只要 graph bundle 可显示某日期 heatmap，正常完整 bundle 中引用图标也可显示。
9. graph merge、cold checkpoint 和 restore 后 icon hash 与 identity version 保持一致。
10. YouTube summary 可以读取同一 App facts，但 semantic story 不由 App 计数机械决定。

## 16. 明确不做

- 不把 icon PNG 写入 SQLite BLOB；
- 不让 YASB 成为 metadata/icon writer；
- 不直接读取 live Screenpipe hybrid storage；
- 不把裸 HWND、PID、path 或 app name 当跨时间稳定身份；
- 不把 focus/switch 数量当生产力；
- v1 不做网页 favicon/domain 级时间统计；
- v1 不根据 App 使用量生成主观 productivity score；
- 本规划分支不改用户 dev config、styles、计划任务或 production database。
