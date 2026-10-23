import json
import math
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class DensitySource(StrEnum):
    AUTO = "auto"
    SCREENPIPE_GRAPH = "screenpipe_graph"
    PIECES = "pieces"


@dataclass(frozen=True)
class ResolvedDensitySource:
    source: DensitySource
    database_path: str
    fallback_reason: str | None = None


@dataclass(frozen=True)
class ScreenpipeGraphFeatures:
    schema_version: int | None
    density_frame_column: str | None
    density_ui_column: str | None
    supports_acquisition_watermark: bool
    supports_app_timeline: bool
    supports_app_timeline_v2: bool = False
    supports_global_timeline_v2: bool = False
    supports_icon_colors: bool = False

    @property
    def supports_density(self) -> bool:
        return self.density_frame_column is not None and self.density_ui_column is not None


@dataclass(frozen=True)
class _ScreenpipeWatermark:
    max_rowid: int
    observed_at: str | None


@dataclass(frozen=True)
class _ScreenpipeRefreshState:
    watermark: _ScreenpipeWatermark
    earliest_changed_minute: float | None


@dataclass
class _CacheEntry:
    raw_buckets: list[int]
    refreshed_through: float
    complete: bool
    source_watermark: _ScreenpipeWatermark | None = None


@dataclass(frozen=True)
class AppMinuteRecord:
    bucket: int
    canonical_app_id: str
    display_name: str
    icon_sha256: str | None
    engaged_score_milli: int
    engaged_milliseconds: int
    foreground_milliseconds: int
    direct_event_count: int
    interaction_level: str
    attribution_confidence_milli: int
    direct_input_count: int = 0
    key_count: int = 0
    click_count: int = 0
    scroll_count: int = 0
    text_count: int = 0
    clipboard_count: int = 0
    other_input_count: int = 0
    non_direct_activity_count: int = 0
    effective_frame_count: int = 0
    effective_non_direct_ui_event_count: int = 0
    projection_kind: str = "exact"
    representative_color_rgb: int | None = None
    color_algorithm_version: int | None = None
    contract_version: int = 1


@dataclass(frozen=True)
class UnattributedReasonCount:
    signal_kind: str
    reason: str
    count: int


@dataclass(frozen=True)
class GlobalMinuteRecord:
    bucket: int
    direct_input_count: int
    non_direct_activity_count: int
    attributed_direct_input_count: int
    attributed_non_direct_activity_count: int
    unattributed_direct_input_count: int
    unattributed_non_direct_activity_count: int
    unattributed_reason_counts: tuple[UnattributedReasonCount, ...]


@dataclass(frozen=True)
class ActivityTimelineData:
    app_records: tuple[AppMinuteRecord, ...]
    global_records: tuple[GlobalMinuteRecord, ...]
    contract_version: int


@dataclass
class _AppCacheEntry:
    records: list[AppMinuteRecord]
    global_records: list[GlobalMinuteRecord]
    contract_version: int
    complete: bool
    source_watermark: _ScreenpipeWatermark | None = None


_APP_TIMELINE_COLUMNS = {
    "minute_start",
    "canonical_app_id",
    "display_name",
    "icon_sha256",
    "engaged_score_milli",
    "engaged_milliseconds",
    "foreground_milliseconds",
    "direct_event_count",
    "interaction_level",
    "attribution_confidence_milli",
}

_APP_TIMELINE_V2_COLUMNS = {
    "minute_start",
    "canonical_app_id",
    "display_name",
    "icon_sha256",
    "direct_input_count",
    "key_count",
    "click_count",
    "scroll_count",
    "text_count",
    "clipboard_count",
    "other_input_count",
    "non_direct_activity_count",
    "effective_frame_count",
    "effective_non_direct_ui_event_count",
    "foreground_milliseconds",
    "interaction_level",
    "attribution_confidence_milli",
    "projection_kind",
}

_GLOBAL_TIMELINE_V2_COLUMNS = {
    "minute_start",
    "direct_input_count",
    "non_direct_activity_count",
    "attributed_direct_input_count",
    "attributed_non_direct_activity_count",
    "unattributed_direct_input_count",
    "unattributed_non_direct_activity_count",
    "unattributed_reason_counts_json",
}

_FEATURE_CACHE_MAX_ENTRIES = 16
type _GraphIdentity = tuple[str, int, int]
type _GraphDataVersion = tuple[int, int, int, int]

