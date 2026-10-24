import hashlib
import heapq
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from core.widgets.yasb.pieces_density_source import AppMinuteRecord, GlobalMinuteRecord


@dataclass(frozen=True)
class ActivityLayoutPolicy:
    bridge_max_minutes: int = 2
    robust_percentile: float = 0.95
    max_tracks: int = 5
    track_gap_px: float = 2.0
    min_icon_size: float = 12.0
    max_icon_size: float = 24.0
    icon_search_radius_px: float = 72.0
    icon_search_step_px: float = 4.0
    stack_offset_ratio: float = 0.15
    max_stack_depth: int = 3
    stack_overlap_ratio: float = 0.65
    stack_similarity: float = 0.82


@dataclass(frozen=True)
class SignalScale:
    upper_log: float
    nonzero_samples: int
    percentile: float

    def normalize(self, count: int) -> float:
        if count <= 0:
            return 0.0
        if self.upper_log <= 0:
            return 1.0
        return min(math.log1p(count) / self.upper_log, 1.0)


@dataclass(frozen=True)
class AppColor:
    rgb: int
    source: str
    algorithm_version: int | None

    @property
    def hex_rgb(self) -> str:
        return f"#{self.rgb:06x}"


@dataclass(frozen=True)
class ActivityComponent:
    component_id: str
    app_id: str
    display_name: str
    icon_sha256: str | None
    color: AppColor
    start_bucket: int
    end_bucket: int
    direct_counts: tuple[int, ...]
    non_direct_counts: tuple[int, ...]
    foreground_milliseconds: tuple[int, ...]
    bridge_buckets: tuple[int, ...]
    total_direct: int
    total_non_direct: int
    total_engaged_score_milli: int
    confidence_milli: int
    anchor_bucket: float
    projection_kind: str

    @property
    def duration_buckets(self) -> int:
        return self.end_bucket - self.start_bucket

    @property
    def has_direct_pin(self) -> bool:
        return self.total_direct > 0


@dataclass(frozen=True)
class EnvelopeSample:
    pixel: int
    direct_level: float
    non_direct_level: float
    direct_count: int
    non_direct_count: int


@dataclass(frozen=True)
class GlobalEnvelopeSample:
    pixel: int
    direct_level: float
    non_direct_level: float
    residual_direct_level: float
    residual_non_direct_level: float
    direct_count: int
    non_direct_count: int
    residual_direct_count: int
    residual_non_direct_count: int


@dataclass(frozen=True)
class IconGeometry:
    x: float
    y: float
    size: float
    opacity: float
    stack_depth: int
    logical_anchor_x: float
    leader_from_x: float | None
    collision_candidates: int


@dataclass(frozen=True)
class ComponentGeometry:
    component: ActivityComponent
    track: int
    x_start: float
    x_end: float
    direct_pin_x: float | None
    bridge_segments: tuple[tuple[float, float], ...]
    envelope: tuple[EnvelopeSample, ...]
    icon: IconGeometry | None


@dataclass(frozen=True)
class ActivityLayoutMetrics:
    component_count: int
    visible_component_count: int
    hidden_component_count: int
    icon_count: int
    hidden_icon_count: int
    path_vertex_count: int
    collision_candidate_count: int
    reused_component_count: int
    estimated_bytes: int


@dataclass(frozen=True)
class ActivityLayoutSnapshot:
    bucket_count: int
    width: int
    band_height: float
    app_direct_scale: SignalScale
    app_non_direct_scale: SignalScale
    global_direct_scale: SignalScale
    global_non_direct_scale: SignalScale
    components: tuple[ComponentGeometry, ...]
    global_envelope: tuple[GlobalEnvelopeSample, ...]
    metrics: ActivityLayoutMetrics
    policy: ActivityLayoutPolicy


