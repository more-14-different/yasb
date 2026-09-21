import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import main as yasb_main  # noqa: E402


class Kernel32Fake:
    def __init__(self):
        self._handles = iter((101, 102))
        self._errors = iter((183, 0))
        self.initial_owner_values = []
        self.closed_handles = []

    def CreateMutexW(self, _security, initial_owner, _name):
        self.initial_owner_values.append(initial_owner)
        return next(self._handles)

    def GetLastError(self):
        return next(self._errors)

    def CloseHandle(self, handle):
        self.closed_handles.append(handle)

    def ReleaseMutex(self, _handle):
        raise AssertionError("a non-owned mutex must never be released")


class SingleInstanceTests(unittest.TestCase):
    def test_restart_wait_retries_without_claiming_or_releasing_mutex_ownership(self):
        kernel32 = Kernel32Fake()

        with (
            patch.object(yasb_main.ctypes.windll, "kernel32", kernel32),
            patch.object(yasb_main.sys, "argv", ["yasb", "--restart-wait"]),
            patch.object(yasb_main.time, "time", side_effect=(0.0, 0.0)),
            patch.object(yasb_main.time, "monotonic", side_effect=(0.0, 0.0)),
            patch.object(yasb_main.time, "sleep", return_value=None),
        ):
            with yasb_main.single_instance_lock() as handle:
                self.assertEqual(handle, 102)

        self.assertEqual(kernel32.initial_owner_values, [False, False])
        self.assertEqual(kernel32.closed_handles, [101, 102])

    def test_parent_timeout_closes_acquired_mutex_before_aborting(self):
        kernel32 = Kernel32Fake()
        kernel32.ReleaseMutex = lambda _handle: None

        with (
            patch.object(yasb_main.ctypes.windll, "kernel32", kernel32),
            patch.object(
                yasb_main.sys,
                "argv",
                ["yasb", "--restart-wait", "--restart-parent-pid", "999"],
            ),
            patch.object(yasb_main.time, "time", side_effect=(0.0, 0.0)),
            patch.object(yasb_main.time, "monotonic", side_effect=(0.0, 0.0)),
            patch.object(yasb_main.time, "sleep", return_value=None),
            patch.object(yasb_main, "_wait_for_process_exit", return_value=False),
        ):
            with self.assertRaises(SystemExit):
                with yasb_main.single_instance_lock():
                    self.fail("the application must not start while its restart parent is alive")

        self.assertEqual(kernel32.closed_handles, [101, 102])


if __name__ == "__main__":
    unittest.main()
