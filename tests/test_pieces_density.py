import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import cast
from unittest.mock import patch

from PyQt6.QtCore import QRectF

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.widgets.yasb.pieces_density import (  # noqa: E402
    PiecesDensityWidget,
    SessionManager,
    density_tooltip_html,
    format_compact_date,
    format_compact_duration,
    format_compact_time,
    ruler_label_baseline,
    ruler_label_x,
    selected_session_index,
)
from core.widgets.yasb.pieces_density_source import (  # noqa: E402
    DensityBucketCache,
    DensitySource,
    ResolvedDensitySource,
    _query_screenpipe_refresh_state,
    _ScreenpipeRefreshState,
    _ScreenpipeWatermark,
    inspect_screenpipe_graph_features,
    integrate_density,
    query_density_buckets,
    resolve_density_source,
)


class SessionManagerSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "truth_time.sqlite3"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _create_database(self, version: int, include_required_columns: bool = True):
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute(f"pragma user_version = {version}")
            conn.execute("create table livestreams (official_start_at_utc_us integer, official_end_at_utc_us integer)")
            machine_end_columns = (
                ", shutdown_at_utc_us integer, shutdown_upper_bound_utc_us integer" if include_required_columns else ""
            )
            conn.execute(f"create table machine_sessions (boot_at_utc_us integer{machine_end_columns})")
            conn.commit()
        finally:
            conn.close()

    def test_rejects_schema_older_than_v2(self):
        self._create_database(version=1)
        manager = SessionManager(str(self.database_path))

        self.assertEqual(manager.livestream_intervals(), [])
        self.assertIn("schema v1 is unsupported", manager.last_error)

    def test_rejects_missing_contract_columns(self):
        self._create_database(version=2, include_required_columns=False)
        manager = SessionManager(str(self.database_path))

        self.assertEqual(manager.machine_intervals(), [])
        self.assertIn("shutdown_at_utc_us", manager.last_error)

    def test_accepts_v2_and_future_compatible_schemas(self):
        for version in (2, 3):
            with self.subTest(version=version):
                if self.database_path.exists():
                    self.database_path.unlink()
                self._create_database(version=version)
                conn = sqlite3.connect(self.database_path)
                try:
                    conn.execute(
                        "insert into livestreams values (?, ?)",
                        (1_800_000_000_000_000, 1_800_000_060_000_000),
                    )
                    conn.commit()
                finally:
                    conn.close()

                manager = SessionManager(str(self.database_path))
                self.assertEqual(manager.livestream_intervals(), [(1_800_000_000.0, 1_800_000_060.0)])
                self.assertEqual(manager.last_error, "")


class SessionSelectionTests(unittest.TestCase):
    def test_new_latest_session_does_not_shift_an_older_selection(self):
        selected_start = 200.0

        self.assertEqual(selected_session_index([100.0, 200.0, 300.0], selected_start), 1)
        self.assertEqual(selected_session_index([100.0, 200.0, 300.0, 400.0], selected_start), 1)

    def test_none_always_selects_latest_session(self):
        self.assertEqual(selected_session_index([100.0, 200.0, 300.0], None), 2)


class CompactTimeFormatTests(unittest.TestCase):
    def test_date_omits_leading_zero_from_month_and_day(self):
        value = datetime(2026, 7, 8, 9, 5)

        self.assertEqual(format_compact_date(value), "7-8")
        self.assertEqual(format_compact_date(value, include_year=True), "26-7-8")

    def test_clock_omits_leading_zero_from_hour_but_not_minute(self):
        self.assertEqual(format_compact_time(datetime(2026, 7, 8, 9, 5)), "9:05")
        self.assertEqual(format_compact_time(datetime(2026, 7, 8, 9, 0)), "9:00")
        self.assertEqual(format_compact_time(datetime(2026, 7, 8, 0, 0)), "0:00")

    def test_duration_omits_leading_zero_from_hour_but_not_minute(self):
        self.assertEqual(format_compact_duration(7, 5), "7:05")
        self.assertEqual(format_compact_duration(0, 0), "0:00")


class DensityTooltipTests(unittest.TestCase):
    def test_uses_four_colored_semantic_units_and_compact_time(self):
        tooltip = density_tooltip_html(28, datetime(2026, 7, 8, 9, 5))

        self.assertIn("<b>28</b>", tooltip)
        self.assertIn("Events&nbsp;∈", tooltip)
        self.assertIn("<b>9:05</b>", tooltip)
        self.assertIn("±5m", tooltip)
        self.assertNotIn("09:05", tooltip)
        self.assertEqual(tooltip.count('style="white-space: nowrap;"'), 4)
        self.assertEqual(tooltip.count("<font color="), 4)


