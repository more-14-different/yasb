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


@dataclass
class _CacheEntry:
    raw_buckets: list[int]
    refreshed_through: float
    complete: bool


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


def query_density_buckets(
    resolved: ResolvedDensitySource,
    stream_start: float,
    query_start: float,
    query_end: float,
) -> list[tuple[int, int]]:
    uri_path = resolved.database_path.replace("\\", "/")
    connection = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=2)
    try:
        connection.execute("pragma query_only = on")
        if resolved.source is DensitySource.SCREENPIPE_GRAPH:
            start_text = _rfc3339(query_start)
            end_text = _rfc3339(query_end)
            minute_columns = {
                str(row[1])
                for row in connection.execute("pragma table_info(source_activity_minutes)")
            }
            ui_count_column = (
                "effective_ui_event_count"
                if "effective_ui_event_count" in minute_columns
                else "ui_event_count"
            )
            rows = connection.execute(
                "select cast((unixepoch(minute_start, 'subsec') - ?) / 60 as integer), "
                f"sum(frame_count + {ui_count_column}) "
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


class DensityBucketCache:
    """Keeps history in RAM and rereads only the two newest minute buckets."""

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
            if entry:
                last_bucket = int(max(entry.refreshed_through - stream_start, 0) // 60)
                refresh_index = max(last_bucket - 1, 0)
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
