# Adaptive App Activity Ribbons Design

## Intent

Replace the current timeline icon columns with a compact, glanceable activity map that answers four questions without requiring hover:

1. Which applications dominated which time ranges?
2. Where did applications overlap or alternate?
3. Where was the user directly interacting, and where was the foreground application only producing observable activity?
4. How much of the global activity can and cannot be attributed to a specific application?

The design keeps the existing full-width Pieces Density overlay and its time ruler. It changes both the app layer above the ruler and the global density layer below it into a symmetric two-signal visualization.

## Goals

- Make every application range visually attributable through stable color, outline, icon, and interval geometry.
- Preserve a visible mark for every application with at least one confidently attributed direct input event.
- Represent direct input and non-direct observable activity separately rather than combining them into one score.
- Merge redundant repetitions so one long-running activity component normally has one icon.
- Allow overlapping application ranges while using the fixed overlay height efficiently.
- Keep unattributed activity visible and auditable.
- Preserve cold-storage behavior: if a timeline's database is available, its icons and cached colors are available from the same graph bundle; if the database is unavailable, neither is shown.
- Keep painting free of database access, filesystem I/O, image decoding, or expensive layout work.
- Treat latency, memory, query plans, and repaint cost as acceptance criteria in every implementation phase rather than a final optimization pass.

## Non-goals

- Inferring CPU/GPU computation that creates no Screenpipe-visible frame or UI change.
- Claiming that foreground residency alone means user attention.
- Showing every background process.
- Turning the overlay into an expandable task manager or full project-planning UI.
- Replacing the existing detailed hover tooltip with labels permanently rendered over the timeline.

## Anti-goals

- Do not let a low input count become invisible merely because fill opacity is low.
- Do not mix two applications' density curves into one ambiguous curve.
- Do not let animated video, terminal spinners, title animation, or duplicate accessibility rows dominate raw event counts.
- Do not force one permanent row per application; that would exceed the approximately 85 px overlay height.
- Do not make each application normalize its own peak to full height; cross-application comparison must remain meaningful.
- Do not break existing consumers of `timeline_app_minutes_v1`.

## Approaches considered

### A. Stage-centered icon columns

This is the current implementation. It is compact, but it encodes a range as a point. Users cannot infer start/end boundaries, the same application repeats across stages, and lower-ranked icons disappear under spatial pressure.

### B. Fixed application swimlanes

One row per application makes ranges unambiguous, but row count grows with the number of applications rather than actual temporal overlap. It does not fit the current overlay height.

### C. Adaptive interval ribbons with inverted activity clouds — selected

Applications own time-bounded components, not permanent rows. Non-overlapping components reuse vertical tracks. Each component combines a direct-input ribbon, a downward non-direct activity cloud, a stable app color, and one movable icon label. Only genuinely overlapping components consume another track.

This approach was selected because it preserves range semantics and overlap while remaining height-bounded.

## Visual grammar

### Per-application layer

Each application activity component contains:

- **Stable outline and baseline:** always visible in the application's representative color.
- **Direct-input ribbon:** fill opacity and luminance encode per-minute direct input density.
- **Non-direct activity cloud:** a fixed-transparency area extends downward from the ribbon. Height is the primary encoding for per-minute effective activity count; same-hue luminance is secondary.
- **Passive segment texture:** when non-direct activity exists without direct input, the ribbon uses sparse diagonal hatching plus a thin dashed border.
- **Focus-only bridge:** a dotted hairline may connect two credible segments when foreground continuity supports the bridge. A focus-only interval does not create a component by itself and adds no activity weight.
- **Icon:** one icon per activity component, normally near its weighted median.
- **Minimum direct marker:** even a single confidently attributed direct event leaves a stable-color time pin if there is not enough width for a ribbon or icon.

The activity cloud points downward so it reads as a suspended app-specific cloud and is visually distinct from the global heatmap.

### Dominance without exclusive partitioning

Dominance is evaluated per minute from the two separately normalized signals, with direct input taking precedence over non-direct activity. The strongest application receives a brighter/thicker baseline and front-most draw order. Applications within a versioned similarity ratio may be co-dominant, so dominance ranges may overlap; lower-ranked credible activity remains visible. This deliberately avoids forcing the day into one mutually exclusive application partition.

### Global layer

The global layer mirrors the same two signals:

- **Global direct-input ribbon:** the aggregate direct input density for the minute, encoded by fill opacity and luminance.
- **Global non-direct activity mountain:** effective non-direct frame/UI activity grows upward from the global ribbon.

The existing mixed `effective_frame_count + effective_ui_event_count` series must not remain the semantic source, because direct UI/input events would then appear in both the ribbon and the mountain.

### Unattributed residual

For every minute and signal:

```text
sum(attributed application activity) + unattributed residual = global activity
```

