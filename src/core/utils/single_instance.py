import contextlib
import logging
import sys
import time

from core.utils.win32.bindings.kernel32 import (
    CloseHandle,
    CreateMutex,
    GetLastError,
    OpenMutex,
    OpenProcess,
    WaitForSingleObject,
)
from core.utils.win32.constants import ERROR_ALREADY_EXISTS, SYNCHRONIZE, WAIT_OBJECT_0

BAR_MUTEX_NAME = "yasb_reborn"


def _get_arg_value(name: str) -> str | None:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return None
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


def _wait_for_process_exit(process_id: int, timeout: float) -> bool:
    handle = OpenProcess(SYNCHRONIZE, False, process_id)
    if not handle:
        return True
    try:
        return WaitForSingleObject(handle, max(0, int(timeout * 1000))) == WAIT_OBJECT_0
    finally:
        CloseHandle(handle)


@contextlib.contextmanager
def single_instance_lock(name: str = BAR_MUTEX_NAME):
    """Hold a named mutex so only one YASB instance runs at a time."""

    deadline = time.monotonic() + (10.0 if "--restart-wait" in sys.argv else 0.0)
    waiting = False

    while True:
        mutex = CreateMutex(None, False, name)
        if not mutex:
            logging.error("Failed to create mutex.")
            sys.exit(1)
        if GetLastError() != ERROR_ALREADY_EXISTS:
            break

        CloseHandle(mutex)
        if time.monotonic() >= deadline:
            logging.error(
                "Timeout waiting for previous instance. Aborting start."
                if waiting
                else "Another instance of the YASB is already running."
            )
            sys.exit(1)
        if not waiting:
            logging.info("Waiting for previous YASB instance to exit...")
            waiting = True
        time.sleep(0.25)

    if waiting:
        logging.info("Previous instance exited, continuing startup.")

    restart_parent_pid_value = _get_arg_value("--restart-parent-pid")
    if restart_parent_pid_value and restart_parent_pid_value.isdigit():
        restart_parent_pid = int(restart_parent_pid_value)
        logging.info("Waiting for previous YASB process %s to terminate...", restart_parent_pid)
        if not _wait_for_process_exit(restart_parent_pid, timeout=10):
            CloseHandle(mutex)
            logging.error("Timeout waiting for previous YASB process to terminate. Aborting start.")
            sys.exit(1)

    # Windows closes the handle once this process has exited; closing it earlier would let a waiting
    # instance start while this one is still shutting down.
    yield mutex


def wait_for_bar_exit(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        handle = OpenMutex(SYNCHRONIZE, False, BAR_MUTEX_NAME)
        if not handle:
            return True
        CloseHandle(handle)
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