_HASH_PALETTE = (
    0x56B4E9,
    0xE69F00,
    0x009E73,
    0xCC79A7,
    0xD55E00,
    0x0072B2,
    0xB7A42C,
    0x7A6FDB,
    0x2FA89A,
    0xD86B88,
    0x5C9E3D,
    0xC47A35,
)


def robust_signal_scale(values: Iterable[int], percentile: float = 0.95) -> SignalScale:
    percentile = min(max(percentile, 0.5), 1.0)
    samples = sorted(math.log1p(value) for value in values if value > 0)
    if not samples:
        return SignalScale(0.0, 0, percentile)
    index = max(math.ceil(len(samples) * percentile) - 1, 0)
    return SignalScale(samples[index], len(samples), percentile)


def resolve_app_color(record: AppMinuteRecord) -> AppColor:
    if record.representative_color_rgb is not None and 0 <= record.representative_color_rgb <= 0xFFFFFF:
        return AppColor(record.representative_color_rgb, "persisted", record.color_algorithm_version)
    identity = record.activity_id or record.canonical_app_id
    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    return AppColor(_HASH_PALETTE[int.from_bytes(digest[:2], "big") % len(_HASH_PALETTE)], "stable_hash", None)


def _weighted_median_bucket(start_bucket: int, direct: Sequence[int], non_direct: Sequence[int]) -> float:
    weights = [left + right for left, right in zip(direct, non_direct)]
    total = sum(weights)
    if total <= 0:
        return start_bucket + max(len(weights) - 1, 0) / 2 + 0.5
    threshold = total / 2
    running = 0
    for offset, weight in enumerate(weights):
        running += weight
        if running >= threshold:
            return start_bucket + offset + 0.5
    return start_bucket + len(weights) - 0.5


def _component_from_run(
    app_id: str,
    minute_records: dict[int, AppMinuteRecord],
    active_buckets: list[int],
    bridge_buckets: list[int],
) -> ActivityComponent:
    start_bucket = active_buckets[0]
    end_bucket = active_buckets[-1] + 1
    records = [minute_records.get(bucket) for bucket in range(start_bucket, end_bucket)]
    direct = tuple(record.direct_input_count if record else 0 for record in records)
    non_direct = tuple(record.non_direct_activity_count if record else 0 for record in records)
    foreground = tuple(record.foreground_milliseconds if record else 0 for record in records)
    evidence_records = [record for record in records if record is not None]
    latest = evidence_records[-1]
    icon_sha256 = next((record.icon_sha256 for record in reversed(evidence_records) if record.icon_sha256), None)
    projection_kinds = {record.projection_kind for record in evidence_records}
    return ActivityComponent(
        component_id=f"{app_id}:{start_bucket}:{end_bucket}",
        app_id=app_id,
        display_name=latest.display_name,
        icon_sha256=icon_sha256,
        color=resolve_app_color(latest),
        start_bucket=start_bucket,
        end_bucket=end_bucket,
        direct_counts=direct,
        non_direct_counts=non_direct,
        foreground_milliseconds=foreground,
        bridge_buckets=tuple(bridge_buckets),
        total_direct=sum(direct),
        total_non_direct=sum(non_direct),
        total_engaged_score_milli=sum(record.engaged_score_milli for record in evidence_records),
        confidence_milli=min(record.attribution_confidence_milli for record in evidence_records),
        anchor_bucket=_weighted_median_bucket(start_bucket, direct, non_direct),
        projection_kind=projection_kinds.pop() if len(projection_kinds) == 1 else "mixed",
    )


