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


@dataclass
class _AppCacheEntry:
    records: list[AppMinuteRecord]
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
    return ScreenpipeGraphFeatures(
        schema_version=schema_version,
        density_frame_column=frame_column if "minute_start" in minute_columns else None,
        density_ui_column=ui_column if "minute_start" in minute_columns else None,
        supports_acquisition_watermark={"source_pack_sha256", "observed_at"}.issubset(acquisition_columns)
        and "source_pack_sha256" in minute_columns,
        supports_app_timeline=_APP_TIMELINE_COLUMNS.issubset(timeline_columns),
    )


def inspect_screenpipe_graph_features(database_path: str) -> ScreenpipeGraphFeatures:
    connection = _open_read_only(database_path)
    try:
        return _inspect_screenpipe_graph_features(connection)
    finally:
        connection.close()


def _parse_rfc3339(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _query_screenpipe_refresh_state(
    database_path: str,
    previous_watermark: _ScreenpipeWatermark | None,
) -> _ScreenpipeRefreshState | None:
    connection = _open_read_only(database_path)
    try:
        features = _inspect_screenpipe_graph_features(connection)
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
                "where a.rowid > ? or a.observed_at >= ?",
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
    if resolved.source is not DensitySource.SCREENPIPE_GRAPH:
        return []
    connection = _open_read_only(resolved.database_path)
    try:
        if not _inspect_screenpipe_graph_features(connection).supports_app_timeline:
            return []
        rows = connection.execute(
            "select cast((unixepoch(minute_start, 'subsec') - ?) / 60 as integer), "
            "canonical_app_id, display_name, icon_sha256, engaged_score_milli, "
            "engaged_milliseconds, foreground_milliseconds, direct_event_count, "
            "interaction_level, attribution_confidence_milli "
            "from timeline_app_minutes_v1 where minute_start >= ? and minute_start < ? "
            "order by minute_start, engaged_score_milli desc, canonical_app_id",
            (stream_start, _rfc3339(query_start), _rfc3339(query_end)),
        ).fetchall()
        return [
            AppMinuteRecord(
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
            )
            for row in rows
        ]
    finally:
        connection.close()


class AppTimelineCache:
    _MAX_ENTRIES = 8

    def __init__(self):
        self._entries: dict[tuple[str, float, float | None], _AppCacheEntry] = {}
        self._lock = threading.Lock()

    def load(
        self,
        resolved: ResolvedDensitySource,
        stream_start: float,
        interval_end: float,
        closed_interval: bool,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> list[AppMinuteRecord]:
        if resolved.source is not DensitySource.SCREENPIPE_GRAPH:
            return []
        key = (
            os.path.normcase(resolved.database_path),
            stream_start,
            interval_end if closed_interval else None,
        )
        with self._lock:
            entry = self._entries.get(key)
            if entry and entry.complete:
                return list(entry.records)
            if cancelled():
                return []
            refresh = _query_screenpipe_refresh_state(
                resolved.database_path,
                entry.source_watermark if entry else None,
            )
            watermark = refresh.watermark if refresh else None
            if entry and refresh is not None and refresh.earliest_changed_minute is None:
                return list(entry.records)
            if entry and refresh is not None:
                earliest_changed_minute = refresh.earliest_changed_minute
                assert earliest_changed_minute is not None
                refresh_bucket = (
                    max(int((earliest_changed_minute - stream_start) // 60), 0)
                    if math.isfinite(earliest_changed_minute)
                    else 0
                )
                records = [record for record in entry.records if record.bucket < refresh_bucket]
            else:
                refresh_bucket = 0
                records = []
            records.extend(
                query_app_minutes(
                    resolved,
                    stream_start,
                    stream_start + refresh_bucket * 60,
                    interval_end,
                )
            )
            if cancelled():
                return []
            records.sort(key=lambda item: (item.bucket, -item.engaged_score_milli, item.canonical_app_id))
            self._entries[key] = _AppCacheEntry(records, closed_interval, watermark)
            while len(self._entries) > self._MAX_ENTRIES:
                self._entries.pop(next(iter(self._entries)))
            return list(records)


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