_FEATURE_CACHE: dict[_GraphIdentity, ScreenpipeGraphFeatures] = {}
_FEATURE_CACHE_LOCK = threading.Lock()


def _expand_path(value: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(value)))


def resolve_screenpipe_graph_db_path(configured_path: str) -> str:
    value = configured_path.strip()
    if value and value.casefold() != "auto":
        return _expand_path(value)
    environment_path = os.environ.get("EVENT_LOGGER_SCREENPIPE_GRAPH_DB_PATH", "").strip()
    if environment_path:
        return _expand_path(environment_path)
    relative = Path("data") / "screenpipe-graph" / "activity-memory-continuous-v2.sqlite3"
    candidates: list[Path] = []
    for anchor in (Path.cwd(), Path(__file__).resolve()):
        for parent in (anchor, *anchor.parents):
            candidates.append(parent / "event-logger" / relative)
            if parent.name.casefold() == "event-logger":
                candidates.append(parent / relative)
    existing = next((candidate for candidate in candidates if candidate.is_file()), None)
    fallback = existing or candidates[0]
    return str(fallback.resolve())


def resolve_pieces_db_path(configured_path: str) -> str:
    value = configured_path.strip()
    if value and value.casefold() != "auto":
        return _expand_path(value)
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(
        local_app_data,
        "Mesh Intelligent Technologies, Inc",
        "Pieces OS",
        "com.pieces.os",
        "production",
        "Pieces",
        "vector_db",
        "workstreamEvents.sqlite",
    )


def resolve_density_source(
    configured_source: str,
    screenpipe_graph_database_path: str,
    pieces_database_path: str,
) -> ResolvedDensitySource:
    source = DensitySource(configured_source)
    screenpipe_graph_path = resolve_screenpipe_graph_db_path(screenpipe_graph_database_path)
    pieces_path = resolve_pieces_db_path(pieces_database_path)
    if source is DensitySource.SCREENPIPE_GRAPH:
        return ResolvedDensitySource(source, screenpipe_graph_path)
    if source is DensitySource.PIECES:
        return ResolvedDensitySource(source, pieces_path)
    if os.path.isfile(screenpipe_graph_path):
        return ResolvedDensitySource(DensitySource.SCREENPIPE_GRAPH, screenpipe_graph_path)
    return ResolvedDensitySource(
        DensitySource.PIECES,
        pieces_path,
        f"screenpipe_graph_unavailable:{screenpipe_graph_path}",
    )


def _rfc3339(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="microseconds")


def _open_read_only(database_path: str) -> sqlite3.Connection:
    uri_path = database_path.replace("\\", "/")
    connection = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=2)
    connection.execute("pragma query_only = on")
    return connection


def _graph_identity(database_path: str) -> _GraphIdentity:
    absolute_path = os.path.abspath(database_path)
    stat = os.stat(absolute_path)
    return os.path.normcase(absolute_path), int(stat.st_dev), int(stat.st_ino)


def _graph_data_version(database_path: str) -> _GraphDataVersion:
    stat = os.stat(database_path)
    try:
        wal_stat = os.stat(f"{database_path}-wal")
    except FileNotFoundError:
        return int(stat.st_mtime_ns), int(stat.st_size), 0, 0
    return int(stat.st_mtime_ns), int(stat.st_size), int(wal_stat.st_mtime_ns), int(wal_stat.st_size)


def _object_columns(connection: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"pragma table_info({name})")}


