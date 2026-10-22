import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.utils import single_instance  # noqa: E402


class SingleInstanceTests(unittest.TestCase):
    def test_restart_wait_retries_without_claiming_mutex_ownership(self):
        create_mutex = Mock(side_effect=(101, 102))
        get_last_error = Mock(side_effect=(183, 0))
        close_handle = Mock()

        with (
            patch.object(single_instance, "CreateMutex", create_mutex),
            patch.object(single_instance, "GetLastError", get_last_error),
            patch.object(single_instance, "CloseHandle", close_handle),
            patch.object(single_instance.sys, "argv", ["yasb", "--restart-wait"]),
            patch.object(single_instance.time, "monotonic", side_effect=(0.0, 0.0)),
            patch.object(single_instance.time, "sleep", return_value=None),
        ):
            with single_instance.single_instance_lock() as handle:
                self.assertEqual(handle, 102)

        self.assertEqual(
            [call.args[1] for call in create_mutex.call_args_list],
            [False, False],
        )
        close_handle.assert_called_once_with(101)

    def test_parent_timeout_closes_acquired_mutex_before_aborting(self):
        close_handle = Mock()

        with (
            patch.object(single_instance, "CreateMutex", return_value=102),
            patch.object(single_instance, "GetLastError", return_value=0),
            patch.object(single_instance, "CloseHandle", close_handle),
            patch.object(
                single_instance.sys,
                "argv",
                ["yasb", "--restart-wait", "--restart-parent-pid", "999"],
            ),
            patch.object(single_instance, "_wait_for_process_exit", return_value=False),
        ):
            with self.assertRaises(SystemExit):
                with single_instance.single_instance_lock():
                    self.fail("the application must not start while its restart parent is alive")

        close_handle.assert_called_once_with(102)


if __name__ == "__main__":
    unittest.main()
