import logging
from contextlib import suppress
from typing import Literal

from PIL import Image
from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from core.events.komorebi import KomorebiEvent
from core.events.service import EventService
from core.utils.utilities import refresh_widget_style
from core.utils.win32.app_icons import get_window_icon
from core.utils.win32.utils import get_monitor_hwnd, get_process_info
from core.utils.win32.window_actions import restore_window, set_foreground, show_window
from core.validation.widgets.komorebi.workspaces import KomorebiWorkspacesConfig
from core.widgets.base import BaseWidget
from core.widgets.services.komorebi.client import KomorebiClient

try:
    from core.widgets.services.komorebi.event_listener import KomorebiEventListener
except ImportError:
    KomorebiEventListener = None
    logging.warning("Failed to load Komorebi Event Listener")

WorkspaceStatus = Literal["EMPTY", "POPULATED", "ACTIVE"]
WORKSPACE_STATUS_EMPTY: WorkspaceStatus = "EMPTY"
WORKSPACE_STATUS_POPULATED: WorkspaceStatus = "POPULATED"
WORKSPACE_STATUS_ACTIVE: WorkspaceStatus = "ACTIVE"


def _set_workspace_button_class(widget: QWidget, status: WorkspaceStatus, pending: bool = False) -> None:
    current_class = str(widget.property("class") or "")
    button_classes = [cls for cls in current_class.split() if cls.startswith("button-")]
    classes = ["ws-btn"]
    if pending:
        classes.append("pending")
    classes.append(status.lower())
    classes.extend(button_classes)
    widget.setProperty("class", " ".join(classes))


class WorkspaceButton(QPushButton):
    def __init__(
        self,
        workspace_index: int,
        parent_widget: WorkspaceWidget,
        config: KomorebiWorkspacesConfig,
        label: str = None,
        active_label: str = None,
        populated_label: str = None,
    ):
        super().__init__(parent_widget._workspace_container)
        self.komorebic = KomorebiClient()
        self.workspace_index = workspace_index
        self.parent_widget = parent_widget
        self.config = config
        self.status = WORKSPACE_STATUS_EMPTY
        self.setProperty("class", "ws-btn")
        self.default_label = label if label else str(workspace_index + 1)
        self.active_label = active_label if active_label else self.default_label
        self.populated_label = populated_label if populated_label else self.default_label
        self.setText(self.default_label)
        self.clicked.connect(self.activate_workspace)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, self.sizePolicy().verticalPolicy())
        self.hide()
        self.update_and_redraw(self.status)

    def update_visible_buttons(self):
        visible_buttons = [btn for btn in self.parent_widget._workspace_buttons if not btn.isHidden()]
        for index, button in enumerate(visible_buttons):
            current_class = button.property("class")
            new_class = " ".join([cls for cls in current_class.split() if not cls.startswith("button-")])
            new_class = f"{new_class} button-{index + 1}"
            button.setProperty("class", new_class)
            refresh_widget_style(button)

    def update_and_redraw(self, status: WorkspaceStatus):
        self.status = status
        _set_workspace_button_class(self, status)
        if status == WORKSPACE_STATUS_ACTIVE:
            self.setText(self.active_label)
        elif status == WORKSPACE_STATUS_POPULATED:
            self.setText(self.populated_label)
        else:
            self.setText(self.default_label)
        refresh_widget_style(self)

    def activate_workspace(self):
        try:
            self.parent_widget.request_workspace_switch(self.workspace_index)
        except Exception:
            self.parent_widget.clear_pending_workspace()
            logging.exception("Failed to focus workspace at index %s", self.workspace_index)