The residual is directly visible as a neutral-gray hatched portion of the global ribbon or mountain. It does not receive an app icon. If the residual is anomalously large, a small `?` marker may be shown. Hover exposes the residual count, percentage, confidence, and attribution failure reason.

## Signal definitions

### Direct input

Direct input is the deduplicated, attributed count of keyboard, text, click, scroll, clipboard, and other explicitly classified user-input observations. Category counts remain available for hover and auditing.

Direct input determines ribbon heat. It is evidence of interaction, not duration by itself.

### Non-direct observable activity

Non-direct activity is the deduplicated count of effective foreground frames and effective non-direct UI/accessibility changes after direct input observations have been excluded.

It indicates that the foreground application produced observable changes without direct input. It does not prove that the user was attentive or that a process was doing background computation.

### Focus-only residency

Focus-only residency carries no direct or non-direct count. It can support a dotted connector between nearby credible segments, but cannot independently create or enlarge an activity component.

## Scaling

Direct input and non-direct activity use separate scales because their distributions differ substantially.

Within the app layer, each signal uses one cross-application scale for the selected timeline:

1. apply `log1p(count)`;
2. derive a robust upper bound from a high percentile of non-zero minute values;
3. clamp values at that bound;
4. map the result to opacity/luminance or cloud height.

The global layer uses the same algorithm but a separate global scale. It must not share the app-layer numeric bound because aggregation would keep it saturated.

Zero remains visually distinct from the minimum non-zero value. Opacity affects fill only; stable outlines, baselines, and minimum direct markers never fade below their visibility floor.

## Activity components and redundancy removal

The semantic unit is an activity component: a maximal run of credible activity for one canonical application.

- Direct or non-direct active minutes extend the component.
- Short gaps may be bridged only when foreground continuity, surrounding signal strength, and gap size jointly support continuity.
- The bridge threshold is adaptive but bounded and deterministic; it must not join long focus-only absences.
- Clearly separated work periods create separate components and therefore separate icons.
- Components may overlap across applications and may have intersections or unions in wall-clock time.
- Pixel-level merging is a later display operation and must not rewrite semantic component boundaries.

The result is that a long continuous Code session normally shows one Code icon, while a later unrelated Code session shows another.

## Two-dimensional layout

### Interval track packing

Tracks belong to time intervals, not to applications for the whole day.

- Sort components using stable priority: direct evidence, total engaged score, duration, then canonical app ID.
- Assign each component to the lowest track whose occupied pixel contour does not conflict.
- Different applications can reuse the same track when their time ranges do not overlap.
- A component moves to another track only where its range, cloud contour, icon box, or leader line would collide.
- The layout remains fixed-height by default; it degrades detail rather than changing overlay height during use.

### Icon placement

The weighted activity median is the logical icon anchor, not an immutable draw coordinate.

1. Search a bounded horizontal neighborhood around the anchor.
2. Prefer local valleys with low direct-ribbon and activity-cloud information.
3. Avoid ruler labels, control overlays, higher-priority icons, and high-density peaks.
4. If displaced, draw a short subtle leader line to the logical anchor.
5. If no full icon fits, reduce to the configured minimum icon size; if that still does not fit, retain the time pin and make the icon available in hover.

### Similar-icon stacking

Borrow the workspace preview's diagonal-stack behavior only for icon boxes:

- stack only when activity components have high time overlap, similar normalized signal distributions, and colliding icon boxes;
- offset each additional icon down and right by approximately 15% of icon size;
- cap total stack expansion at half an icon;
- reduce opacity for rear icons while keeping the highest-priority icon fully legible;
- retain separate parallel app-colored signal outlines; never merge the underlying facts into one curve.

### Level of detail under pressure

Degrade in this order:

1. reduce decorative cloud detail while retaining envelope height;
2. reduce icon size;
3. stack eligible icons;
4. replace a full icon with a color pin and hover identity;
5. retain all direct-event pins even when lower-confidence passive-only components are summarized.

## App color identity

Color resolution is keyed by icon content identity, not process name alone:

1. extract a representative color from the current icon;
2. reject transparent, near-white, near-black, and low-chroma samples;
3. correct luminance and saturation for the overlay background;
4. resolve collisions against already registered canonical app colors when the color is first assigned;
5. fall back to a stable `canonical_app_id` hash palette when extraction fails.

The resolved base color is persisted and does not change when neighboring apps enter or leave the timeline. If two persisted colors remain too similar when adjacent, the renderer distinguishes them with outline luminance and texture rather than changing their identities dynamically.

The derived color metadata is stored alongside the icon version metadata in the graph bundle; image bytes remain in the content-addressed icon asset store rather than the SQLite database. YASB caches the resolved color by `(icon_sha256, color_algorithm_version)`.