class DensitySourceTests(unittest.TestCase):
    def test_auto_prefers_screenpipe_graph_and_names_pieces_fallback(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            screenpipe_graph = root / "screenpipe-graph.sqlite"
            pieces = root / "pieces.sqlite"
            pieces.touch()

            fallback = resolve_density_source("auto", str(screenpipe_graph), str(pieces))
            self.assertEqual(fallback.source, DensitySource.PIECES)
            self.assertEqual(fallback.database_path, str(pieces))
            assert fallback.fallback_reason is not None
            self.assertIn("screenpipe_graph_unavailable", fallback.fallback_reason)

            screenpipe_graph.touch()
            preferred = resolve_density_source("auto", str(screenpipe_graph), str(pieces))
            self.assertEqual(preferred.source, DensitySource.SCREENPIPE_GRAPH)
            self.assertIsNone(preferred.fallback_reason)

    def test_screenpipe_graph_density_reads_exact_minute_facts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "screenpipe-graph.sqlite"
            connection = sqlite3.connect(database_path)
            try:
                connection.executescript(
                    "create table source_activity_minutes ("
                    "source_generation_id text, minute_start text, frame_count integer, "
                    "effective_frame_count integer, suppressed_frame_count integer, "
                    "ui_event_count integer, effective_ui_event_count integer, "
                    "suppressed_ui_event_count integer, source_pack_sha256 text);"
                    "insert into source_activity_minutes values "
                    "('g1','2026-07-19T10:00:00Z',5,2,3,3,1,2,'a');"
                    "insert into source_activity_minutes values "
                    "('g1','2026-07-19T10:01:00Z',15,7,8,11,5,6,'b');"
                    "insert into source_activity_minutes values "
                    "('g1','2026-07-19T10:02:00Z',25,13,12,17,9,8,'c');"
                )
                connection.commit()
            finally:
                connection.close()
            start = datetime.fromisoformat("2026-07-19T10:00:00+00:00").timestamp()
            resolved = ResolvedDensitySource(DensitySource.SCREENPIPE_GRAPH, str(database_path), None)

            self.assertEqual(
                query_density_buckets(resolved, start, start, start + 120),
                [(0, 3), (1, 12)],
            )

    def test_screenpipe_graph_density_falls_back_to_v1_raw_ui_counts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "screenpipe-graph.sqlite"
            connection = sqlite3.connect(database_path)
            try:
                connection.executescript(
                    "create table source_activity_minutes ("
                    "source_generation_id text, minute_start text, frame_count integer, "
                    "ui_event_count integer, source_pack_sha256 text);"
                    "insert into source_activity_minutes values "
                    "('g1','2026-07-19T10:00:00Z',2,3,'a');"
                )
                connection.commit()
            finally:
                connection.close()
            start = datetime.fromisoformat("2026-07-19T10:00:00+00:00").timestamp()
            resolved = ResolvedDensitySource(DensitySource.SCREENPIPE_GRAPH, str(database_path), None)

            self.assertEqual(
                query_density_buckets(resolved, start, start, start + 60),
                [(0, 5)],
            )

    def test_v4_graph_features_keep_heatmap_without_app_timeline(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "screenpipe-graph.sqlite"
            connection = sqlite3.connect(database_path)
            try:
                connection.executescript(
                    "create table graph_schema (singleton integer primary key, version integer not null);"
                    "insert into graph_schema values (1, 4);"
                    "create table source_acquisitions ("
                    "source_pack_sha256 text primary key, observed_at text not null);"
                    "create table source_activity_minutes ("
                    "source_generation_id text, minute_start text, frame_count integer, "
                    "effective_frame_count integer, ui_event_count integer, "
                    "effective_ui_event_count integer, source_pack_sha256 text);"
                )
                connection.commit()
            finally:
                connection.close()

            features = inspect_screenpipe_graph_features(str(database_path))

            self.assertEqual(features.schema_version, 4)
            self.assertTrue(features.supports_density)
            self.assertTrue(features.supports_acquisition_watermark)
            self.assertFalse(features.supports_app_timeline)

    def test_v5_graph_features_detect_stable_app_timeline_view(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "screenpipe-graph.sqlite"
            connection = sqlite3.connect(database_path)
            try:
                connection.executescript(
                    "create table graph_schema (singleton integer primary key, version integer not null);"
                    "insert into graph_schema values (1, 5);"
                    "create table source_acquisitions ("
                    "source_pack_sha256 text primary key, observed_at text not null);"
                    "create table source_activity_minutes ("
                    "minute_start text, frame_count integer, ui_event_count integer, "
                    "source_pack_sha256 text);"
                    "create view timeline_app_minutes_v1 as select "
                    "null as minute_start, null as canonical_app_id, null as display_name, "
                    "null as icon_sha256, null as engaged_score_milli, "
                    "null as engaged_milliseconds, null as foreground_milliseconds, "
                    "null as direct_event_count, null as interaction_level, "
                    "null as attribution_confidence_milli;"
                )
                connection.commit()
            finally:
                connection.close()

            features = inspect_screenpipe_graph_features(str(database_path))

            self.assertEqual(features.schema_version, 5)
            self.assertTrue(features.supports_density)
            self.assertTrue(features.supports_app_timeline)

    def test_screenpipe_watermark_detects_historical_rows_inserted_by_merge(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "screenpipe-graph.sqlite"
            connection = sqlite3.connect(database_path)
            try:
                connection.executescript(
                    "create table source_acquisitions ("
                    "source_pack_sha256 text primary key, observed_at text not null);"
                    "create table source_activity_minutes ("
                    "minute_start text, frame_count integer, ui_event_count integer, "
                    "source_pack_sha256 text);"
                    "insert into source_acquisitions values ('live', '2026-10-23T10:00:00Z');"
                    "insert into source_activity_minutes values "
                    "('2026-10-23T09:59:00Z', 1, 1, 'live');"
                )
                connection.commit()
            finally:
                connection.close()

            initial = _query_screenpipe_refresh_state(str(database_path), None)
            assert initial is not None

            connection = sqlite3.connect(database_path)
            try:
                connection.executescript(
                    "insert into source_acquisitions values ('restored', '2026-09-01T00:00:00Z');"
                    "insert into source_activity_minutes values "
                    "('2026-08-31T23:59:00Z', 2, 2, 'restored');"
                )
                connection.commit()
            finally:
                connection.close()

            refreshed = _query_screenpipe_refresh_state(str(database_path), initial.watermark)
            assert refreshed is not None
            self.assertEqual(
                refreshed.earliest_changed_minute,
                datetime.fromisoformat("2026-08-31T23:59:00+00:00").timestamp(),
            )

    def test_active_pieces_cache_only_refreshes_the_two_newest_minutes(self):
        cache = DensityBucketCache()
        resolved = ResolvedDensitySource(DensitySource.PIECES, "unused.sqlite", None)
        with patch(
            "core.widgets.yasb.pieces_density_source.query_density_buckets",
            side_effect=[[(0, 1), (1, 2), (2, 3)], [(2, 4), (3, 5)]],
        ) as query:
            self.assertEqual(cache.load(resolved, 0, 180, False), [1, 2, 3])
            self.assertEqual(cache.load(resolved, 0, 240, False), [1, 2, 4, 5])

        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args_list[1].args[2:], (120, 240))

    def test_legacy_screenpipe_cache_reloads_late_minutes_from_the_full_interval(self):
        cache = DensityBucketCache()
        resolved = ResolvedDensitySource(DensitySource.SCREENPIPE_GRAPH, "unused.sqlite", None)
        with (
            patch(
                "core.widgets.yasb.pieces_density_source._query_screenpipe_refresh_state",
                return_value=None,
            ),
            patch(
                "core.widgets.yasb.pieces_density_source.query_density_buckets",
                side_effect=[[(2, 3)], [(0, 1), (1, 2), (2, 3), (3, 4)]],
            ) as query,
        ):
            self.assertEqual(cache.load(resolved, 0, 180, False), [0, 0, 3])
            self.assertEqual(cache.load(resolved, 0, 240, False), [1, 2, 3, 4])

        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args_list[1].args[2:], (0, 240))

    def test_active_screenpipe_cache_refreshes_only_acquisition_minutes(self):
        cache = DensityBucketCache()
        resolved = ResolvedDensitySource(DensitySource.SCREENPIPE_GRAPH, "unused.sqlite", None)
        with (
            patch(
                "core.widgets.yasb.pieces_density_source._query_screenpipe_refresh_state",
                side_effect=[
                    _ScreenpipeRefreshState(_ScreenpipeWatermark(1, "2026-10-23T10:00:00Z"), None),
                    _ScreenpipeRefreshState(_ScreenpipeWatermark(2, "2026-10-23T10:01:00Z"), 60),
                ],
            ),
            patch(
                "core.widgets.yasb.pieces_density_source.query_density_buckets",
                side_effect=[[(0, 1), (1, 2), (2, 3)], [(1, 20), (2, 3), (3, 4)]],
            ) as query,
        ):
            self.assertEqual(cache.load(resolved, 0, 180, False), [1, 2, 3])
            self.assertEqual(cache.load(resolved, 0, 240, False), [1, 20, 3, 4])

        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args_list[1].args[2:], (60, 240))

    def test_closed_interval_cache_avoids_reopening_the_database(self):
        cache = DensityBucketCache()
        resolved = ResolvedDensitySource(DensitySource.PIECES, "unused.sqlite", None)
        with patch(
            "core.widgets.yasb.pieces_density_source.query_density_buckets",
            return_value=[(0, 3)],
        ) as query:
            self.assertEqual(cache.load(resolved, 0, 60, True), [3])
            self.assertEqual(cache.load(resolved, 0, 60, True), [3])

        query.assert_called_once()

    def test_sliding_density_is_linear_and_keeps_edge_windows(self):
        self.assertEqual(integrate_density([1, 2, 3], radius=1), [3, 6, 5])


class RefreshTests(unittest.TestCase):
    def test_running_worker_is_cancelled_and_refresh_is_queued(self):
        class Worker:
            cancelled = False

            @staticmethod
            def isRunning() -> bool:
                return True

            def cancel(self) -> None:
                self.cancelled = True

        class Widget:
            _worker = Worker()
            _refresh_pending = False

            @staticmethod
            def _fetch_data() -> None:
                raise AssertionError("refresh must wait for the cancelled worker to finish")

            @property
            def worker_cancelled(self) -> bool:
                return self._worker.cancelled

            @property
            def refresh_pending(self) -> bool:
                return self._refresh_pending

        widget = Widget()
        PiecesDensityWidget.request_refresh(cast(PiecesDensityWidget, widget))

        self.assertTrue(widget.worker_cancelled)
        self.assertTrue(widget.refresh_pending)

    def test_session_navigation_preserves_duration_until_refresh_finishes(self):
        class SessionManagerStub:
            @staticmethod
            def get_sessions(_source: object) -> list[float]:
                return [100.0, 200.0, 300.0]

        class ControlsStub:
            def __init__(self) -> None:
                self.updates: list[float | None] = []

            def update_buttons(self, duration_sec: float | None = None) -> None:
                self.updates.append(duration_sec)

        class Widget:
            _session_manager = SessionManagerStub()
            _time_source = object()
            _selected_session_start = 200.0
            _controls_left = ControlsStub()
            _controls_right = ControlsStub()
            refreshes = 0

            def sessions(self) -> list[float]:
                return self._session_manager.get_sessions(self._time_source)

            def selected_session_index(self, sessions: list[float]) -> int | None:
                return selected_session_index(sessions, self._selected_session_start)

            def request_refresh(self) -> None:
                self.refreshes += 1

            @property
            def selected_session_start(self) -> float | None:
                return self._selected_session_start

            @property
            def right_updates(self) -> list[float | None]:
                return self._controls_right.updates

        widget = Widget()
        PiecesDensityWidget.select_previous_session(cast(PiecesDensityWidget, widget))

        self.assertEqual(widget.selected_session_start, 100.0)
        self.assertEqual(widget.right_updates, [None])
        self.assertEqual(widget.refreshes, 1)

        PiecesDensityWidget.select_next_session(cast(PiecesDensityWidget, widget))

        self.assertEqual(widget.selected_session_start, 200.0)
        self.assertEqual(widget.right_updates, [None, None])
        self.assertEqual(widget.refreshes, 2)


class RulerLabelTests(unittest.TestCase):
    def test_colon_is_centered_over_major_tick(self):
        label_x = ruler_label_x(
            tick_x=100,
            hour_width=10,
            colon_width=4,
            label_width=26,
            ruler_width=300,
        )

        self.assertEqual(label_x + 10 + 4 / 2, 100)

    def test_label_stays_on_normal_baseline_without_collision(self):
        label = QRectF(200, 60, 35, 12)
        exclusions = [QRectF(0, 55, 100, 25)]

        self.assertEqual(ruler_label_baseline(73, label, exclusions, 3), 73)

    def test_only_colliding_label_baseline_moves_above_control(self):
        label = QRectF(70, 60, 35, 12)
        exclusions = [QRectF(0, 55, 100, 25)]

        self.assertEqual(ruler_label_baseline(73, label, exclusions, 3), 56)


if __name__ == "__main__":
    unittest.main()