class WorkspaceButtonWithIcons(QFrame):
    def __init__(
        self,
        workspace_index: int,
        parent_widget: WorkspaceWidget,
        config: KomorebiWorkspacesConfig,
        label: str = None,
        active_label: str = None,
        populated_label: str = None,
    ):
        super().__init__(parent_widget._workspace_container)
        self.komorebic = KomorebiClient()
        self.workspace_index = workspace_index
        self.parent_widget = parent_widget
        self.config = config
        self.status = WORKSPACE_STATUS_EMPTY
        self.setProperty("class", "ws-btn")
        self.default_label = label if label else str(workspace_index + 1)
        self.active_label = active_label if active_label else self.default_label
        self.populated_label = populated_label if populated_label else self.default_label

        self.setSizePolicy(QSizePolicy.Policy.Fixed, self.sizePolicy().verticalPolicy())

        self.button_layout = QHBoxLayout(self)
        self.button_layout.setContentsMargins(0, 0, 0, 0)
        self.button_layout.setSpacing(0)

        self.text_label = QLabel(self.default_label)
        self.text_label.setProperty("class", "label")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.button_layout.addWidget(self.text_label)

        self.icons = []
        self.icon_labels = []
        self.hide()
        self.update_icons()
        self.update_and_redraw(self.status)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activate_workspace()

    def update_visible_buttons(self):
        visible_buttons = [btn for btn in self.parent_widget._workspace_buttons if not btn.isHidden()]
        for index, button in enumerate(visible_buttons):
            current_class = button.property("class")
            new_class = " ".join([cls for cls in current_class.split() if not cls.startswith("button-")])
            new_class = f"{new_class} button-{index + 1}"
            button.setProperty("class", new_class)
            refresh_widget_style(button)

    def update_and_redraw(self, status: WorkspaceStatus):
        self.status = status
        _set_workspace_button_class(self, status)
        if status == WORKSPACE_STATUS_ACTIVE:
            self.text_label.setText(self.active_label)
        elif status == WORKSPACE_STATUS_POPULATED:
            self.text_label.setText(self.populated_label)
        else:
            self.text_label.setText(self.default_label)
        refresh_widget_style(self)

    def update_icons(self, icons: dict[int, QPixmap] = None):
        if icons:
            for icon_entry in self.icons:
                hwnd = icon_entry["hwnd"]
                if hwnd in icons:
                    icon_entry["pixmap"] = icons[hwnd]
        else:
            self.icons = self.parent_widget._get_all_icons_in_workspace(self.workspace_index)

        if (
            not self.config.app_icons.enabled_active
            and self.workspace_index == self.parent_widget._curr_workspace_index
        ):
            icons_list = []
        elif (
            not self.config.app_icons.enabled_populated
            and self.workspace_index != self.parent_widget._curr_workspace_index
        ):
            icons_list = []
        else:
            icons_list = [icon_entry for icon_entry in self.icons if icon_entry["pixmap"] is not None]
            if self.config.app_icons.max_icons > 0:
                icons_list = icons_list[: self.config.app_icons.max_icons]

        # Remove extra icon widgets if there are more than needed.
        for extra_label in self.icon_labels[len(icons_list) :]:
            self.button_layout.removeWidget(extra_label)
            extra_label.setParent(None)
        self.icon_labels = self.icon_labels[: len(icons_list)]

        # Keep icon widgets aligned with the workspace window ordering.
        for index, icon_entry in enumerate(icons_list):
            if index < len(self.icon_labels):
                self.icon_labels[index].update_icon(icon_entry)
            else:
                icon_label = WorkspaceAppIconLabel(self.workspace_index, self.parent_widget)
                icon_label.update_icon(icon_entry)
                self.button_layout.addWidget(icon_label)
                self.icon_labels.append(icon_label)

        if self.config.app_icons.hide_label and len(self.icon_labels) > 0:
            self.text_label.hide()
        else:
            self.text_label.show()

    def update_icon_by_hwnd(self, hwnd: int):
        if any(icon_entry["hwnd"] == hwnd for icon_entry in self.icons):
            pixmap = self.parent_widget._get_app_icon(hwnd, self.workspace_index, ignore_cache=True)
            if pixmap:
                self.update_icons(icons={hwnd: pixmap})

    def activate_workspace(self):
        try:
            self.parent_widget.request_workspace_switch(self.workspace_index)
        except Exception:
            self.parent_widget.clear_pending_workspace()
            logging.exception("Failed to focus workspace at index %s", self.workspace_index)


class WorkspaceAppIconLabel(QLabel):
    def __init__(self, workspace_index: int, parent_widget: "WorkspaceWidget"):
        super().__init__()
        self.workspace_index = workspace_index
        self.parent_widget = parent_widget
        self.target_hwnd = None
        self.app_key = None

    def update_icon(self, icon_entry: dict):
        self.target_hwnd = icon_entry["hwnd"]
        self.app_key = icon_entry["app_key"]
        self.setProperty("class", icon_entry["class_name"])
        self.setPixmap(icon_entry["pixmap"])
        refresh_widget_style(self)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.parent_widget.focus_workspace_window(self.workspace_index, self.target_hwnd, self.app_key)
            event.accept()
            return
        super().mousePressEvent(event)


