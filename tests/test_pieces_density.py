import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import QRectF

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.widgets.yasb.pieces_density import (  # noqa: E402
    PiecesDensityWidget,
    SessionManager,
    ruler_label_baseline,
    selected_session_index,
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
            conn.execute(
                "create table livestreams ("
                "official_start_at_utc_us integer, official_end_at_utc_us integer)"
            )
            machine_end_columns = (
                ", shutdown_at_utc_us integer, shutdown_upper_bound_utc_us integer"
                if include_required_columns
                else ""
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


class RulerLabelTests(unittest.TestCase):
    def test_label_stays_on_normal_baseline_without_collision(self):
        label = QRectF(200, 60, 35, 12)
        exclusions = [QRectF(0, 55, 100, 25)]

        self.assertEqual(ruler_label_baseline(73, label, exclusions, 3), 73)

    def test_only_colliding_label_baseline_moves_above_control(self):
        label = QRectF(70, 60, 35, 12)
        exclusions = [QRectF(0, 55, 100, 25)]

        self.assertEqual(ruler_label_baseline(73, label, exclusions, 3), 59)


if __name__ == "__main__":
    unittest.main()