def _inspect_screenpipe_graph_features(connection: sqlite3.Connection) -> ScreenpipeGraphFeatures:
    objects = {
        str(row[0]) for row in connection.execute("select name from sqlite_master where type in ('table', 'view')")
    }
    schema_version = None
    if "graph_schema" in objects:
        row = connection.execute("select version from graph_schema where singleton=1").fetchone()
        if row is not None:
            schema_version = int(row[0])

    minute_columns = (
        _object_columns(connection, "source_activity_minutes") if "source_activity_minutes" in objects else set()
    )
    frame_column = (
        "effective_frame_count"
        if "effective_frame_count" in minute_columns
        else "frame_count"
        if "frame_count" in minute_columns
        else None
    )
    ui_column = (
        "effective_ui_event_count"
        if "effective_ui_event_count" in minute_columns
        else "ui_event_count"
        if "ui_event_count" in minute_columns
        else None
    )
    acquisition_columns = (
        _object_columns(connection, "source_acquisitions") if "source_acquisitions" in objects else set()
    )
    timeline_columns = (
        _object_columns(connection, "timeline_app_minutes_v1") if "timeline_app_minutes_v1" in objects else set()
    )
    timeline_v2_columns = (
        _object_columns(connection, "timeline_app_minutes_v2") if "timeline_app_minutes_v2" in objects else set()
    )
    global_v2_columns = (
        _object_columns(connection, "timeline_global_minutes_v2")
        if "timeline_global_minutes_v2" in objects
        else set()
    )
    icon_columns = _object_columns(connection, "app_icon_versions") if "app_icon_versions" in objects else set()
    return ScreenpipeGraphFeatures(
        schema_version=schema_version,
        density_frame_column=frame_column if "minute_start" in minute_columns else None,
        density_ui_column=ui_column if "minute_start" in minute_columns else None,
        supports_acquisition_watermark={"source_pack_sha256", "observed_at"}.issubset(acquisition_columns)
        and "source_pack_sha256" in minute_columns,
        supports_app_timeline=_APP_TIMELINE_COLUMNS.issubset(timeline_columns),
        supports_app_timeline_v2=_APP_TIMELINE_V2_COLUMNS.issubset(timeline_v2_columns),
        supports_global_timeline_v2=_GLOBAL_TIMELINE_V2_COLUMNS.issubset(global_v2_columns),
        supports_icon_colors={"representative_color_rgb", "color_algorithm_version"}.issubset(icon_columns),
    )


def inspect_screenpipe_graph_features(database_path: str) -> ScreenpipeGraphFeatures:
    key = _graph_identity(database_path)
    with _FEATURE_CACHE_LOCK:
        cached = _FEATURE_CACHE.get(key)
        if cached is not None:
            return cached
    connection = _open_read_only(database_path)
    try:
        features = _inspect_screenpipe_graph_features(connection)
    finally:
        connection.close()
    with _FEATURE_CACHE_LOCK:
        _FEATURE_CACHE[key] = features
        while len(_FEATURE_CACHE) > _FEATURE_CACHE_MAX_ENTRIES:
            _FEATURE_CACHE.pop(next(iter(_FEATURE_CACHE)))
    return features