Cold rotation copies the database, icon assets, and their color metadata as one bundle. If the bundle database is unavailable because the cold disk is unmounted, neither the heatmap nor its icons/colors are shown.

## Graph data contract

### Additive schema evolution

Keep `timeline_app_minutes_v1` unchanged for existing consumers. Introduce additive v2 contracts rather than mutating v1 semantics.

The app-minute fact contract needs at least:

```text
minute_start
canonical_app_id
display_name
icon_sha256
direct_input_count
key_count
click_count
scroll_count
text_count
clipboard_count
non_direct_activity_count
effective_frame_count
effective_non_direct_ui_event_count
foreground_milliseconds
interaction_level
attribution_confidence_milli
projection_kind
scoring_version
```

The global-minute contract needs:

```text
minute_start
direct_input_count
non_direct_activity_count
attributed_direct_input_count
attributed_non_direct_activity_count
unattributed_direct_input_count
unattributed_non_direct_activity_count
unattributed_reason_counts_json
```

The ingestion pipeline must classify direct input before computing non-direct UI activity. Counts must use the existing effective/suppressed deduplication decisions, not raw Screenpipe row counts. The residual reason payload uses a versioned, bounded vocabulary (for example missing app identity, ambiguous focus boundary, inaccessible source, and historical approximation), so hover does not parse free-form diagnostic text.

### Historical compatibility

- Exact v2 facts are produced for newly processed source acquisitions.
- Existing exact source packs can be deterministically reprojected when raw evidence is available.
- Historical approximations retain `projection_kind='historical_approx'` and lower confidence; the UI does not invent separated counts when evidence cannot support them.
- A graph without v2 contracts falls back to the current v1 icon timeline and mixed global heatmap rather than failing entirely.
- Production graph migration happens after implementation phases and consumer verification, not during exploratory UI work.

### Consumer impact

The event-logger YouTube summary pipeline may consume the same v2 app/global minute facts. It should receive explicit direct/non-direct fields rather than infer them from rendered geometry or YASB scoring.

## Hover behavior

The overlay remains non-activating and transparent to input. Existing cursor polling determines the hovered minute/component.

Hover shows:

- application identity and icon;
- component start/end and logical anchor;
- direct input total and category breakdown;
- non-direct activity total and frame/UI breakdown;
- foreground residency separately from activity;
- confidence and exact versus historical projection status;
- global totals;
- attributed and unattributed residual counts and percentages;
- why evidence was unattributed when known.

Hover supplements the visualization; it must not be required to discover basic ownership or range.

## Rendering and performance

Performance is a design constraint shared by event-logger and YASB. Each phase must add or update measurements for the work it introduces.

### Storage and query path

- Back v2 minute contracts with indexes beginning with `minute_start`; do not make YASB execute an unbounded historical `GROUP BY`.
- Produce direct, non-direct, attributed, and residual minute facts during graph projection so the UI never reclassifies raw observations.
- Read all required app and global facts for one selected interval through one read-only connection and a bounded number of prepared queries.
- Preserve the current acquisition watermark/earliest-changed-minute contract. A live refresh replaces only affected minute suffixes and recomputes only components intersecting that suffix.
- Cache closed sessions by graph identity and data-version watermark. Navigating back to an unchanged session should perform no database work.
- Validate query plans in migration tests and reject accidental full scans on the production-sized fixture.

### Computation path

- Perform queries, color extraction, component building, scale calculation, path generation, and packing in a cancellable worker generation; stale generations never reach the GUI.
- Keep component construction linear in app-minute facts.
- Use a sweep-line/free-track structure for interval packing rather than pairwise component comparison.
- Use a small spatial grid for icon/cloud collision candidates; only the bounded candidate set may use detailed contour tests.
- Downsample each cloud to drawable pixel columns before building paths. No path has more horizontal samples than the overlay width requires.
- Reuse unchanged per-component envelopes and icon candidates across incremental refreshes.
- Make deterministic ordering part of the cache key behavior so refreshes do not trigger needless track movement.

### GUI and cache path

- `paintEvent` only draws cached `QPainterPath`/polygon geometry, pens, brushes, and pixmaps; it performs no SQL, filesystem I/O, PNG decode, color analysis, resampling, component building, or collision search.
- Store immutable draw geometry and hit-test intervals in the snapshot handed to the GUI thread.
- Recompute geometry only when relevant data, overlay dimensions, configuration, icon/color metadata, or device-pixel ratio changes.
- Cache hatch brushes and app-color pens rather than constructing them per component per frame.
- Keep decoded/scaled pixmaps in the existing bounded LRU and representative colors in a bounded `(icon_sha256, color_algorithm_version)` cache.
- Bound all caches by entries and estimated bytes. Cache eviction must not affect correctness or stable color identity.
- Coalesce resize, data, and configuration invalidations into one queued layout rebuild.