class WorkspaceWidget(BaseWidget):
    k_signal_connect = pyqtSignal(dict)
    k_signal_update = pyqtSignal(dict, dict)
    k_signal_disconnect = pyqtSignal()
    k_signal_workspace_pending = pyqtSignal(object, object)
    validation_schema = KomorebiWorkspacesConfig
    event_listener = KomorebiEventListener
    _pending_clear_delay_ms = 120

    def __init__(self, config: KomorebiWorkspacesConfig):
        super().__init__(class_name="komorebi-workspaces")
        self.config = config
        self._event_service = EventService()
        self._komorebic = KomorebiClient()

        self._workspace_app_icons_enabled = (
            self.config.app_icons.enabled_populated or self.config.app_icons.enabled_active
        )
        self._komorebi_screen = None
        self._komorebi_workspaces = []
        self._prev_workspace_index = None
        self._curr_workspace_index = None
        self._pending_workspace_index = None
        self._pending_switch_token = 0
        self._prev_num_windows_in_workspaces = []
        self._curr_num_windows_in_workspaces = []
        self._workspace_last_active_hwnd = {}
        self._workspace_app_last_active_hwnd = {}
        self._workspace_buttons: list[WorkspaceButton] = []
        self._workspace_focus_events = [
            KomorebiEvent.CycleFocusWorkspace.value,
            KomorebiEvent.CycleFocusMonitor.value,
            KomorebiEvent.FocusMonitorWorkspaceNumber.value,
            KomorebiEvent.FocusMonitorNumber.value,
            KomorebiEvent.FocusWorkspaceNumber.value,
            KomorebiEvent.ToggleWorkspaceLayer.value,
        ]
        self._update_buttons_event_watchlist = [
            KomorebiEvent.EnsureWorkspaces.value,
            KomorebiEvent.Manage.value,
            KomorebiEvent.MoveContainerToWorkspaceNumber.value,
            KomorebiEvent.NewWorkspace.value,
            KomorebiEvent.ReloadConfiguration.value,
            KomorebiEvent.SendContainerToMonitorNumber.value,
            KomorebiEvent.SendContainerToWorkspaceNumber.value,
            KomorebiEvent.Unmanage.value,
            KomorebiEvent.WatchConfiguration.value,
            KomorebiEvent.WorkspaceName.value,
            KomorebiEvent.Cloak.value,
        ]
        if self.config.hide_if_offline:
            self.hide()
        # Status text shown when komorebi state can't be retrieved
        self._offline_text = QLabel()
        self._offline_text.setText(self.config.label_offline)
        self._offline_text.setProperty("class", "offline-status")
        # Construct container which holds workspace buttons
        self._workspace_container_layout = QHBoxLayout()
        self._workspace_container_layout.setSpacing(0)
        self._workspace_container_layout.setContentsMargins(0, 0, 0, 0)
        self._workspace_container_layout.addWidget(self._offline_text)
        self._workspace_container = QFrame()
        self._workspace_container.setLayout(self._workspace_container_layout)
        self._workspace_container.setProperty("class", "widget-container")
        self._workspace_container.hide()
        self.widget_layout.addWidget(self._offline_text)
        self.widget_layout.addWidget(self._workspace_container)

        self.float_override_label = QLabel()
        self.float_override_label.setText(self.config.label_float_override)
        self.float_override_label.setProperty("class", "float-override")
        self.float_override_label.hide()
        self.widget_layout.addWidget(self.float_override_label)

        if self.config.toggle_workspace_layer.enabled:
            self.workspace_layer_label = QLabel()
            self.workspace_layer_label.setProperty("class", "workspace-layer")
            self.widget_layout.addWidget(self.workspace_layer_label)

        self._icon_cache = dict()
        self.dpi = None

        self._register_signals_and_events()

    def _register_signals_and_events(self):
        self.k_signal_connect.connect(self._on_komorebi_connect_event)
        self.k_signal_update.connect(self._on_komorebi_update_event)
        self.k_signal_disconnect.connect(self._on_komorebi_disconnect_event)
        self.k_signal_workspace_pending.connect(self._on_workspace_pending_event)
        self._event_service.register_event(KomorebiEvent.KomorebiConnect, self.k_signal_connect)
        self._event_service.register_event(KomorebiEvent.KomorebiDisconnect, self.k_signal_disconnect)
        self._event_service.register_event(KomorebiEvent.KomorebiUpdate, self.k_signal_update)
        self._event_service.register_event("komorebi_workspace_pending", self.k_signal_workspace_pending)
        try:
            self.destroyed.connect(self._on_destroyed)  # type: ignore[attr-defined]
        except Exception:
            pass

    def _on_destroyed(self, *args):
        try:
            self._event_service.unregister_event(KomorebiEvent.KomorebiConnect, self.k_signal_connect)
            self._event_service.unregister_event(KomorebiEvent.KomorebiDisconnect, self.k_signal_disconnect)
            self._event_service.unregister_event(KomorebiEvent.KomorebiUpdate, self.k_signal_update)
            self._event_service.unregister_event("komorebi_workspace_pending", self.k_signal_workspace_pending)
        except Exception:
            pass

    def _reset(self):
        self._komorebi_state = None
        self._komorebi_screen = None
        self._komorebi_workspaces = []
        self._curr_workspace_index = None
        self._prev_workspace_index = None
        self._pending_workspace_index = None
        self._pending_switch_token += 1
        self._workspace_last_active_hwnd = {}
        self._workspace_app_last_active_hwnd = {}
        self._workspace_buttons = []
        self._clear_container_layout()

    def _on_komorebi_connect_event(self, state: dict) -> None:
        self._reset()
        self._hide_offline_status()
        if self._update_komorebi_state(state):
            self._remember_active_window()
            self._add_or_update_buttons()
        if self.config.hide_if_offline:
            self.show()

    def _on_komorebi_disconnect_event(self) -> None:
        self._show_offline_status()
        if self.config.hide_if_offline:
            self.hide()

    def _on_workspace_pending_event(self, target: int | str, monitor_index: int | None) -> None:
        if monitor_index is not None:
            if not self._komorebi_screen or self._komorebi_screen.get("index") != monitor_index:
                return
        elif not self._is_focused_monitor():
            return

        workspace_index = self._resolve_pending_workspace_index(target)
        if workspace_index is None:
            return
        self.set_pending_workspace(workspace_index)

    def _on_komorebi_update_event(self, event: dict, state: dict) -> None:
        if self._update_komorebi_state(state):
            active_workspace_changed = self._has_active_workspace_index_changed()
            if self._pending_workspace_index is not None and active_workspace_changed:
                try:
                    prev_workspace_button = self._workspace_buttons[self._prev_workspace_index]
                    self._update_button(prev_workspace_button)
                except (IndexError, TypeError):
                    self._add_or_update_buttons()

                pending_token = self._pending_switch_token
                QTimer.singleShot(
                    self._pending_clear_delay_ms,
                    lambda token=pending_token: self.clear_pending_workspace(token),
                )

            if event["type"] == KomorebiEvent.FocusChange.value:
                self._remember_active_window()
            if self._workspace_app_icons_enabled:
                try:
                    if event["type"] in ["ToggleFloat"]:
                        self._workspace_buttons[self._curr_workspace_index].update_icons()
                    if active_workspace_changed:
                        self._workspace_buttons[self._prev_workspace_index].update_icons()
                        self._workspace_buttons[self._curr_workspace_index].update_icons()
                    for i in range(len(self._komorebi_workspaces)):
                        if self._prev_num_windows_in_workspaces[i] != self._curr_num_windows_in_workspaces[i]:
                            self._workspace_buttons[i].update_icons()
                        elif event["type"] in [KomorebiEvent.TitleUpdate.value]:
                            hwnd = event["content"][1]["hwnd"]
                            self._workspace_buttons[i].update_icon_by_hwnd(hwnd)
                except (IndexError, TypeError):
                    pass

            if event["type"] == KomorebiEvent.MoveWorkspaceToMonitorNumber.value:
                if event["content"] != self._komorebi_screen["index"]:
                    workspaces = self._komorebic.get_workspaces(self._komorebi_screen)
                    screen_workspace_indexes = list(map(lambda ws: ws["index"], workspaces))
                    button_workspace_indexes = list(map(lambda ws: ws.workspace_index, self._workspace_buttons))
                    unknown_indexes = set(button_workspace_indexes) - set(screen_workspace_indexes)
                    if len(unknown_indexes) >= 0:
                        for workspace_index in unknown_indexes:
                            self._try_remove_workspace_button(workspace_index)
                self._add_or_update_buttons()
            elif event["type"] in self._workspace_focus_events or active_workspace_changed:
                # send workspace_update event to active_window widgets
                self._event_service.emit_event("workspace_update", event["type"])
                try:
                    prev_workspace_button = self._workspace_buttons[self._prev_workspace_index]
                    self._update_button(prev_workspace_button)
                    if self._pending_workspace_index is None:
                        new_workspace_button = self._workspace_buttons[self._curr_workspace_index]
                        self._update_button(new_workspace_button)
                except (IndexError, TypeError):
                    self._add_or_update_buttons()
            elif event["type"] in self._update_buttons_event_watchlist:
                self._add_or_update_buttons()

            # Update workspace button if number of windows in workspace changes
            for i in range(len(self._komorebi_workspaces)):
                if (
                    self._prev_num_windows_in_workspaces[i] != self._curr_num_windows_in_workspaces[i]
                    and self._curr_num_windows_in_workspaces[i] == 0
                ):
                    self._update_button(self._workspace_buttons[i])

            # Remove workspace button if workspace is closed
            if event["type"] == KomorebiEvent.CloseWorkspace.value:
                workspaces = self._komorebic.get_workspaces(self._komorebi_screen)
                screen_workspace_indexes = list(map(lambda ws: ws["index"], workspaces))
                button_workspace_indexes = list(map(lambda ws: ws.workspace_index, self._workspace_buttons))
                unknown_indexes = set(button_workspace_indexes) - set(screen_workspace_indexes)
                if len(unknown_indexes) >= 0:
                    for workspace_index in unknown_indexes:
                        self._try_remove_workspace_button(workspace_index)
                    self._add_or_update_buttons()

            if event["type"] == KomorebiEvent.FocusChange.value:
                self._get_workspace_layer(self._curr_workspace_index)

            # Show float override label if float override is active
            if state.get("float_override") and self.config.label_float_override:
                self.float_override_label.show()
            else:
                self.float_override_label.hide()

        # send workspace_update event to active_window widgets
        if event["type"] in ["MoveWindow", "Show", "Hide", "Destroy"]:
            self._event_service.emit_event("workspace_update", event["type"])

    def _clear_container_layout(self):
        for i in reversed(range(self._workspace_container_layout.count())):
            old_workspace_widget = self._workspace_container_layout.itemAt(i).widget()
            self._workspace_container_layout.removeWidget(old_workspace_widget)
            old_workspace_widget.setParent(None)

    def _update_komorebi_state(self, komorebi_state: dict) -> bool:
        try:
            self._screen_hwnd = self.monitor_hwnd or get_monitor_hwnd(int(QWidget.winId(self)))
            self._komorebi_state = komorebi_state
            if self._komorebi_state:
                self._komorebi_screen = self._komorebic.get_screen_by_hwnd(self._komorebi_state, self._screen_hwnd)
                self._komorebi_workspaces = self._komorebic.get_workspaces(self._komorebi_screen)
                focused_workspace = self._get_focused_workspace()
                if focused_workspace:
                    self._prev_workspace_index = self._curr_workspace_index
                    self._curr_workspace_index = focused_workspace["index"]

                self._curr_num_windows_in_workspaces = self._curr_num_windows_in_workspaces[
                    : len(self._komorebi_workspaces)
                ] + [0] * (len(self._komorebi_workspaces) - len(self._curr_num_windows_in_workspaces))
                self._prev_num_windows_in_workspaces = self._curr_num_windows_in_workspaces.copy()
                for i in range(len(self._komorebi_workspaces)):
                    windows = self._get_all_windows_in_workspace(i)
                    self._curr_num_windows_in_workspaces[i] = len(windows) if windows else 0

                return True
        except TypeError:
            return False

    def _get_focused_workspace(self):
        return self._komorebic.get_focused_workspace(self._komorebi_screen)

    def _has_active_workspace_index_changed(self):
        return self._prev_workspace_index != self._curr_workspace_index

    def _remember_active_window(self) -> None:
        focused_workspace = self._get_focused_workspace()
        if not focused_workspace:
            return

        workspace_index = focused_workspace["index"]
        focused_window = self._get_current_workspace_focused_window(focused_workspace)
        if not focused_window:
            return

        hwnd = focused_window["hwnd"]
        self._workspace_last_active_hwnd[workspace_index] = hwnd
        app_key = self._get_app_key(hwnd)
        if app_key:
            self._workspace_app_last_active_hwnd[(workspace_index, app_key)] = hwnd

    def _get_current_workspace_focused_window(self, workspace: dict) -> dict | None:
        if workspace.get("layer") == "Floating":
            return self._komorebic.get_focused_floating_window(workspace)

        focused_container = self._komorebic.get_focused_container(workspace, get_monocle=True)
        if not focused_container:
            return None
        return self._komorebic.get_focused_window(focused_container)

    def _get_workspace_new_status(self, workspace) -> WorkspaceStatus:
        if self._curr_workspace_index == workspace["index"]:
            return WORKSPACE_STATUS_ACTIVE
        elif self._komorebic.get_num_windows(workspace) > 0:
            return WORKSPACE_STATUS_POPULATED
        else:
            return WORKSPACE_STATUS_EMPTY

    def _get_workspace_non_active_status(self, workspace_index: int) -> WorkspaceStatus:
        workspace = self._komorebic.get_workspace_by_index(self._komorebi_screen, workspace_index)
        if workspace and self._komorebic.get_num_windows(workspace) > 0:
            return WORKSPACE_STATUS_POPULATED
        return WORKSPACE_STATUS_EMPTY

    def _is_focused_monitor(self) -> bool:
        if not self._komorebi_state or not self._komorebi_screen:
            return False
        try:
            return self._komorebi_state["monitors"]["focused"] == self._komorebi_screen["index"]
        except (KeyError, TypeError):
            return False

    def _resolve_pending_workspace_index(self, target: int | str) -> int | None:
        if isinstance(target, int):
            return target
        if not isinstance(target, str) or self._curr_workspace_index is None:
            return None

        try:
            target_kind, direction = target.split(":", 1)
        except ValueError:
            return None
        if direction not in ["previous", "next"]:
            return None

        if target_kind == "cycle":
            return self._resolve_cycle_workspace_index(direction)
        if target_kind == "cycle-empty":
            return self._resolve_cycle_empty_workspace_index(direction)
        return None

    def _resolve_cycle_workspace_index(self, direction: str) -> int | None:
        workspace_count = len(self._komorebi_workspaces)
        if workspace_count <= 0 or self._curr_workspace_index is None:
            return None

        step = -1 if direction == "previous" else 1
        return (self._curr_workspace_index + step + workspace_count) % workspace_count

    def _resolve_cycle_empty_workspace_index(self, direction: str) -> int | None:
        workspace_count = len(self._komorebi_workspaces)
        if workspace_count <= 0 or self._curr_workspace_index is None:
            return None

        step = -1 if direction == "previous" else 1
        for offset in range(1, workspace_count + 1):
            candidate_index = (self._curr_workspace_index + step * offset + workspace_count) % workspace_count
            workspace = self._komorebic.get_workspace_by_index(self._komorebi_screen, candidate_index)
            if workspace and not self._komorebic.get_num_windows(workspace):
                return candidate_index
        return self._curr_workspace_index

    def set_pending_workspace(self, workspace_index: int) -> None:
        if self._komorebi_screen is None or self._curr_workspace_index is None:
            return
        if workspace_index == self._curr_workspace_index:
            return
        if workspace_index < 0 or workspace_index >= len(self._workspace_buttons):
            return

        self._pending_workspace_index = workspace_index
        self._pending_switch_token += 1
        self._redraw_pending_workspace_buttons()
        QApplication.processEvents()

        token = self._pending_switch_token
        QTimer.singleShot(2000, lambda: self.clear_pending_workspace(token))

    def clear_pending_workspace(self, token: int | None = None) -> None:
        if token is not None and token != self._pending_switch_token:
            return
        if self._pending_workspace_index is None:
            return

        pending_workspace_index = self._pending_workspace_index
        self._pending_workspace_index = None

        for workspace_index in {pending_workspace_index, self._curr_workspace_index}:
            if workspace_index is None:
                continue
            try:
                self._update_button(self._workspace_buttons[workspace_index])
            except (IndexError, TypeError):
                pass

    def _redraw_pending_workspace_buttons(self) -> None:
        pending_workspace_index = self._pending_workspace_index
        if pending_workspace_index is None:
            return

        for workspace_index in {self._curr_workspace_index, pending_workspace_index}:
            if workspace_index is None:
                continue
            try:
                button = self._workspace_buttons[workspace_index]
            except IndexError:
                continue

            _set_workspace_button_class(
                button,
                self._get_workspace_non_active_status(workspace_index),
                pending=workspace_index == pending_workspace_index,
            )
            refresh_widget_style(button)

    def request_workspace_switch(self, workspace_index: int) -> None:
        if self._komorebi_screen is None:
            return

        self.set_pending_workspace(workspace_index)
        QTimer.singleShot(
            0,
            lambda: self._activate_workspace_after_pending(workspace_index),
        )

    def _activate_workspace_after_pending(self, workspace_index: int) -> None:
        if self._komorebi_screen is None:
            return
        try:
            self._komorebic.activate_workspace(self._komorebi_screen["index"], workspace_index)
        except Exception:
            self.clear_pending_workspace()
            logging.exception("Failed to focus workspace at index %s", workspace_index)

    def _get_workspace_layer(self, workspace_index: int) -> None:
        """
        This function is used to get the workspace layer by index. (toggle-workspace-layer)
        Also updates the label's CSS class based on current layer.
        """
        if self.config.toggle_workspace_layer.enabled:
            workspace = self._komorebic.get_workspace_by_index(self._komorebi_screen, workspace_index)
            if workspace and "layer" in workspace:
                # Set base class plus layer-specific class
                layer_type = workspace["layer"].lower()  # Either "tiling" or "floating"
                self.workspace_layer_label.setProperty("class", f"workspace-layer {layer_type}")

                # Set appropriate label text
                if workspace["layer"] == "Tiling":
                    self.workspace_layer_label.setText(self.config.toggle_workspace_layer.tiling_label)
                elif workspace["layer"] == "Floating":
                    self.workspace_layer_label.setText(self.config.toggle_workspace_layer.floating_label)
                refresh_widget_style(self.workspace_layer_label)
            else:
                self.workspace_layer_label.setProperty("class", "workspace-layer")
                self.workspace_layer_label.setText("")
                refresh_widget_style(self.workspace_layer_label)

    def _update_button(self, workspace_btn: WorkspaceButton) -> None:
        self._refresh_button_labels(workspace_btn)
        workspace_index = workspace_btn.workspace_index
        workspace = self._komorebic.get_workspace_by_index(self._komorebi_screen, workspace_index)
        workspace_status = self._get_workspace_new_status(workspace)
        if self.config.hide_empty_workspaces and workspace_status == WORKSPACE_STATUS_EMPTY:
            workspace_btn.hide()
        else:
            current_classes = str(workspace_btn.property("class") or "").split()
            if (
                workspace_btn.status != workspace_status
                or "pending" in current_classes
                or workspace_status.lower() not in current_classes
            ):
                workspace_btn.update_and_redraw(workspace_status)
            workspace_btn.show()
            workspace_btn.update_visible_buttons()
        self._get_workspace_layer(workspace_index)

    def _refresh_button_labels(self, workspace_btn: WorkspaceButton) -> None:
        # Workspace names can change dynamically (e.g. via `komorebic workspace-name`).
        # Refresh cached button labels so the UI reflects the latest state.
        try:
            default_label, active_label, populated_label = self._get_workspace_label(workspace_btn.workspace_index)
        except Exception:
            return

        if (
            getattr(workspace_btn, "default_label", None) == default_label
            and getattr(workspace_btn, "active_label", None) == active_label
            and getattr(workspace_btn, "populated_label", None) == populated_label
        ):
            return

        workspace_btn.default_label = default_label
        workspace_btn.active_label = active_label
        workspace_btn.populated_label = populated_label
        workspace_btn.update_and_redraw(workspace_btn.status)

    def _add_or_update_buttons(self) -> None:
        buttons_added = False
        for workspace_index, _ in enumerate(self._komorebi_workspaces):
            try:
                button = self._workspace_buttons[workspace_index]
                self._update_button(button)
            except IndexError:
                button = self._try_add_workspace_button(workspace_index)
                buttons_added = True

        if buttons_added:
            self._workspace_buttons.sort(key=lambda btn: btn.workspace_index)
            for i, workspace_btn in enumerate(self._workspace_buttons):
                if self._workspace_container_layout.indexOf(workspace_btn) != i:
                    self._workspace_container_layout.insertWidget(i, workspace_btn)
                self._update_button(workspace_btn)

    def _get_workspace_label(self, workspace_index):
        workspace = self._komorebic.get_workspace_by_index(self._komorebi_screen, workspace_index)
        monitor_index = self._komorebi_screen["index"]
        ws_index = workspace_index if self.config.label_zero_index else workspace_index + 1
        ws_monitor_index = monitor_index if self.config.label_zero_index else monitor_index + 1
        ws_raw_name = None
        try:
            ws_raw_name = workspace.get("name") if isinstance(workspace, dict) else None
        except Exception:
            ws_raw_name = None
        try:
            ws_name = ws_raw_name or self.config.label_default_name.format(
                index=ws_index, monitor_index=ws_monitor_index
            )
        except Exception:
            ws_name = str(ws_index)

        default_label = self.config.label_workspace_btn.format(
            name=ws_name, index=ws_index, monitor_index=ws_monitor_index
        )
        active_label = self.config.label_workspace_active_btn.format(
            name=ws_name, index=ws_index, monitor_index=ws_monitor_index
        )
        populated_label = self.config.label_workspace_populated_btn.format(
            name=ws_name, index=ws_index, monitor_index=ws_monitor_index
        )
        return default_label, active_label, populated_label

    def _try_add_workspace_button(self, workspace_index: int) -> WorkspaceButton:
        workspace_button_indexes = [ws_btn.workspace_index for ws_btn in self._workspace_buttons]
        if workspace_index not in workspace_button_indexes:
            default_label, active_label, populated_label = self._get_workspace_label(workspace_index)
            if self._workspace_app_icons_enabled:
                workspace_btn = WorkspaceButtonWithIcons(
                    workspace_index, self, self.config, default_label, active_label, populated_label
                )
            else:
                workspace_btn = WorkspaceButton(
                    workspace_index, self, self.config, default_label, active_label, populated_label
                )
            self._workspace_buttons.append(workspace_btn)
            return workspace_btn

    def _try_remove_workspace_button(self, workspace_index: int) -> None:
        with suppress(IndexError):
            workspace_button = self._workspace_buttons[workspace_index]
            workspace_button.hide()

    def _show_offline_status(self):
        self._offline_text.show()
        self._workspace_container.hide()
        if self.config.toggle_workspace_layer.enabled:
            self.workspace_layer_label.hide()

    def _hide_offline_status(self):
        self._offline_text.hide()
        self._workspace_container.show()
        if self.config.toggle_workspace_layer.enabled:
            self.workspace_layer_label.show()

    def wheelEvent(self, event):
        """Handle mouse wheel events to switch workspaces."""
        if not self.config.enable_scroll_switching or not self._komorebi_screen:
            return

        delta = event.angleDelta().y()
        # Determine direction (consider reverse_scroll_direction setting)
        direction = -1 if (delta > 0) != self.config.reverse_scroll_direction else 1

        workspaces = self._komorebic.get_workspaces(self._komorebi_screen)
        if not workspaces:
            return

        current_idx = self._curr_workspace_index
        num_workspaces = len(workspaces)
        next_idx = (current_idx + direction) % num_workspaces
        try:
            self.set_pending_workspace(next_idx)
            self._komorebic.activate_workspace(self._komorebi_screen["index"], next_idx)
        except Exception:
            self.clear_pending_workspace()
            logging.exception("Failed to switch to workspace at index %s", next_idx)

    def _get_all_windows_in_workspace(self, workspace_index: int) -> list[dict] | None:
        workspace = self._komorebi_workspaces[workspace_index]
        containers = self._komorebic.get_containers(workspace, get_monocle=True)
        windows_in_workspace = []
        for container in containers:
            windows = self._komorebic.get_windows(container)
            windows_in_workspace.extend(windows)
        floating_windows = [container for container in workspace["floating_windows"]["elements"]]
        if not self.config.app_icons.hide_floating:
            windows_in_workspace.extend(floating_windows)
        return windows_in_workspace

    def _get_all_icons_in_workspace(self, workspace_index: int) -> list[dict] | None:
        windows_in_workspace = self._get_all_windows_in_workspace(workspace_index)
        self._unique_app_keys = set()
        icon_entries = []
        for index, window in enumerate(windows_in_workspace):
            hwnd = window["hwnd"]
            pixmap = self._get_app_icon(hwnd, workspace_index)
            if pixmap is None:
                continue
            icon_entries.append(
                {
                    "hwnd": hwnd,
                    "app_key": self._get_app_key(hwnd),
                    "pixmap": pixmap,
                    "class_name": f"icon icon-{index + 1}",
                }
            )
        return icon_entries

    def _get_app_icon(self, hwnd: int, workspace_index: int, ignore_cache: bool = False) -> QPixmap | None:
        try:
            if self.config.app_icons.hide_duplicates:
                app_key = self._get_app_key(hwnd)
                if app_key and app_key not in self._unique_app_keys:
                    self._unique_app_keys.add(app_key)
                elif app_key:
                    return None
                else:
                    process = get_process_info(hwnd)
                    pid = process["pid"]
                    if pid not in self._unique_app_keys:
                        self._unique_app_keys.add(pid)
                    else:
                        return None

            self.dpi = self.screen().devicePixelRatio()
            cache_key = (hwnd, self.dpi)

            if cache_key in self._icon_cache and not ignore_cache:
                icon_img = self._icon_cache[cache_key]
            else:
                icon_img = get_window_icon(hwnd)

            if icon_img:
                icon_img = icon_img.resize(
                    (
                        int(self.config.app_icons.size * self.dpi),
                        int(self.config.app_icons.size * self.dpi),
                    ),
                    Image.LANCZOS,
                ).convert("RGBA")
                self._icon_cache[cache_key] = icon_img
                qimage = QImage(icon_img.tobytes(), icon_img.width, icon_img.height, QImage.Format.Format_RGBA8888)
                pixmap = QPixmap.fromImage(qimage)
                pixmap.setDevicePixelRatio(self.dpi)
                return pixmap
            else:
                return None
        except Exception:
            logging.debug("Failed to get icons for window with HWND %s", hwnd, exc_info=True)
            return None

    def _get_app_key(self, hwnd: int) -> str | None:
        try:
            process = get_process_info(hwnd)
            process_path = process.get("path")
            if process_path:
                return f"path:{process_path.lower()}"
            process_name = process.get("name")
            if process_name:
                return f"name:{process_name.lower()}"
            pid = process.get("pid")
            if pid:
                return f"pid:{pid}"
        except Exception:
            pass
        return None

    def _workspace_contains_hwnd(self, workspace_index: int, hwnd: int) -> bool:
        if not hwnd:
            return False
        return any(window["hwnd"] == hwnd for window in self._get_all_windows_in_workspace(workspace_index))

    def _resolve_workspace_target_hwnd(self, workspace_index: int, target_hwnd: int | None, app_key: str | None) -> int | None:
        if target_hwnd and self._workspace_contains_hwnd(workspace_index, target_hwnd):
            return target_hwnd

        if app_key:
            cached_hwnd = self._workspace_app_last_active_hwnd.get((workspace_index, app_key))
            if cached_hwnd and self._workspace_contains_hwnd(workspace_index, cached_hwnd):
                return cached_hwnd

            for window in self._get_all_windows_in_workspace(workspace_index):
                hwnd = window["hwnd"]
                if self._get_app_key(hwnd) == app_key:
                    return hwnd

        cached_workspace_hwnd = self._workspace_last_active_hwnd.get(workspace_index)
        if cached_workspace_hwnd and self._workspace_contains_hwnd(workspace_index, cached_workspace_hwnd):
            return cached_workspace_hwnd

        windows_in_workspace = self._get_all_windows_in_workspace(workspace_index)
        if windows_in_workspace:
            return windows_in_workspace[0]["hwnd"]
        return None

    def _focus_hwnd(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        try:
            restore_window(hwnd)
        except Exception:
            pass
        try:
            show_window(hwnd)
            set_foreground(hwnd)
            return True
        except Exception:
            logging.exception("Failed to focus window with HWND %s", hwnd)
            return False

    def focus_workspace_window(self, workspace_index: int, target_hwnd: int | None, app_key: str | None = None) -> None:
        try:
            if not self._komorebi_screen:
                return

            if self._curr_workspace_index != workspace_index:
                self.set_pending_workspace(workspace_index)
                self._komorebic.activate_workspace(self._komorebi_screen["index"], workspace_index, wait=True)

            resolved_hwnd = self._resolve_workspace_target_hwnd(workspace_index, target_hwnd, app_key)
            if not resolved_hwnd:
                if self._curr_workspace_index != workspace_index:
                    self.set_pending_workspace(workspace_index)
                    self._komorebic.activate_workspace(self._komorebi_screen["index"], workspace_index)
                return

            self._focus_hwnd(resolved_hwnd)
        except Exception:
            self.clear_pending_workspace()
            logging.exception(
                "Failed to focus workspace window for workspace %s and HWND %s",
                workspace_index,
                target_hwnd,
            )
