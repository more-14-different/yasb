import math
from dataclasses import dataclass, replace

from core.widgets.yasb.pieces_density_source import AppMinuteRecord


@dataclass(frozen=True)
class StageApp:
    app_id: str
    display_name: str
    icon_sha256: str | None
    score: int
    engaged_milliseconds: int
    foreground_milliseconds: int
    direct_event_count: int
    interaction_level: str
    confidence_milli: int


@dataclass(frozen=True)
class TimelineStage:
    start_bucket: int
    end_bucket: int
    anchor_bucket: float
    apps: tuple[StageApp, ...]

    @property
    def dominant_app_id(self) -> str | None:
        return self.apps[0].app_id if self.apps else None


@dataclass(frozen=True)
class IconPlacement:
    app: StageApp
    x: float
    y: float
    size: float
    opacity: float
    stack_depth: int


@dataclass(frozen=True)
class StageLayout:
    stage: TimelineStage
    icons: tuple[IconPlacement, ...]


def _cosine(left: TimelineStage, right: TimelineStage) -> float:
    left_scores = {app.app_id: app.score for app in left.apps}
    right_scores = {app.app_id: app.score for app in right.apps}
    dot = sum(score * right_scores.get(app_id, 0) for app_id, score in left_scores.items())
    left_norm = math.sqrt(sum(score * score for score in left_scores.values()))
    right_norm = math.sqrt(sum(score * score for score in right_scores.values()))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _aggregate(records: list[AppMinuteRecord], start_bucket: int, end_bucket: int) -> TimelineStage:
    grouped: dict[str, list[AppMinuteRecord]] = {}
    for record in records:
        if start_bucket <= record.bucket < end_bucket:
            grouped.setdefault(record.canonical_app_id, []).append(record)
    apps: list[StageApp] = []
    for app_id, items in grouped.items():
        score = sum(item.engaged_score_milli for item in items)
        direct = sum(item.direct_event_count for item in items)
        level = "direct" if any(item.interaction_level == "direct" for item in items) else "uncertain"
        if direct == 0:
            level = "passive"
        apps.append(
            StageApp(
                app_id=app_id,
                display_name=items[-1].display_name,
                icon_sha256=next((item.icon_sha256 for item in reversed(items) if item.icon_sha256), None),
                score=score,
                engaged_milliseconds=sum(item.engaged_milliseconds for item in items),
                foreground_milliseconds=sum(item.foreground_milliseconds for item in items),
                direct_event_count=direct,
                interaction_level=level,
                confidence_milli=min(item.attribution_confidence_milli for item in items),
            )
        )
    apps.sort(key=lambda app: (app.score, app.direct_event_count, app.app_id), reverse=True)
    total_score = sum(app.score for app in apps)
    if total_score:
        anchor = sum(
            record.bucket * record.engaged_score_milli
            for record in records
            if start_bucket <= record.bucket < end_bucket
        )
        anchor /= total_score
    else:
        anchor = (start_bucket + end_bucket - 1) / 2
    return TimelineStage(start_bucket, end_bucket, anchor, tuple(apps))


def build_timeline_stages(records: list[AppMinuteRecord], bucket_count: int) -> list[TimelineStage]:
    """Build stable foreground-work stages; one-minute dominant noise stays in its neighbour."""
    if not records or bucket_count <= 0:
        return []
    by_bucket: dict[int, list[AppMinuteRecord]] = {}
    for record in records:
        if 0 <= record.bucket < bucket_count:
            by_bucket.setdefault(record.bucket, []).append(record)
    dominant: list[str | None] = []
    for bucket in range(bucket_count):
        candidates = [item for item in by_bucket.get(bucket, []) if item.direct_event_count > 0]
        dominant.append(
            max(candidates, key=lambda item: item.engaged_score_milli).canonical_app_id if candidates else None
        )

    runs: list[tuple[int, int, str | None]] = []
    start = 0
    for index in range(1, bucket_count + 1):
        if index == bucket_count or dominant[index] != dominant[start]:
            runs.append((start, index, dominant[start]))
            start = index
    boundaries = [0]
    for run_start, run_end, app_id in runs:
        run_length = run_end - run_start
        if run_start and (run_length >= 2 or (app_id is None and run_length >= 2)):
            boundaries.append(run_start)
    boundaries.append(bucket_count)

    stages = [
        _aggregate(
            [record for bucket in range(left, right) for record in by_bucket.get(bucket, [])],
            left,
            right,
        )
        for left, right in zip(boundaries, boundaries[1:])
    ]
    stages = [stage for stage in stages if stage.apps]
    merged: list[TimelineStage] = []
    for stage in stages:
        if merged and (merged[-1].dominant_app_id == stage.dominant_app_id or _cosine(merged[-1], stage) >= 0.72):
            previous = merged.pop()
            merged.append(_combine_stages(previous, stage))
        else:
            merged.append(stage)
    return merged


def merge_similar_for_pixels(
    stages: list[TimelineStage], width: float, minimum_footprint: float
) -> list[TimelineStage]:
    if len(stages) < 2 or width <= 0:
        return stages
    result: list[TimelineStage] = []
    for stage in stages:
        if result:
            previous = result[-1]
            distance = abs(stage.anchor_bucket - previous.anchor_bucket) * width / max(stages[-1].end_bucket, 1)
            if distance < minimum_footprint and (
                previous.dominant_app_id == stage.dominant_app_id or _cosine(previous, stage) >= 0.55
            ):
                combined_apps = _merge_stage_apps(previous.apps, stage.apps)
                result[-1] = TimelineStage(
                    previous.start_bucket,
                    stage.end_bucket,
                    _weighted_anchor(previous, stage),
                    combined_apps,
                )
                continue
        result.append(stage)
    return result


