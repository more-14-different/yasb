import pytest
from pydantic import ValidationError
from PyQt6.QtCore import QPropertyAnimation
from PyQt6.QtWidgets import QApplication, QWidget

from core.utils.utilities import PopupWidget, parse_label_template
from core.validation.widgets.yasb.window_switcher import WindowSwitcherConfig
from core.validation.widgets.yasb.windows_desktops import WindowsDesktopsConfig


def test_window_switcher_preview_config_and_bounds():
    config = WindowSwitcherConfig.model_validate({"preview": {"enabled": True, "width": 320}, "max_visible_apps": 7})

    assert config.preview.enabled is True
    assert config.preview.width == 320
    assert config.max_visible_apps == 7
    with pytest.raises(ValidationError):
        WindowSwitcherConfig.model_validate({"preview": {"width": 99}})


def test_windows_desktops_new_options_are_backward_compatible():
    defaults = WindowsDesktopsConfig.model_validate({})
    configured = WindowsDesktopsConfig.model_validate(
        {
            "label_workspace_populated_btn": "{index}:{name}",
            "enable_scroll_switching": True,
            "reverse_scroll_direction": True,
        }
    )

    assert defaults.label_workspace_populated_btn is None
    assert defaults.enable_scroll_switching is False
    assert configured.label_workspace_populated_btn == "{index}:{name}"
    assert configured.enable_scroll_switching is True
    assert configured.reverse_scroll_direction is True


def test_popup_animation_coexists_with_fork_label_parsing(qapp: QApplication):
    parent = QWidget()
    popup = PopupWidget(parent, animation=0)
    animation = next(value for value in vars(popup).values() if isinstance(value, QPropertyAnimation))

    assert animation.duration() == 0
    assert parse_label_template('<span class="accent">A</span> value') == [
        {"is_icon": True, "text": "A", "class_name": "accent"},
        {"is_icon": False, "text": "value", "class_name": ""},
    ]

    popup.deleteLater()
    parent.deleteLater()
    qapp.processEvents()
