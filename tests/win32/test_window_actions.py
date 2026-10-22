from unittest.mock import Mock

import pytest

import core.utils.win32.window_actions as window_actions
from core.utils.win32.constants import (
    GWL_STYLE,
    HWND_TOP,
    SC_MINIMIZE,
    SC_RESTORE,
    SW_MINIMIZE,
    SWP_ASYNCWINDOWPOS,
    SWP_NOACTIVATE,
    SWP_NOMOVE,
    SWP_NOSIZE,
    WM_SYSCOMMAND,
    WS_MINIMIZEBOX,
)


@pytest.mark.parametrize(
    ("style", "enabled", "expected"),
    [
        (WS_MINIMIZEBOX, True, True),
        (WS_MINIMIZEBOX, False, False),
        (0, True, False),
    ],
)
def test_can_minimize(monkeypatch: pytest.MonkeyPatch, style: int, enabled: bool, expected: bool):
    get_window_long = Mock(return_value=style)
    is_window_enabled = Mock(return_value=enabled)
    monkeypatch.setattr(window_actions, "GetWindowLong", get_window_long)
    monkeypatch.setattr(window_actions, "IsWindowEnabled", is_window_enabled)

    assert window_actions.can_minimize(123) is expected
    get_window_long.assert_called_once_with(123, GWL_STYLE)
    if style & WS_MINIMIZEBOX:
        is_window_enabled.assert_called_once_with(123)
    else:
        is_window_enabled.assert_not_called()


def test_can_minimize_closed_window(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(window_actions, "GetWindowLong", Mock(return_value=0))
    monkeypatch.setattr(window_actions, "IsWindowEnabled", Mock(return_value=False))

    assert window_actions.can_minimize(456) is False


def test_restore_posts_syscommand_without_waiting(monkeypatch: pytest.MonkeyPatch):
    post_message = Mock(return_value=1)
    send_message_timeout = Mock(return_value=1)
    show_window_async = Mock(side_effect=AssertionError("successful PostMessage must not use the fallback"))
    monkeypatch.setattr(window_actions, "PostMessage", post_message)
    monkeypatch.setattr(window_actions.u32, "SendMessageTimeoutW", send_message_timeout)
    monkeypatch.setattr(window_actions.u32, "ShowWindowAsync", show_window_async)

    window_actions.restore_window(123)

    post_message.assert_called_once_with(123, WM_SYSCOMMAND, SC_RESTORE, 0)
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

    post_message.assert_called_once_with(456, WM_SYSCOMMAND, SC_MINIMIZE, 0)
    send_message_timeout.assert_not_called()
    show_window_async.assert_called_once_with(456, SW_MINIMIZE)


def test_set_foreground_uses_async_set_window_pos(monkeypatch: pytest.MonkeyPatch):
    set_window_pos = Mock(return_value=1)
    set_foreground_window = Mock(return_value=1)
    monkeypatch.setattr(window_actions, "IsWindow", Mock(return_value=True))
    monkeypatch.setattr(window_actions, "IsHungAppWindow", Mock(return_value=False))
    monkeypatch.setattr(window_actions.u32, "SetWindowPos", set_window_pos)
    monkeypatch.setattr(window_actions.u32, "SetForegroundWindow", set_foreground_window)

    window_actions.set_foreground(789)

    expected_flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
    set_window_pos.assert_called_once_with(789, HWND_TOP, 0, 0, 0, 0, expected_flags)
    set_foreground_window.assert_called_once_with(789)


def test_hung_window_short_circuits_resolution_and_foreground(monkeypatch: pytest.MonkeyPatch):
    get_ancestor = Mock(side_effect=AssertionError("hung window must not be queried"))
    set_window_pos = Mock(side_effect=AssertionError("hung window must not be repositioned"))
    set_foreground_window = Mock(side_effect=AssertionError("hung window must not be focused"))
    monkeypatch.setattr(window_actions, "IsWindow", Mock(return_value=True))
    monkeypatch.setattr(window_actions, "IsHungAppWindow", Mock(return_value=True))
    monkeypatch.setattr(window_actions.u32, "GetAncestor", get_ancestor)
    monkeypatch.setattr(window_actions.u32, "SetWindowPos", set_window_pos, raising=False)
    monkeypatch.setattr(window_actions.u32, "SetForegroundWindow", set_foreground_window)

    assert window_actions.resolve_base_and_focus(321) == (321, 321)
    window_actions.set_foreground(321)
    get_ancestor.assert_not_called()
    set_window_pos.assert_not_called()
    set_foreground_window.assert_not_called()