def _combine_stages(left: TimelineStage, right: TimelineStage) -> TimelineStage:
    return TimelineStage(
        left.start_bucket,
        right.end_bucket,
        _weighted_anchor(left, right),
        _merge_stage_apps(left.apps, right.apps),
    )


def _merge_stage_apps(left: tuple[StageApp, ...], right: tuple[StageApp, ...]) -> tuple[StageApp, ...]:
    grouped: dict[str, StageApp] = {}
    for app in (*left, *right):
        old = grouped.get(app.app_id)
        if old is None:
            grouped[app.app_id] = app
        else:
            grouped[app.app_id] = replace(
                old,
                icon_sha256=app.icon_sha256 or old.icon_sha256,
                score=old.score + app.score,
                engaged_milliseconds=old.engaged_milliseconds + app.engaged_milliseconds,
                foreground_milliseconds=old.foreground_milliseconds + app.foreground_milliseconds,
                direct_event_count=old.direct_event_count + app.direct_event_count,
                interaction_level=(
                    "direct"
                    if "direct" in (old.interaction_level, app.interaction_level)
                    else "uncertain"
                    if "uncertain" in (old.interaction_level, app.interaction_level)
                    else "passive"
                ),
                confidence_milli=min(old.confidence_milli, app.confidence_milli),
            )
    return tuple(sorted(grouped.values(), key=lambda app: (app.score, app.app_id), reverse=True))


def _weighted_anchor(left: TimelineStage, right: TimelineStage) -> float:
    left_score = sum(app.score for app in left.apps)
    right_score = sum(app.score for app in right.apps)
    total = left_score + right_score
    return (
        (left.anchor_bucket * left_score + right.anchor_bucket * right_score) / total if total else right.anchor_bucket
    )


def layout_timeline_stages(
    stages: list[TimelineStage],
    width: float,
    band_height: float,
    *,
    band_top: float = 0,
    min_size: float = 12,
    max_size: float = 24,
    max_icons: int = 5,
    min_gap: float = 3,
    stack_similarity_ratio: float = 0.75,
    stack_offset_ratio: float = 0.28,
    show_passive_foreground: bool = True,
) -> list[StageLayout]:
    if not stages or width <= 0 or band_height < min_size:
        return []
    max_size = max(max_size, min_size)
    stages = merge_similar_for_pixels(stages, width, max_size + min_gap)
    duration = max(stage.end_bucket for stage in stages)
    layouts: list[StageLayout] = []
    previous_right = -math.inf
    for stage in stages:
        anchor_x = min(max(stage.anchor_bucket / max(duration - 1, 1) * width, 0), width)
        ranked = [
            app
            for app in stage.apps
            if app.icon_sha256 and (show_passive_foreground or app.interaction_level != "passive")
        ]
        ranked = ranked[:max_icons]
        if not ranked:
            continue
        available = anchor_x - previous_right
        if available < max_size + min_gap:
            ranked = ranked[:3]
        if available < min_size + min_gap:
            continue
        maximum = max(app.score for app in ranked) or 1
        sizes = [min_size + (max_size - min_size) * math.sqrt(max(app.score, 0) / maximum) for app in ranked]
        placements: list[IconPlacement] = []
        visible_index = len(layouts)
        cursor = 1.0 if visible_index % 2 == 0 else band_height - 1.0
        previous_score = None
        stack_depth = 0
        for app, size in zip(ranked, sizes):
            similar = (
                previous_score is not None
                and min(app.score, previous_score) / max(app.score, previous_score, 1) >= stack_similarity_ratio
            )
            stack_depth = stack_depth + 1 if similar else 0
            if visible_index % 2 == 0:
                if cursor + size > band_height and placements:
                    break
                y = min(cursor, band_height - size)
                cursor = y + (size * stack_offset_ratio if similar else size + 1)
            else:
                if cursor - size < 0 and placements:
                    break
                y = max(cursor - size, 0)
                cursor = y + (size * (1 - stack_offset_ratio) if similar else -1)
            confidence_cap = max(0.3, min(app.confidence_milli / 1000, 1.0))
            if app.interaction_level == "passive":
                opacity = 0.5
            elif app.interaction_level == "uncertain":
                opacity = 0.4
            else:
                opacity = 0.78 + 0.2 * math.sqrt(max(app.score, 0) / maximum)
            opacity = max(0.58 if app.interaction_level == "direct" else 0.3, opacity - stack_depth * 0.07)
            placements.append(
                IconPlacement(
                    app,
                    min(max(anchor_x - size / 2 + stack_depth * size * stack_offset_ratio, 0), max(width - size, 0)),
                    y,
                    size,
                    min(opacity, confidence_cap),
                    stack_depth,
                )
            )
            previous_score = app.score
        if placements:
            # Keep every stage attached to the density timeline.  The alternating
            # direction controls the order inside a stage, not whether its icon
            # group hangs from the top or rises from the bottom of the band.
            bottom_shift = band_top + band_height - max(item.y + item.size for item in placements)
            if bottom_shift:
                placements = [replace(item, y=item.y + bottom_shift) for item in placements]
            previous_right = max(item.x + item.size for item in placements)
            layouts.append(StageLayout(stage, tuple(placements)))
    return layouts