### Initial performance budgets

On the existing development machine with a 24-hour/1,440-minute interval and production-like app cardinality:

- warm incremental refresh worker time: p95 at or below 25 ms;
- cached closed-session selection: p95 at or below 25 ms excluding cold-disk wake latency;
- full cold local query plus layout: p95 at or below 150 ms;
- GUI-thread snapshot swap and update scheduling: at or below 4 ms;
- cached 3,840 px overlay paint: p95 at or below 4 ms;
- steady state: no periodic icon decoding or color extraction and no repaint when neither data nor hover state changed.

Cold storage on `N:` is measured separately because device wake and filesystem latency are environmental. The UI must remain responsive and show loading/fallback state rather than block the GUI thread.

These are initial regression budgets, not permission to hide work outside instrumentation. If real production fixtures show a budget is unrealistic, change it only with captured before/after evidence and an explicit design update.

### Instrumentation and performance tests

- Record structured timings for SQL, cache lookup, component rebuild, scale calculation, packing, path construction, pixmap preparation, and paint.
- Add deterministic large-fixture benchmarks for sparse, typical, dense-overlap, one-event-per-app, and long-continuous-app cases.
- Add an incremental benchmark where only the final minute changes; it must demonstrate bounded suffix work rather than full-day rebuilding.
- Track component count, path vertex count, collision candidate count, cache hit/miss/eviction totals, and snapshot byte estimates.
- Include frozen-build and both-monitor measurements because Python source performance alone is not the deployed behavior.

The overall memory target remains bounded by the selected interval and configured caches, never by complete graph history.

## Configuration

Defaults should work without changing the user's YAML. New configuration belongs under the existing app timeline options and should expose only stable product choices, such as:

- show/hide direct ribbon;
- show/hide non-direct cloud;
- show/hide unattributed residual;
- maximum app tracks and icon sizes;
- passive-only evidence threshold;
- optional legacy fallback behavior.

Scoring coefficients, percentile bounds, collision tolerances, and bridge heuristics should initially remain versioned implementation policy rather than a large public configuration surface.

## Testing

### event-logger

- Direct observations are excluded from non-direct activity counts.
- Effective/suppressed decisions survive aggregation.
- App sums plus residual equal global totals for both signals.
- Residual never becomes negative; violations fail verification rather than silently clamping corrupt facts.
- v1 remains stable while v2 exposes the added fields.
- Exact and historical projection kinds remain distinguishable.
- Hot/cold bundle migration preserves icon and color metadata references.
- Production-sized query-plan and projection benchmarks stay within the agreed budgets.

### YASB

- One direct event always produces a visible pin.
- Continuous activity produces one component/icon; a meaningful gap produces two.
- Non-overlapping intervals reuse a track.
- Overlapping contours do not collide after layout.
- Icon valley search avoids stronger nearby density peaks.
- Similar eligible icons stack with bounded 15% offsets; dissimilar facts never merge.
- Cross-app and global scales use separate robust bounds.
- App clouds point down and the global mountain points up.
- Residual is directly visible and present in hover details.
- Layout and pixmap caches invalidate only on relevant changes.
- Incremental refresh touches only the changed suffix and intersecting components.
- Dense-overlap packing uses bounded collision candidates rather than quadratic all-pairs checks.
- Source and frozen-build performance budgets pass on the reference machine.
- Frozen-build screenshots cover typical, sparse, dense, multi-monitor, and high-DPI cases.

## Rollout

1. Add and verify additive graph v2 facts/views in an event-logger feature branch.
2. Reproject a copied/rehearsal graph and prove conservation invariants.
3. Add YASB v2 query models and fallback behavior.
4. Implement components, scaling, packing, color extraction/cache, and hover models with tests.
5. Implement cached Qt rendering and screenshot regressions.
6. Validate both monitors, live refresh, session navigation, cold bundle mounted/unmounted behavior, and the frozen dev build.
7. Commit and push each repository phase serially.
8. Run the consumer verifier, back up the production graph to cold storage, then perform the production additive migration.

## Decisions made during design

- Adaptive interval ribbons were selected over icon columns and fixed swimlanes.
- Direct input and non-direct activity are separate visual and data signals.
- Passive activity uses sparse hatching plus a thin dashed border.
- App activity clouds point downward; the global activity mountain points upward.
- Every confidently attributed direct event leaves a visible mark.
- Scales are cross-app and robust, not normalized independently per app.
- Tracks are interval-packed; icons can move into local density valleys.
- Only eligible icon boxes stack; underlying curves remain separate.
- Unattributed residual is directly visible and included in hover details.
- Representative icon color is preferred, with corrected stable hash color as fallback.
- The overlay remains fixed-height by default and degrades detail under pressure.
