# Adaptive App Activity Ribbons Implementation Plan

## Scope and repository boundaries

This plan implements the approved adaptive app activity ribbon design across two repositories:

- `event-logger` owns exact signal classification, minute facts, conservation, icon/color metadata, bundle compatibility, and graph migration.
- `yasb-fork` owns bounded queries, semantic components, layout, cached rendering, hover details, and v1 fallback.

Development happens only on these branches:

- `event-logger`: `feat/adaptive-app-activity-signals-v2`
- `yasb-fork`: `feat/adaptive-app-activity-ribbons`

No production graph or cold archive is upgraded until Phase 6. Every phase ends with acceptance, a scoped commit in every changed repository, `git swap auto`, and a push before the next phase starts.

## Invariants carried through every phase

1. `timeline_app_minutes_v1` keeps its existing columns and meaning.
2. Newly projected exact data satisfies, per minute and signal:

   ```text
   attributed applications + unattributed residual = global total
   ```

3. Direct observations never contribute to non-direct activity.
4. Suppressed observations remain auditable but do not contribute to effective v2 signals.
5. A single confidently attributed effective direct event remains visible.
6. Missing v2 contracts cause a clean v1 fallback, not a blank or broken timeline.
7. Querying, icon decoding, color extraction, component building, and packing never occur in `paintEvent`.
8. Database availability and icon/color availability remain one graph-bundle lifecycle: when a cold timeline database is unavailable, its app layer is unavailable too.

## Phase 0 — contract inventory, implementation map, and baselines

### Plan

- Capture the current schema, query, rendering, cache, bundle, and consumer touch points.
- Record repeatable correctness and performance baselines without modifying production data.
- Establish the exact phase boundaries and rollback/fallback behavior.

### Implementation

- Commit the approved design and this plan on the new YASB branch.
- Run focused existing tests in both repositories.
- Capture the current v1 query plan against a read-only copied or fixture database when available.
- Record current targeted test elapsed times as diagnostic baselines; they are not product latency budgets.

### Acceptance

- Both worktrees are clean except for the plan being committed.
- Existing density tests and Screenpipe graph tests pass.
- Rust formatting passes.
- Baseline evidence and the Phase 1 schema shape are recorded below.

### Phase 1 schema decision

Add effective v2 signal facts without reinterpreting existing v1 columns or changing their physical row layout:

- Global/source v1 minute facts retain raw/effective/suppressed frame and UI totals; a keyed v2 companion fact adds raw/effective/suppressed direct-input and non-direct-UI totals.
- App/source v1 minute facts retain scoring fields and physical compatibility; a keyed v2 companion fact adds effective direct-input totals, effective direct category totals, suppressed direct totals, and raw/effective/suppressed non-direct UI totals.
- Frame activity continues to use existing raw/effective/suppressed app frame columns.
- A bounded residual-reason fact records unattributed effective counts using a versioned reason vocabulary.
- `timeline_app_minutes_v2` and `timeline_global_minutes_v2` aggregate already-projected facts; YASB does not classify raw observations.

The v2 views expose negative residuals rather than clamping them. Migration verification treats a negative residual as corruption and fails loudly.

## Phase 1 — exact graph signal facts and conservation

### Plan

- Centralize direct/non-direct classification so global and per-app aggregation cannot diverge.
- Evolve the graph schema additively and preserve v1.
- Produce explicit residual facts and v2 views.

### Implementation

1. Add failing unit tests for direct exclusion, suppression, missing identity, and conservation.
2. Bump the graph schema version and add columns/indexes with idempotent migrations.
3. Refactor observation classification into a shared typed helper.
4. Extend source/global and source/app aggregation with raw, effective, and suppressed v2 counts.
5. Store unattributed effective counts by signal and bounded reason.
6. Add `timeline_app_minutes_v2` and `timeline_global_minutes_v2` while retaining v1 unchanged.
7. Add query-plan tests that require minute-led indexes for bounded interval reads.

### Acceptance

- Direct observations are absent from the non-direct UI count.
- Suppressed input/UI observations are excluded from effective totals.
- App sums plus residual exactly equal global totals for exact projected fixtures.
- Negative residual verification fails rather than hiding the mismatch.
- v1 schema regression tests remain unchanged and pass.
- `cargo fmt --all -- --check`, focused tests, full tests, and Clippy pass.

### Commit

- `event-logger`: `feat(screenpipe): add conserved app activity v2 signals`

## Phase 2 — bundles, historical projection, icon colors, and consumers

### Plan