def _parse_rfc3339(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _query_screenpipe_refresh_state(
    database_path: str,
    previous_watermark: _ScreenpipeWatermark | None,
    features: ScreenpipeGraphFeatures | None = None,
) -> _ScreenpipeRefreshState | None:
    connection = _open_read_only(database_path)
    try:
        features = features or _inspect_screenpipe_graph_features(connection)
        if not features.supports_acquisition_watermark:
            return None
        max_rowid = int(connection.execute("select coalesce(max(rowid), 0) from source_acquisitions").fetchone()[0])
        observed_row = connection.execute("select max(observed_at) from source_acquisitions").fetchone()
        observed_at = str(observed_row[0]) if observed_row and observed_row[0] is not None else None
        watermark = _ScreenpipeWatermark(max_rowid, observed_at)
        if previous_watermark is None:
            return _ScreenpipeRefreshState(watermark, None)
        if max_rowid < previous_watermark.max_rowid or (
            previous_watermark.observed_at is not None
            and (observed_at is None or observed_at < previous_watermark.observed_at)
        ):
            return _ScreenpipeRefreshState(watermark, float("-inf"))

        if previous_watermark.observed_at is None:
            row = connection.execute(
                "select min(m.minute_start) "
                "from source_acquisitions a "
                "left join source_activity_minutes m on m.source_pack_sha256 = a.source_pack_sha256 "
                "where a.rowid > ?",
                (previous_watermark.max_rowid,),
            ).fetchone()
        else:
            row = connection.execute(
                "select min(m.minute_start) "
                "from source_acquisitions a "
                "left join source_activity_minutes m on m.source_pack_sha256 = a.source_pack_sha256 "
                "where a.rowid > ? or a.observed_at > ?",
                (previous_watermark.max_rowid, previous_watermark.observed_at),
            ).fetchone()
        earliest = _parse_rfc3339(str(row[0])) if row and row[0] is not None else None
        return _ScreenpipeRefreshState(watermark, earliest)
    finally:
        connection.close()


def query_density_buckets(
    resolved: ResolvedDensitySource,
    stream_start: float,
    query_start: float,
    query_end: float,
) -> list[tuple[int, int]]:
    connection = _open_read_only(resolved.database_path)
    try:
        if resolved.source is DensitySource.SCREENPIPE_GRAPH:
            start_text = _rfc3339(query_start)
            end_text = _rfc3339(query_end)
            features = _inspect_screenpipe_graph_features(connection)
            if not features.supports_density:
                raise sqlite3.OperationalError("screenpipe graph lacks source_activity_minutes density facts")
            rows = connection.execute(
                "select cast((unixepoch(minute_start, 'subsec') - ?) / 60 as integer), "
                f"sum({features.density_frame_column} + {features.density_ui_column}) "
                "from source_activity_minutes "
                "where minute_start >= ? and minute_start < ? "
                "group by minute_start order by minute_start",
                (stream_start, start_text, end_text),
            ).fetchall()
        else:
            rows = connection.execute(
                "select cast((created_at - ?) / 60 as integer), count(*) "
                "from vectors where created_at >= ? and created_at < ? group by 1 order by 1",
                (stream_start, query_start, query_end),
            ).fetchall()
        return [(int(bucket), int(count)) for bucket, count in rows]
    finally:
        connection.close()


def query_app_minutes(
    resolved: ResolvedDensitySource,
    stream_start: float,
    query_start: float,
    query_end: float,
) -> list[AppMinuteRecord]:
    return list(query_activity_timeline(resolved, stream_start, query_start, query_end).app_records)


def query_activity_timeline(
    resolved: ResolvedDensitySource,
    stream_start: float,
    query_start: float,
    query_end: float,
    *,
    features: ScreenpipeGraphFeatures | None = None,
) -> ActivityTimelineData:
    if resolved.source is not DensitySource.SCREENPIPE_GRAPH:
        return ActivityTimelineData((), (), 1)
    features = features or inspect_screenpipe_graph_features(resolved.database_path)
    connection = _open_read_only(resolved.database_path)
    try:
        start_text = _rfc3339(query_start)
        end_text = _rfc3339(query_end)
        records: dict[tuple[int, str], AppMinuteRecord] = {}
        has_v2 = features.supports_app_timeline_v2 and features.supports_global_timeline_v2
        if features.supports_app_timeline and not has_v2:
            rows = connection.execute(
                "select cast((unixepoch(minute_start, 'subsec') - ?) / 60 as integer), "
                "canonical_app_id, display_name, icon_sha256, engaged_score_milli, "
                "engaged_milliseconds, foreground_milliseconds, direct_event_count, "
                "interaction_level, attribution_confidence_milli "
                "from timeline_app_minutes_v1 where minute_start >= ? and minute_start < ? "
                "order by minute_start, engaged_score_milli desc, canonical_app_id",
                (stream_start, start_text, end_text),
            ).fetchall()
            for row in rows:
                record = AppMinuteRecord(
                    bucket=int(row[0]),
                    canonical_app_id=str(row[1]),
                    display_name=str(row[2]),
                    icon_sha256=str(row[3]) if row[3] is not None else None,
                    engaged_score_milli=int(row[4]),
                    engaged_milliseconds=int(row[5]),
                    foreground_milliseconds=int(row[6]),
                    direct_event_count=int(row[7]),
                    interaction_level=str(row[8]),
                    attribution_confidence_milli=int(row[9]),
                    direct_input_count=int(row[7]),
                )
                records[(record.bucket, record.canonical_app_id)] = record

        if has_v2:
            v1_join = (
                "left join timeline_app_minutes_v1 legacy "
                "on legacy.minute_start=v2.minute_start "
                "and legacy.canonical_app_id=v2.canonical_app_id "
                if features.supports_app_timeline
                else ""
            )
            score_projection = (
                "coalesce(legacy.engaged_score_milli, "
                "v2.direct_input_count * 2000 + v2.non_direct_activity_count * 100), "
                "coalesce(legacy.engaged_milliseconds, 0), "
                if features.supports_app_timeline
                else "v2.direct_input_count * 2000 + v2.non_direct_activity_count * 100, 0, "
            )
            color_join = (
                "left join app_icon_versions color on color.app_id=v2.canonical_app_id "
                "and color.icon_sha256=v2.icon_sha256 and color.valid_to is null "
                if features.supports_icon_colors
                else ""
            )
            color_projection = (
                "color.representative_color_rgb, color.color_algorithm_version "
                if features.supports_icon_colors
                else "null, null "
            )
            rows = connection.execute(
                "select cast((unixepoch(v2.minute_start, 'subsec') - ?) / 60 as integer), "
                "v2.canonical_app_id, v2.display_name, v2.icon_sha256, "
                f"{score_projection}"
                "v2.foreground_milliseconds, v2.direct_input_count, v2.interaction_level, "
                "v2.attribution_confidence_milli, v2.direct_input_count, v2.key_count, "
                "v2.click_count, v2.scroll_count, v2.text_count, v2.clipboard_count, "
                "v2.other_input_count, v2.non_direct_activity_count, v2.effective_frame_count, "
                "v2.effective_non_direct_ui_event_count, v2.projection_kind, "
                f"{color_projection}"
                "from timeline_app_minutes_v2 v2 "
                f"{v1_join}{color_join}"
                "where v2.minute_start >= ? and v2.minute_start < ? "
                "order by v2.minute_start, v2.direct_input_count desc, "
                "v2.non_direct_activity_count desc, v2.canonical_app_id",
                (stream_start, start_text, end_text),
            ).fetchall()
            for row in rows:
                direct_counts = tuple(int(row[index]) for index in range(10, 17))
                non_direct_counts = tuple(int(row[index]) for index in range(17, 20))
                if any(value < 0 for value in (*direct_counts, *non_direct_counts)):
                    raise sqlite3.DatabaseError("screenpipe graph contains a negative app activity v2 count")
                direct, key, click, scroll, text_count, clipboard, other = direct_counts
                non_direct, frames, non_direct_ui = non_direct_counts
                if direct != key + click + scroll + text_count + clipboard + other:
                    raise sqlite3.DatabaseError("screenpipe graph violates app direct activity v2 conservation")
                if non_direct != frames + non_direct_ui:
                    raise sqlite3.DatabaseError("screenpipe graph violates app non-direct activity v2 conservation")
                record = AppMinuteRecord(
                    bucket=int(row[0]),
                    canonical_app_id=str(row[1]),
                    display_name=str(row[2]),
                    icon_sha256=str(row[3]) if row[3] is not None else None,
                    engaged_score_milli=int(row[4]),
                    engaged_milliseconds=int(row[5]),
                    foreground_milliseconds=int(row[6]),
                    direct_event_count=int(row[7]),
                    interaction_level=str(row[8]),
                    attribution_confidence_milli=int(row[9]),
                    direct_input_count=direct,
                    key_count=key,
                    click_count=click,
                    scroll_count=scroll,
                    text_count=text_count,
                    clipboard_count=clipboard,
                    other_input_count=other,
                    non_direct_activity_count=non_direct,
                    effective_frame_count=frames,
                    effective_non_direct_ui_event_count=non_direct_ui,
                    projection_kind=str(row[20]),
                    representative_color_rgb=int(row[21]) if row[21] is not None else None,
                    color_algorithm_version=int(row[22]) if row[22] is not None else None,
                    contract_version=2,
                )
                records[(record.bucket, record.canonical_app_id)] = record

        global_records: list[GlobalMinuteRecord] = []
        if has_v2:
            rows = connection.execute(
                "select cast((unixepoch(minute_start, 'subsec') - ?) / 60 as integer), "
                "direct_input_count, non_direct_activity_count, attributed_direct_input_count, "
                "attributed_non_direct_activity_count, unattributed_direct_input_count, "
                "unattributed_non_direct_activity_count, unattributed_reason_counts_json "
                "from timeline_global_minutes_v2 where minute_start >= ? and minute_start < ? "
                "order by minute_start",
                (stream_start, start_text, end_text),
            ).fetchall()
            for row in rows:
                counts = tuple(int(row[index]) for index in range(1, 7))
                if any(value < 0 for value in counts):
                    raise sqlite3.DatabaseError("screenpipe graph contains a negative activity v2 residual")
                direct, non_direct, attributed_direct, attributed_non_direct, residual_direct, residual_non_direct = (
                    counts
                )
                if direct != attributed_direct + residual_direct or non_direct != (
                    attributed_non_direct + residual_non_direct
                ):
                    raise sqlite3.DatabaseError("screenpipe graph violates activity v2 conservation")
                try:
                    reasons_value = json.loads(str(row[7]))
                except (TypeError, ValueError) as error:
                    raise sqlite3.DatabaseError("screenpipe graph residual reasons are malformed JSON") from error
                if not isinstance(reasons_value, dict):
                    raise sqlite3.DatabaseError("screenpipe graph residual reasons are not an object")
                try:
                    reasons = tuple(
                        sorted(
                            (
                                UnattributedReasonCount(*str(key).split(":", 1), int(value))
                                for key, value in reasons_value.items()
                                if ":" in str(key)
                            ),
                            key=lambda item: (item.signal_kind, item.reason),
                        )
                    )
                except (TypeError, ValueError) as error:
                    raise sqlite3.DatabaseError("screenpipe graph residual reasons contain a malformed count") from error
                if len(reasons) != len(reasons_value):
                    raise sqlite3.DatabaseError("screenpipe graph residual reason key lacks a signal kind")
                if any(
                    reason.signal_kind not in {"direct_input", "non_direct_activity"} for reason in reasons
                ):
                    raise sqlite3.DatabaseError("screenpipe graph residual reason has an unknown signal kind")
                if any(reason.count < 0 for reason in reasons):
                    raise sqlite3.DatabaseError("screenpipe graph contains a negative residual reason count")
                reason_direct = sum(reason.count for reason in reasons if reason.signal_kind == "direct_input")
                reason_non_direct = sum(
                    reason.count for reason in reasons if reason.signal_kind == "non_direct_activity"
                )
                if reason_direct != residual_direct or reason_non_direct != residual_non_direct:
                    raise sqlite3.DatabaseError("screenpipe graph residual reasons violate activity v2 conservation")
                global_records.append(
                    GlobalMinuteRecord(
                        int(row[0]),
                        direct,
                        non_direct,
                        attributed_direct,
                        attributed_non_direct,
                        residual_direct,
                        residual_non_direct,
                        reasons,
                    )
                )
        ordered_records = tuple(
            sorted(
                records.values(),
                key=lambda item: (
                    item.bucket,
                    -item.direct_input_count,
                    -item.non_direct_activity_count,
                    -item.engaged_score_milli,
                    item.canonical_app_id,
                ),
            )
        )
        return ActivityTimelineData(ordered_records, tuple(global_records), 2 if has_v2 else 1)
    finally:
        connection.close()


class AppTimelineCache:
    _MAX_ENTRIES = 8

    def __init__(self):
        self._entries: dict[
            tuple[_GraphIdentity, float, float | None, int, _GraphDataVersion | None],
            _AppCacheEntry,
        ] = {}
        self._lock = threading.Lock()

    def load(
        self,
        resolved: ResolvedDensitySource,
        stream_start: float,
        interval_end: float,
        closed_interval: bool,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> list[AppMinuteRecord]:
        return list(
            self.load_activity(
                resolved,
                stream_start,
                interval_end,
                closed_interval,
                cancelled,
            ).app_records
        )

    def load_activity(
        self,
        resolved: ResolvedDensitySource,
        stream_start: float,
        interval_end: float,
        closed_interval: bool,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> ActivityTimelineData:
        if resolved.source is not DensitySource.SCREENPIPE_GRAPH:
            return ActivityTimelineData((), (), 1)
        graph_identity = _graph_identity(resolved.database_path)
        data_version = _graph_data_version(resolved.database_path) if closed_interval else None
        features = inspect_screenpipe_graph_features(resolved.database_path)
        contract_version = int(features.supports_app_timeline_v2 and features.supports_global_timeline_v2) + 1
        key = (
            graph_identity,
            stream_start,
            interval_end if closed_interval else None,
            contract_version,
            data_version,
        )
        with self._lock:
            entry = self._entries.get(key)
            if entry and entry.complete:
                return ActivityTimelineData(tuple(entry.records), tuple(entry.global_records), entry.contract_version)
            if cancelled():
                return ActivityTimelineData((), (), contract_version)
            refresh = _query_screenpipe_refresh_state(
                resolved.database_path,
                entry.source_watermark if entry else None,
                features,
            )
            watermark = refresh.watermark if refresh else None
            if entry and refresh is not None and refresh.earliest_changed_minute is None:
                return ActivityTimelineData(tuple(entry.records), tuple(entry.global_records), entry.contract_version)
            if entry and refresh is not None:
                earliest_changed_minute = refresh.earliest_changed_minute
                assert earliest_changed_minute is not None
                refresh_bucket = (
                    max(int((earliest_changed_minute - stream_start) // 60), 0)
                    if math.isfinite(earliest_changed_minute)
                    else 0
                )
                records = [record for record in entry.records if record.bucket < refresh_bucket]
                global_records = [record for record in entry.global_records if record.bucket < refresh_bucket]
            else:
                refresh_bucket = 0
                records = []
                global_records = []
            refreshed = query_activity_timeline(
                resolved,
                stream_start,
                stream_start + refresh_bucket * 60,
                interval_end,
                features=features,
            )
            records.extend(refreshed.app_records)
            global_records.extend(refreshed.global_records)
            if cancelled():
                return ActivityTimelineData((), (), contract_version)
            records.sort(
                key=lambda item: (
                    item.bucket,
                    -item.direct_input_count,
                    -item.non_direct_activity_count,
                    -item.engaged_score_milli,
                    item.canonical_app_id,
                )
            )
            global_records.sort(key=lambda item: item.bucket)
            self._entries[key] = _AppCacheEntry(
                records,
                global_records,
                contract_version,
                closed_interval,
                watermark,
            )
            while len(self._entries) > self._MAX_ENTRIES:
                self._entries.pop(next(iter(self._entries)))
            return ActivityTimelineData(tuple(records), tuple(global_records), contract_version)


class DensityBucketCache:
    """Caches closed intervals and incrementally refreshes safe live sources.

    Screenpipe graph windows are committed after their source interval closes, so
    an acquisition watermark identifies which earlier minutes need refreshing.
    Legacy graphs without that contract revalidate the complete open interval.
    Pieces rows arrive directly, so only its two newest minute buckets are refreshed.
    """

    _MAX_ENTRIES = 8

    def __init__(self):
        self._entries: dict[tuple[str, str, float, float | None], _CacheEntry] = {}
        self._lock = threading.Lock()

    def load(
        self,
        resolved: ResolvedDensitySource,
        stream_start: float,
        interval_end: float,
        closed_interval: bool,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> list[int]:
        bucket_count = max(math.ceil((interval_end - stream_start) / 60), 1)
        key = (
            resolved.source.value,
            os.path.normcase(resolved.database_path),
            stream_start,
            interval_end if closed_interval else None,
        )
        with self._lock:
            entry = self._entries.get(key)
            if entry and entry.complete and len(entry.raw_buckets) == bucket_count:
                return list(entry.raw_buckets)
            if cancelled():
                return []

            raw_buckets = list(entry.raw_buckets) if entry else []
            raw_buckets.extend([0] * (bucket_count - len(raw_buckets)))
            raw_buckets = raw_buckets[:bucket_count]
            refresh_state = None
            source_watermark = entry.source_watermark if entry else None
            if resolved.source is DensitySource.SCREENPIPE_GRAPH:
                refresh_state = _query_screenpipe_refresh_state(resolved.database_path, source_watermark)
                if refresh_state is not None:
                    source_watermark = refresh_state.watermark
            if entry and resolved.source is not DensitySource.SCREENPIPE_GRAPH:
                last_bucket = int(max(entry.refreshed_through - stream_start, 0) // 60)
                refresh_index = max(last_bucket - 1, 0)
            elif entry and refresh_state is not None and refresh_state.earliest_changed_minute is None:
                refresh_index = bucket_count
            elif entry and refresh_state is not None:
                earliest_changed_minute = refresh_state.earliest_changed_minute
                assert earliest_changed_minute is not None
                refresh_index = (
                    max(
                        min(int((earliest_changed_minute - stream_start) // 60), bucket_count),
                        0,
                    )
                    if math.isfinite(earliest_changed_minute)
                    else 0
                )
            else:
                refresh_index = 0
            for index in range(refresh_index, bucket_count):
                raw_buckets[index] = 0

            query_start = stream_start + refresh_index * 60
            for bucket, count in query_density_buckets(resolved, stream_start, query_start, interval_end):
                if refresh_index <= bucket < bucket_count:
                    raw_buckets[bucket] = count
            if cancelled():
                return []

            self._entries[key] = _CacheEntry(
                raw_buckets=list(raw_buckets),
                refreshed_through=interval_end,
                complete=closed_interval,
                source_watermark=source_watermark,
            )
            while len(self._entries) > self._MAX_ENTRIES:
                self._entries.pop(next(iter(self._entries)))
            return raw_buckets


def integrate_density(raw_buckets: list[int], radius: int = 5) -> list[int]:
    prefix = [0]
    for value in raw_buckets:
        prefix.append(prefix[-1] + value)
    return [
        prefix[min(len(raw_buckets), index + radius + 1)] - prefix[max(0, index - radius)]
        for index in range(len(raw_buckets))
    ]
