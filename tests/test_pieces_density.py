import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
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
    def test_auto_prefers_screenpipe_and_keeps_pieces_fallback(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            screenpipe = root / "screenpipe.sqlite"
            pieces = root / "pieces.sqlite"
            pieces.touch()

            fallback = resolve_density_source("auto", str(screenpipe), str(pieces))
            self.assertEqual(fallback.source, DensitySource.PIECES)

            screenpipe.touch()
            preferred = resolve_density_source("auto", str(screenpipe), str(pieces))
            self.assertEqual(preferred.source, DensitySource.SCREENPIPE)

    def test_screenpipe_density_matches_graph_raw_observation_policy(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "screenpipe.sqlite"
            connection = sqlite3.connect(database_path)
            try:
                connection.executescript(
                    "create table frames (timestamp text, focused integer);"
                    "create table ui_events (timestamp text, event_type text);"
                    "insert into frames values ('2026-07-19T10:00:10.000000+00:00', 1);"
                    "insert into frames values ('2026-07-19T10:00:20.000000+00:00', 0);"
                    "insert into ui_events values ('2026-07-19T10:01:10.000000+00:00', 'key');"
                    "insert into ui_events values ('2026-07-19T10:01:20.000000+00:00', 'move');"
                )
                connection.commit()
            finally:
                connection.close()
            start = datetime.fromisoformat("2026-07-19T10:00:00+00:00").timestamp()
            resolved = ResolvedDensitySource(DensitySource.SCREENPIPE, str(database_path))

            self.assertEqual(
                query_density_buckets(resolved, start, start, start + 120),
                [(0, 1), (1, 1)],
            )

    def test_active_cache_only_refreshes_the_two_newest_minutes(self):
        cache = DensityBucketCache()
        resolved = ResolvedDensitySource(DensitySource.SCREENPIPE, "unused.sqlite")
        with patch(
            "core.widgets.yasb.pieces_density_source.query_density_buckets",
            side_effect=[[(0, 1), (1, 2), (2, 3)], [(2, 4), (3, 5)]],
        ) as query:
            self.assertEqual(cache.load(resolved, 0, 180, False), [1, 2, 3])
            self.assertEqual(cache.load(resolved, 0, 240, False), [1, 2, 4, 5])

        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args_list[1].args[2:], (120, 240))

    def test_closed_interval_cache_avoids_reopening_the_database(self):
        cache = DensityBucketCache()
        resolved = ResolvedDensitySource(DensitySource.PIECES, "unused.sqlite")
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
            def isRunning():
                return True

            def cancel(self):
                self.cancelled = True

        class Widget:
            _worker = Worker()
            _refresh_pending = False

            @staticmethod
            def _fetch_data():
                raise AssertionError("refresh must wait for the cancelled worker to finish")

        widget = Widget()
        PiecesDensityWidget._request_refresh(widget)

        self.assertTrue(widget._worker.cancelled)
        self.assertTrue(widget._refresh_pending)

    def test_session_navigation_preserves_duration_until_refresh_finishes(self):
        class SessionManagerStub:
            @staticmethod
            def get_sessions(_source):
                return [100.0, 200.0, 300.0]

        class ControlsStub:
            def __init__(self):
                self.updates = []

            def update_buttons(self, duration_sec=None):
                self.updates.append(duration_sec)

        class Widget:
            _session_manager = SessionManagerStub()
            _time_source = object()
            _selected_session_start = 200.0
            _controls_left = ControlsStub()
            _controls_right = ControlsStub()
            refreshes = 0

            def _selected_session_index(self, sessions):
                return selected_session_index(sessions, self._selected_session_start)

            def _request_refresh(self):
                self.refreshes += 1

        widget = Widget()
        PiecesDensityWidget._prev_session(widget)

        self.assertEqual(widget._selected_session_start, 100.0)
        self.assertEqual(widget._controls_right.updates, [None])
        self.assertEqual(widget.refreshes, 1)

        PiecesDensityWidget._next_session(widget)

        self.assertEqual(widget._selected_session_start, 200.0)
        self.assertEqual(widget._controls_right.updates, [None, None])
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