- Make the new facts portable across hot/cold bundles and consumable by the summary pipeline.
- Keep historical uncertainty explicit.
- Persist derived color metadata beside icon-version metadata, never PNG bytes inside SQLite.

### Implementation

1. Replace schema-fragile `INSERT ... SELECT *` paths with explicit compatible column lists.
2. Extend graph archive/import manifests and verifier coverage for schema v2 facts and residuals.
3. Add exact reprojection for source packs with sufficient evidence and `historical_approx` rows otherwise.
4. Add representative color and algorithm-version metadata keyed by icon content identity.
5. Preserve icon bytes in the existing content-addressed asset store.
6. Let the YouTube summary adapter read explicit v2 direct/non-direct fields with v1 fallback.
7. Update the storage contract, consumer registry, service documentation, and verifier.

### Acceptance

- A rehearsal hot-to-cold-to-hot round trip preserves v1, v2, residual, icon, and color references.
- Exact and approximate projections are distinguishable.
- Old bundles import through the compatibility path.
- The Screenpipe consumer verifier passes.
- Rust formatting, tests, and Clippy pass.

### Commit

- `event-logger`: `feat(screenpipe): preserve activity v2 across graph bundles`

## Phase 3 — YASB v2 source model and bounded cache

### Plan

- Introduce v2 query/result models without changing rendering yet.
- Make fallback and availability semantics deterministic.

### Implementation

1. Add immutable app/global minute v2 records and residual reason models.
2. Detect v2 view availability once per graph identity.
3. Read a selected interval through bounded, minute-indexed prepared queries on a worker.
4. Preserve v1 records and mixed heatmap fallback for older graphs.
5. Cache closed intervals by graph identity, data version, interval, and contract version.
6. Make a changed watermark invalidate only the affected suffix.

### Acceptance

- v2, v1-only, unavailable cold database, malformed row, and generation-change tests pass.
- No GUI-thread SQL or filesystem access is introduced.
- Query-plan assertions reject full historical scans.
- Warm closed-session re-selection performs no database query.

### Commit

- `yasb-fork`: `feat(timeline): load app activity v2 facts`

## Phase 4 — semantic components, scaling, packing, and color policy

### Plan

- Build deterministic, pure-Python geometry inputs before Qt rendering.
- Bound dense layouts by pixel width and candidate locality rather than fact count squared.

### Implementation

1. Build maximal per-app components with deterministic bounded focus bridges.
2. Compute separate cross-app and global `log1p` robust scales.
3. Downsample direct and non-direct envelopes to drawable pixel columns.
4. Pack intervals with a sweep-line/free-track structure.
5. Place icons near weighted medians using bounded valley search and a spatial grid.
6. Diagonally stack only eligible colliding icon boxes; never merge signal curves.
7. Resolve persisted representative colors first and stable hash colors second.
8. Produce immutable draw/hit-test snapshots and incremental suffix reuse metadata.

### Acceptance

- One event yields a pin; continuous and split sessions form the expected component counts.
- Non-overlapping components reuse tracks and dense overlaps avoid all-pairs scans.
- Similar icons stack with bounded offsets; dissimilar signals remain separate.
- App and global scales are separate and deterministic.
- Sparse, typical, dense, long-running, and one-event fixtures satisfy memory/vertex/candidate bounds.
- Warm incremental rebuild p95 is at most 25 ms on the reference machine.

### Commit

- `yasb-fork`: `feat(timeline): build adaptive app activity layout`

## Phase 5 — cached Qt rendering, hover, and deployed validation

### Plan

- Replace the point-column presentation with the approved two-signal visual grammar.
- Preserve bottom alignment: the lowest icon edge remains just above the ruler.

### Implementation

1. Draw cached stable outlines, direct ribbons, inverted app clouds, passive hatch/dash, focus bridges, pins, icons, and leader lines.
2. Add the visible global direct ribbon, upward non-direct mountain, and gray residual overlay.
3. Extend hover with component interval, category counts, activity split, focus residency, confidence/projection kind, and residual reasons.
4. Reuse bounded pixmap/color/brush/pen/path caches and coalesce invalidations.
5. Instrument SQL, build stages, cache outcomes, snapshot bytes, and paint duration.
6. Build and relaunch the frozen dev app, then validate both monitors, DPI, focus changes, workspace moves, session navigation, and mounted/unmounted cold storage.
7. Capture typical, sparse, dense-overlap, and high-DPI screenshots.

### Acceptance