def build_activity_components(
    records: Sequence[AppMinuteRecord],
    bucket_count: int,
    *,
    policy: ActivityLayoutPolicy = ActivityLayoutPolicy(),
    previous_components: Sequence[ActivityComponent] = (),
    changed_from_bucket: int | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> tuple[tuple[ActivityComponent, ...], int]:
    if bucket_count <= 0 or not records:
        return (), 0
    previous_by_app: dict[str, list[ActivityComponent]] = {}
    for component in previous_components:
        previous_by_app.setdefault(component.app_id, []).append(component)
    rebuild_base = max((changed_from_bucket or 0) - policy.bridge_max_minutes - 1, 0)
    rebuild_starts: dict[str, int] = {}
    reusable_components: list[ActivityComponent] = []
    if changed_from_bucket is not None:
        for app_id, prior in previous_by_app.items():
            safe = [component for component in prior if component.end_bucket <= rebuild_base]
            reusable_components.extend(safe)
            unsafe = [component for component in prior if component.end_bucket > rebuild_base]
            rebuild_starts[app_id] = min(
                [rebuild_base, *(component.start_bucket for component in unsafe)]
            )

    grouped: dict[str, dict[int, AppMinuteRecord]] = {}
    for record in records:
        if cancelled():
            return (), 0
        activity_id = record.activity_id or record.canonical_app_id
        rebuild_start = rebuild_starts.get(activity_id, rebuild_base)
        if 0 <= record.bucket < bucket_count and (changed_from_bucket is None or record.bucket >= rebuild_start):
            grouped.setdefault(activity_id, {})[record.bucket] = record

    components: list[ActivityComponent] = list(reusable_components)
    for app_id in sorted(grouped):
        minute_records = grouped[app_id]
        active = sorted(
            bucket
            for bucket, record in minute_records.items()
            if record.direct_input_count > 0 or record.non_direct_activity_count > 0
        )
        if not active:
            continue
        run_active = [active[0]]
        run_bridges: list[int] = []
        previous_bucket = active[0]
        for bucket in active[1:]:
            gap = bucket - previous_bucket - 1
            boundary_strength = (
                minute_records[previous_bucket].direct_input_count
                + minute_records[previous_bucket].non_direct_activity_count
                + minute_records[bucket].direct_input_count
                + minute_records[bucket].non_direct_activity_count
            )
            adaptive_limit = min(policy.bridge_max_minutes, 2 if boundary_strength >= 4 else 1)
            gap_buckets = list(range(previous_bucket + 1, bucket))
            foreground_continuity = bool(gap_buckets) and all(
                minute_records.get(gap_bucket) is not None
                and minute_records[gap_bucket].foreground_milliseconds > 0
                for gap_bucket in gap_buckets
            )
            if gap == 0 or (gap <= adaptive_limit and foreground_continuity):
                run_bridges.extend(gap_buckets)
                run_active.append(bucket)
            else:
                components.append(_component_from_run(app_id, minute_records, run_active, run_bridges))
                run_active = [bucket]
                run_bridges = []
            previous_bucket = bucket
        components.append(_component_from_run(app_id, minute_records, run_active, run_bridges))

    components.sort(
        key=lambda item: (
            item.start_bucket,
            -int(item.has_direct_pin),
            -(item.total_direct + item.total_non_direct),
            -item.duration_buckets,
            item.app_id,
        )
    )
    return tuple(components), len(reusable_components)


def _component_priority(component: ActivityComponent) -> tuple[int, int, int, str, int]:
    return (
        -int(component.has_direct_pin),
        -(component.total_direct + component.total_non_direct),
        -component.duration_buckets,
        component.app_id,
        component.start_bucket,
    )


def _pack_tracks(
    components: Sequence[ActivityComponent],
    bucket_count: int,
    width: int,
    max_tracks: int,
    gap_px: float,
) -> dict[str, int]:
    assignments: dict[str, int] = {}
    occupied: list[tuple[float, int]] = []
    free_tracks: list[int] = []
    track_count = 0
    ordered = sorted(components, key=lambda item: (item.start_bucket, _component_priority(item)))
    for component in ordered:
        left = component.start_bucket / bucket_count * width
        right = component.end_bucket / bucket_count * width
        while occupied and occupied[0][0] + gap_px <= left:
            _, track = heapq.heappop(occupied)
            heapq.heappush(free_tracks, track)
        if free_tracks:
            track = heapq.heappop(free_tracks)
        elif track_count < max_tracks:
            track = track_count
            track_count += 1
        else:
            assignments[component.component_id] = -1
            continue
        assignments[component.component_id] = track
        heapq.heappush(occupied, (right, track))
    return assignments


def _downsample_component(
    component: ActivityComponent,
    bucket_count: int,
    width: int,
    direct_scale: SignalScale,
    non_direct_scale: SignalScale,
) -> tuple[EnvelopeSample, ...]:
    columns: dict[int, list[float | int]] = {}
    for offset, (direct, non_direct) in enumerate(zip(component.direct_counts, component.non_direct_counts)):
        bucket = component.start_bucket + offset
        pixel = min(int(bucket / bucket_count * width), width - 1)
        values = columns.setdefault(pixel, [0.0, 0.0, 0, 0])
        values[0] = max(float(values[0]), direct_scale.normalize(direct))
        values[1] = max(float(values[1]), non_direct_scale.normalize(non_direct))
        values[2] = int(values[2]) + direct
        values[3] = int(values[3]) + non_direct
    return tuple(
        EnvelopeSample(pixel, float(values[0]), float(values[1]), int(values[2]), int(values[3]))
        for pixel, values in sorted(columns.items())
    )


def _downsample_global(
    records: Sequence[GlobalMinuteRecord],
    bucket_count: int,
    width: int,
    direct_scale: SignalScale,
    non_direct_scale: SignalScale,
) -> tuple[GlobalEnvelopeSample, ...]:
    columns: dict[int, list[float | int]] = {}
    for record in records:
        if not 0 <= record.bucket < bucket_count:
            continue
        pixel = min(int(record.bucket / bucket_count * width), width - 1)
        values = columns.setdefault(pixel, [0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0])
        counts = (
            record.direct_input_count,
            record.non_direct_activity_count,
            record.unattributed_direct_input_count,
            record.unattributed_non_direct_activity_count,
        )
        for index, count in enumerate(counts):
            values[index] = int(values[index]) + count
        values[4] = max(float(values[4]), direct_scale.normalize(counts[0]))
        values[5] = max(float(values[5]), non_direct_scale.normalize(counts[1]))
        values[6] = max(float(values[6]), direct_scale.normalize(counts[2]))
        values[7] = max(float(values[7]), non_direct_scale.normalize(counts[3]))
    return tuple(
        GlobalEnvelopeSample(
            pixel,
            float(values[4]),
            float(values[5]),
            float(values[6]),
            float(values[7]),
            *(int(value) for value in values[:4]),
        )
        for pixel, values in sorted(columns.items())
    )


def _bridge_segments(component: ActivityComponent, bucket_count: int, width: int) -> tuple[tuple[float, float], ...]:
    if not component.bridge_buckets:
        return ()
    segments: list[tuple[int, int]] = []
    start = previous = component.bridge_buckets[0]
    for bucket in component.bridge_buckets[1:]:
        if bucket != previous + 1:
            segments.append((start, previous + 1))
            start = bucket
        previous = bucket
    segments.append((start, previous + 1))
    return tuple((left / bucket_count * width, right / bucket_count * width) for left, right in segments)


type _Box = tuple[float, float, float, float]


class _SpatialGrid:
    def __init__(self, cell_size: float):
        self.cell_size = max(cell_size, 1.0)
        self.cells: dict[tuple[int, int], list[tuple[_Box, ActivityComponent, int]]] = {}

    def _keys(self, box: _Box) -> Iterable[tuple[int, int]]:
        left, top, right, bottom = box
        for x in range(math.floor(left / self.cell_size), math.floor(right / self.cell_size) + 1):
            for y in range(math.floor(top / self.cell_size), math.floor(bottom / self.cell_size) + 1):
                yield x, y

    def candidates(self, box: _Box) -> list[tuple[_Box, ActivityComponent, int]]:
        found: dict[int, tuple[_Box, ActivityComponent, int]] = {}
        for key in self._keys(box):
            for item in self.cells.get(key, ()):
                found[id(item)] = item
        return list(found.values())

    def add(self, box: _Box, component: ActivityComponent, stack_depth: int):
        item = (box, component, stack_depth)
        for key in self._keys(box):
            self.cells.setdefault(key, []).append(item)


def _boxes_overlap(left: _Box, right: _Box) -> bool:
    return left[0] < right[2] and left[2] > right[0] and left[1] < right[3] and left[3] > right[1]


def _component_signal(component: ActivityComponent, bucket: int) -> float:
    offset = bucket - component.start_bucket
    if not 0 <= offset < component.duration_buckets:
        return 0.0
    return component.direct_counts[offset] + component.non_direct_counts[offset]


def _stack_eligible(left: ActivityComponent, right: ActivityComponent, policy: ActivityLayoutPolicy) -> bool:
    overlap = max(0, min(left.end_bucket, right.end_bucket) - max(left.start_bucket, right.start_bucket))
    if overlap / max(min(left.duration_buckets, right.duration_buckets), 1) < policy.stack_overlap_ratio:
        return False
    start = min(left.start_bucket, right.start_bucket)
    end = max(left.end_bucket, right.end_bucket)
    left_values = [_component_signal(left, bucket) for bucket in range(start, end)]
    right_values = [_component_signal(right, bucket) for bucket in range(start, end)]
    dot = sum(a * b for a, b in zip(left_values, right_values))
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    similarity = dot / (left_norm * right_norm) if left_norm and right_norm else 0.0
    return similarity >= policy.stack_similarity


def _candidate_positions(target: float, left: float, right: float, radius: float, step: float) -> list[float]:
    positions = [min(max(target, left), right)]
    steps = max(math.ceil(radius / max(step, 1.0)), 0)
    for index in range(1, steps + 1):
        for sign in (-1, 1):
            candidate = target + sign * index * step
            if left <= candidate <= right:
                positions.append(candidate)
    return positions


def _local_density(component: ActivityComponent, x: float, bucket_count: int, width: int) -> float:
    bucket = min(max(int(x / max(width, 1) * bucket_count), component.start_bucket), component.end_bucket - 1)
    offset = bucket - component.start_bucket
    return math.log1p(component.direct_counts[offset]) + math.log1p(component.non_direct_counts[offset])


def _place_icons(
    components: Sequence[ActivityComponent],
    assignments: dict[str, int],
    bucket_count: int,
    width: int,
    band_height: float,
    policy: ActivityLayoutPolicy,
    reused: dict[str, tuple[ActivityComponent, IconGeometry]] | None = None,
) -> tuple[dict[str, IconGeometry], int]:
    reused = reused or {}
    placements = {component_id: item[1] for component_id, item in reused.items()}
    grid = _SpatialGrid(policy.max_icon_size)
    for component, icon in reused.values():
        grid.add((icon.x, icon.y, icon.x + icon.size, icon.y + icon.size), component, icon.stack_depth)
    collision_candidates = 0
    component_max = max((math.log1p(item.total_direct + item.total_non_direct) for item in components), default=1.0)
    track_height = band_height / max(policy.max_tracks, 1)
    for component in sorted(components, key=_component_priority):
        if component.component_id in reused:
            continue
        track = assignments.get(component.component_id, -1)
        if track < 0 or not component.icon_sha256:
            continue
        strength = math.log1p(component.total_direct + component.total_non_direct) / max(component_max, 1e-9)
        size = policy.min_icon_size + (policy.max_icon_size - policy.min_icon_size) * math.sqrt(strength)
        size = min(size, policy.max_icon_size, max(track_height * 1.35, policy.min_icon_size))
        x_start = component.start_bucket / bucket_count * width
        x_end = component.end_bucket / bucket_count * width
        logical_anchor = component.anchor_bucket / bucket_count * width
        left = max(x_start + size / 2, size / 2)
        right = min(x_end - size / 2, width - size / 2)
        if right < left:
            continue
        y = min(max(track * track_height + (track_height - size) / 2, 0.0), max(band_height - size, 0.0))
        candidates = _candidate_positions(
            logical_anchor,
            left,
            right,
            min(policy.icon_search_radius_px, max((right - left) / 2, 0.0)),
            policy.icon_search_step_px,
        )
        ranked: list[tuple[float, float, _Box, list[tuple[_Box, ActivityComponent, int]]]] = []
        for center_x in candidates:
            box = (center_x - size / 2, y, center_x + size / 2, y + size)
            nearby = grid.candidates(box)
            collision_candidates += len(nearby)
            collisions = [item for item in nearby if _boxes_overlap(box, item[0])]
            distance = abs(center_x - logical_anchor) / max(policy.icon_search_radius_px, 1.0)
            ranked.append((_local_density(component, center_x, bucket_count, width) * 4 + distance, center_x, box, collisions))
        ranked.sort(key=lambda item: (bool(item[3]), item[0], abs(item[1] - logical_anchor), item[1]))
        selected = next((item for item in ranked if not item[3]), None)
        stack_depth = 0
        if selected is None:
            for item in ranked:
                collisions = item[3]
                candidate_depth = max((collision[2] for collision in collisions), default=-1) + 1
                if candidate_depth > policy.max_stack_depth:
                    continue
                if collisions and all(_stack_eligible(component, collision[1], policy) for collision in collisions):
                    offset = min(candidate_depth * size * policy.stack_offset_ratio, size * 0.5)
                    center_x = min(max(item[1] + offset, left), right)
                    stacked_y = y + offset if y + offset + size <= band_height else max(y - offset, 0.0)
                    stacked_box = (center_x - size / 2, stacked_y, center_x + size / 2, stacked_y + size)
                    if stacked_box[3] <= band_height:
                        selected = (item[0], center_x, stacked_box, collisions)
                        stack_depth = candidate_depth
                        break
        if selected is None:
            continue
        _, center_x, box, _ = selected
        confidence_cap = min(component.confidence_milli / 1000, 1.0)
        strength_opacity = 0.72 + 0.25 * math.sqrt(strength)
        opacity = max(0.62, min(confidence_cap, strength_opacity) - stack_depth * 0.09)
        leader_from_x = logical_anchor if abs(center_x - logical_anchor) >= 2.0 else None
        placements[component.component_id] = IconGeometry(
            box[0],
            box[1],
            size,
            opacity,
            stack_depth,
            logical_anchor,
            leader_from_x,
            len(selected[3]),
        )
        grid.add(box, component, stack_depth)
    return placements, collision_candidates


def build_activity_layout(
    app_records: Sequence[AppMinuteRecord],
    global_records: Sequence[GlobalMinuteRecord],
    bucket_count: int,
    width: int,
    band_height: float,
    *,
    policy: ActivityLayoutPolicy = ActivityLayoutPolicy(),
    previous: ActivityLayoutSnapshot | None = None,
    changed_from_bucket: int | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> ActivityLayoutSnapshot:
    width = max(int(width), 1)
    bucket_count = max(int(bucket_count), 1)
    prior_components = tuple(item.component for item in previous.components) if previous else ()
    # A changed suffix is meaningful only relative to a layout from the same
    # interval. Another monitor can hit the shared data cache without owning a
    # prior layout; treating that cold build as incremental drops every
    # component before the suffix.
    incremental_from = changed_from_bucket if previous is not None else None
    components, reused = build_activity_components(
        app_records,
        bucket_count,
        policy=policy,
        previous_components=prior_components,
        changed_from_bucket=incremental_from,
        cancelled=cancelled,
    )
    app_direct_scale = robust_signal_scale(
        (record.direct_input_count for record in app_records if 0 <= record.bucket < bucket_count),
        policy.robust_percentile,
    )
    app_non_direct_scale = robust_signal_scale(
        (record.non_direct_activity_count for record in app_records if 0 <= record.bucket < bucket_count),
        policy.robust_percentile,
    )
    global_direct_scale = robust_signal_scale(
        (record.direct_input_count for record in global_records if 0 <= record.bucket < bucket_count),
        policy.robust_percentile,
    )
    global_non_direct_scale = robust_signal_scale(
        (record.non_direct_activity_count for record in global_records if 0 <= record.bucket < bucket_count),
        policy.robust_percentile,
    )
    assignments = _pack_tracks(components, bucket_count, width, policy.max_tracks, policy.track_gap_px)
    reusable_geometry: dict[str, ComponentGeometry] = {}
    if (
        previous is not None
        and previous.bucket_count == bucket_count
        and previous.width == width
        and previous.band_height == band_height
        and previous.policy == policy
        and previous.app_direct_scale == app_direct_scale
        and previous.app_non_direct_scale == app_non_direct_scale
        and incremental_from is not None
    ):
        previous_geometry = {item.component.component_id: item for item in previous.components}
        for component in components:
            old = previous_geometry.get(component.component_id)
            if old is not None and old.component is component and old.track == assignments.get(component.component_id, -1):
                reusable_geometry[component.component_id] = old
    reused_icons = {
        component_id: (geometry.component, geometry.icon)
        for component_id, geometry in reusable_geometry.items()
        if geometry.icon is not None
    }
    icons, collision_candidates = _place_icons(
        components,
        assignments,
        bucket_count,
        width,
        band_height,
        policy,
        reused_icons,
    )
    geometries: list[ComponentGeometry] = []
    path_vertices = 0
    hidden_icons = 0
    for component in components:
        if cancelled():
            break
        track = assignments.get(component.component_id, -1)
        old_geometry = reusable_geometry.get(component.component_id)
        if old_geometry is not None:
            path_vertices += len(old_geometry.envelope) * 2
            if component.icon_sha256 and old_geometry.icon is None:
                hidden_icons += 1
            geometries.append(old_geometry)
            continue
        envelope = _downsample_component(
            component,
            bucket_count,
            width,
            app_direct_scale,
            app_non_direct_scale,
        )
        path_vertices += len(envelope) * 2
        icon = icons.get(component.component_id)
        if component.icon_sha256 and icon is None:
            hidden_icons += 1
        geometries.append(
            ComponentGeometry(
                component,
                track,
                component.start_bucket / bucket_count * width,
                component.end_bucket / bucket_count * width,
                component.anchor_bucket / bucket_count * width if component.has_direct_pin else None,
                _bridge_segments(component, bucket_count, width),
                envelope,
                icon,
            )
        )
    global_envelope = _downsample_global(
        global_records,
        bucket_count,
        width,
        global_direct_scale,
        global_non_direct_scale,
    )
    path_vertices += len(global_envelope) * 4
    visible_components = sum(item.track >= 0 for item in geometries)
    estimated_bytes = (
        len(geometries) * 320
        + sum(len(item.envelope) * 40 + len(item.bridge_segments) * 16 for item in geometries)
        + len(global_envelope) * 64
    )
    metrics = ActivityLayoutMetrics(
        component_count=len(geometries),
        visible_component_count=visible_components,
        hidden_component_count=len(geometries) - visible_components,
        icon_count=len(icons),
        hidden_icon_count=hidden_icons,
        path_vertex_count=path_vertices,
        collision_candidate_count=collision_candidates,
        reused_component_count=reused,
        estimated_bytes=estimated_bytes,
    )
    return ActivityLayoutSnapshot(
        bucket_count,
        width,
        band_height,
        app_direct_scale,
        app_non_direct_scale,
        global_direct_scale,
        global_non_direct_scale,
        tuple(geometries),
        global_envelope,
        metrics,
        policy,
    )
