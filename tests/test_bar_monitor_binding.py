import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.bar import Bar  # noqa: E402


class BarMonitorBindingTests(unittest.TestCase):
    def test_refresh_propagates_monitor_handle_and_notifies_widgets(self):
        class Widget:
            monitor_hwnd = 111

            def __init__(self):
                self.notifications = []

            def on_bar_geometry_changed(self, monitor_hwnd):
                self.notifications.append(monitor_hwnd)

        class TestBar:
            monitor_hwnd = 111
            _bar_name = "test"
            _widgets = {"left": [Widget()], "center": [], "right": []}

            class Screen:
                @staticmethod
                def name():
                    return "display"

            _target_screen = Screen()

            @staticmethod
            def winId():
                return 123

        bar = TestBar()
        widget = bar._widgets["left"][0]

        with patch("core.bar.get_monitor_hwnd", return_value=222):
            Bar._refresh_monitor_bindings(bar)

        self.assertEqual(bar.monitor_hwnd, 222)
        self.assertEqual(widget.monitor_hwnd, 222)
        self.assertEqual(widget.notifications, [222])

    def test_refresh_keeps_last_valid_handle_during_transient_lookup_failure(self):
        class Widget:
            monitor_hwnd = 111

        class TestBar:
            monitor_hwnd = 111
            _bar_name = "test"
            _widgets = {"left": [Widget()]}

            class Screen:
                @staticmethod
                def name():
                    return "display"

            _target_screen = Screen()

            @staticmethod
            def winId():
                return 123

        bar = TestBar()

        with patch("core.bar.get_monitor_hwnd", return_value=None):
            Bar._refresh_monitor_bindings(bar)

        self.assertEqual(bar.monitor_hwnd, 111)
        self.assertEqual(bar._widgets["left"][0].monitor_hwnd, 111)


if __name__ == "__main__":
    unittest.main()