- Basic ownership/range, direct activity, passive activity, and residual are visible without hover.
- Hover details match source facts.
- `paintEvent` performs cached drawing only.
- Cached 3,840 px paint p95 and GUI snapshot swap are each at most 4 ms.
- Full cold local query plus layout p95 is at most 150 ms, excluding cold-disk wake.
- No periodic decode/color work or idle repaint occurs.
- Focus, window moves, workspace switches, config reload, and both monitors remain correct.

### Commit

- `yasb-fork`: `feat(timeline): render adaptive app activity ribbons`

## Phase 6 — rehearsal, production graph upgrade, and final acceptance

### Plan

- Upgrade data only after both shipped consumers pass against copied graphs.
- Preserve a recoverable pre-migration graph and cold bundle.

### Implementation

1. Run all repository checks and the cross-repository consumer verifier.
2. Create and verify a timestamped production graph backup and matching icon asset snapshot on mounted `N:` storage.
3. Reproject a copy, validate conservation/query plans, and test it in the frozen YASB build and summary pipeline.
4. Stop only the services required by the documented migration procedure.
5. Apply the additive production migration/reprojection, validate health and invariants, then restart services.
6. Verify current-day and cold-day timelines while `N:` is mounted; verify that an unavailable cold database hides its heatmap and icons together.
7. Record migration identity, counts, timings, backup path, and rollback procedure.

### Acceptance

- Production health, conservation, consumers, frozen UI, and summary pipeline pass.
- The pre-migration backup and rollback instructions are verified.
- Hot/cold availability semantics match the approved behavior.
- Both repository worktrees are clean and all phase commits are pushed.

### Commit

- Commit only source-controlled migration receipts/documentation generated by the procedure; never commit databases, icon blobs, logs, or credentials.

## Rollback and compatibility

- Before Phase 6, rollback is switching the YASB consumer to its v1 path; production data is untouched.
- The production migration is additive. An older YASB build continues reading v1.
- Reprojection writes a new generation or uses the repository's verified transactional migration path; it never edits a cold generation in place.
- If conservation, consumer verification, or frozen UI acceptance fails, keep the new code branches but restore services to the pre-migration graph identity.

## Phase evidence log

Each completed phase appends:

- commands and result summaries;
- measured latency/counter baselines relevant to that phase;
- affected repository commit IDs and pushed branches;
- operational artifacts or screenshots, excluding runtime databases and secrets.

### Phase 0 evidence — 2026-10-23

- Branches verified clean and tracking their remotes:
  - `yasb-fork/feat/adaptive-app-activity-ribbons`
  - `event-logger/feat/adaptive-app-activity-signals-v2`
- `yasb-fork`: `.venv\Scripts\python.exe -m pytest tests\test_pieces_density.py -q` — 33 tests and 2 subtests passed in 2.44 s.
- `event-logger`: `cargo test screenpipe:: --lib` — 70 passed, 257 filtered out, test execution 4.31 s.
- `event-logger`: `cargo fmt --all -- --check` — passed.
- The live v1 app query plan uses `idx_source_app_activity_minutes_minute` for the bounded minute predicate. The view still needs temporary B-trees for grouping and final ordering; v2 plan tests must prevent regression to an unbounded source-fact scan and should evaluate whether the final sort can be reduced.
- A read-only 24-hour production query returned 317 v1 app-minute rows. Ten process-level SQLite CLI samples measured 13.93 ms minimum, 17.37 ms median, and 48.55 ms maximum/p95. This includes CLI startup and is retained only as a before-change diagnostic, not as the worker latency acceptance measurement.
- No database, bundle, configuration, or runtime service was modified.

### Phase 1 evidence — 2026-10-23

- `event-logger` commit `0bd63f7` (`feat(screenpipe): add conserved app activity v2 signals`) was pushed to `feat/adaptive-app-activity-signals-v2`.
- Graph schema v6 adds keyed global, app, and unattributed v2 signal facts plus `timeline_app_minutes_v2` and `timeline_global_minutes_v2`; the v1 view has an exact column regression assertion and remains unchanged.
- New exact source packs classify direct input before non-direct activity, retain raw/effective/suppressed counts, exclude focus-only events from both signals, and preserve v1 projection behavior independently.
- Every source-pack transaction verifies per-minute direct and non-direct conservation before commit. Graph migration verification rejects conservation violations, and corrupted app totals remain visible as negative residuals rather than being clamped.
- Bounded v2 app/global query-plan tests confirm use of minute-led indexes.
- `cargo test` — 330 library tests plus 38 binary tests passed; no failures.
- `cargo fmt --all -- --check` and `cargo clippy --all-targets --all-features -- -D warnings` — passed.
- No production database, cold bundle, configuration, or running service was modified.

