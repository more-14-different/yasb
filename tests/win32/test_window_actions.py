from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
import win32api
import win32con
import win32gui

import core.utils.win32.window_actions as window_actions
from core.utils.win32.window_actions import can_minimize

if TYPE_CHECKING:
    from _win32typing import PyResourceId  # pyright: ignore[reportMissingModuleSource]

WINDOWS = {
    "normal": (win32con.WS_OVERLAPPEDWINDOW, True),
    "popup with minimize box": (win32con.WS_POPUP | win32con.WS_SYSMENU | win32con.WS_MINIMIZEBOX, True),
    "no minimize box": (win32con.WS_OVERLAPPED | win32con.WS_CAPTION | win32con.WS_SYSMENU, False),
    "disabled": (win32con.WS_OVERLAPPEDWINDOW | win32con.WS_DISABLED, False),
}


@pytest.fixture(scope="module")
def window_class() -> Iterator[PyResourceId]:
    instance = win32api.GetModuleHandle(None)
    wc = win32gui.WNDCLASS()
    wc.hInstance = instance  # pyright: ignore[reportAttributeAccessIssue]
    wc.lpszClassName = "YasbTestWindow"  # pyright: ignore[reportAttributeAccessIssue]
    wc.lpfnWndProc = {}  # pyright: ignore[reportAttributeAccessIssue]
    atom = win32gui.RegisterClass(wc)
    yield atom
    win32gui.UnregisterClass(atom, instance)


@pytest.fixture
def make_window(window_class: PyResourceId) -> Iterator[Callable[[int], int]]:
    created: list[int] = []

    def make(style: int) -> int:
        hwnd = win32gui.CreateWindow(
            window_class, "YASB test window", style, 0, 0, 200, 100, 0, 0, win32api.GetModuleHandle(None), None
        )
        created.append(hwnd)
        return hwnd

    yield make
    for hwnd in created:
        if win32gui.IsWindow(hwnd):
            win32gui.DestroyWindow(hwnd)


@pytest.mark.parametrize(("style", "expected"), list(WINDOWS.values()), ids=list(WINDOWS))
def test_can_minimize(make_window: Callable[[int], int], style: int, expected: bool):
    assert can_minimize(make_window(style)) is expected


def test_can_minimize_closed_window(make_window: Callable[[int], int]):
    hwnd = make_window(win32con.WS_OVERLAPPEDWINDOW)
    win32gui.DestroyWindow(hwnd)

    assert can_minimize(hwnd) is False


def test_restore_posts_syscommand_without_waiting(monkeypatch: pytest.MonkeyPatch):
    post_message = Mock(return_value=1)
    send_message_timeout = Mock(return_value=1)
    show_window_async = Mock(side_effect=AssertionError("successful PostMessage must not use the fallback"))
    monkeypatch.setattr(window_actions, "PostMessage", post_message)
    monkeypatch.setattr(window_actions.u32, "SendMessageTimeoutW", send_message_timeout)
    monkeypatch.setattr(window_actions.u32, "ShowWindowAsync", show_window_async)

    window_actions.restore_window(123)

    post_message.assert_called_once_with(123, win32con.WM_SYSCOMMAND, win32con.SC_RESTORE, 0)
    send_message_timeout.assert_not_called()
    show_window_async.assert_not_called()


def test_failed_post_falls_back_to_show_window_async(monkeypatch: pytest.MonkeyPatch):
    post_message = Mock(return_value=0)
    send_message_timeout = Mock(return_value=0)
    show_window_async = Mock(return_value=1)
    monkeypatch.setattr(window_actions, "PostMessage", post_message)
    monkeypatch.setattr(window_actions.u32, "SendMessageTimeoutW", send_message_timeout)
    monkeypatch.setattr(window_actions.u32, "ShowWindowAsync", show_window_async)

    window_actions.minimize_window(456)

    post_message.assert_called_once_with(456, win32con.WM_SYSCOMMAND, win32con.SC_MINIMIZE, 0)
    send_message_timeout.assert_not_called()
    show_window_async.assert_called_once_with(456, win32con.SW_MINIMIZE)


def test_set_foreground_uses_async_set_window_pos(monkeypatch: pytest.MonkeyPatch):
    is_window = Mock(return_value=True)
    is_hung = Mock(return_value=False)
    get_window_thread = Mock(return_value=(1, 2))
    get_current_thread = Mock(return_value=1)
    bring_to_top = Mock(return_value=1)
    set_active = Mock(return_value=1)
    set_window_pos = Mock(return_value=1)
    set_foreground_window = Mock(return_value=1)
    monkeypatch.setattr(window_actions.win32gui, "IsWindow", is_window)
    monkeypatch.setattr(window_actions.u32, "IsHungAppWindow", is_hung)
    monkeypatch.setattr(window_actions.win32process, "GetWindowThreadProcessId", get_window_thread)
    monkeypatch.setattr(window_actions.k32, "GetCurrentThreadId", get_current_thread)
    monkeypatch.setattr(window_actions.u32, "BringWindowToTop", bring_to_top)
    monkeypatch.setattr(window_actions.u32, "SetActiveWindow", set_active)
    monkeypatch.setattr(window_actions.u32, "SetWindowPos", set_window_pos)
    monkeypatch.setattr(window_actions.u32, "SetForegroundWindow", set_foreground_window)

    window_actions.set_foreground(789)

    expected_flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_ASYNCWINDOWPOS
    set_window_pos.assert_called_once_with(789, win32con.HWND_TOP, 0, 0, 0, 0, expected_flags)
    set_foreground_window.assert_called_once_with(789)
    get_window_thread.assert_not_called()
    get_current_thread.assert_not_called()
    bring_to_top.assert_not_called()
    set_active.assert_not_called()


def test_hung_window_short_circuits_resolution_and_foreground(monkeypatch: pytest.MonkeyPatch):
    get_ancestor = Mock(side_effect=AssertionError("hung window must not be queried"))
    set_window_pos = Mock(side_effect=AssertionError("hung window must not be repositioned"))
    set_foreground_window = Mock(side_effect=AssertionError("hung window must not be focused"))
    monkeypatch.setattr(window_actions.win32gui, "IsWindow", Mock(return_value=True))
    monkeypatch.setattr(window_actions.u32, "IsHungAppWindow", Mock(return_value=True))
    monkeypatch.setattr(window_actions.u32, "GetAncestor", get_ancestor)
    monkeypatch.setattr(window_actions.u32, "SetWindowPos", set_window_pos, raising=False)
    monkeypatch.setattr(window_actions.u32, "SetForegroundWindow", set_foreground_window)

    assert window_actions.resolve_base_and_focus(321) == (321, 321)
    window_actions.set_foreground(321)
    get_ancestor.assert_not_called()
    set_window_pos.assert_not_called()
    set_foreground_window.assert_not_called()
