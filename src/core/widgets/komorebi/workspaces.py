import logging
from contextlib import suppress
from typing import Literal

from PIL import Image
from PyQt6.QtCore import QPoint, QRect, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from core.events.komorebi import KomorebiEvent
from core.events.service import EventService
from core.utils.utilities import refresh_widget_style
from core.utils.win32.app_icons import get_window_icon
from core.utils.win32.utils import get_foreground_hwnd, get_monitor_hwnd, get_process_info
from core.utils.win32.window_actions import move_cursor_to_window_center, restore_window, set_foreground, show_window
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
APP_ICON_DISPLAY_MODE_ROW = "row"
APP_ICON_DISPLAY_MODE_LAYOUT_PREVIEW = "layout_preview"


def _log_workspace_diag(message: str, *args) -> None:
    logging.info("[komorebi-workspaces] " + message, *args)


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
            screen_index = (
                self.parent_widget._komorebi_screen.get("index") if self.parent_widget._komorebi_screen else None
            )
            _log_workspace_diag(
                "workspace button click: monitor=%s current_ws=%s target_ws=%s",
                screen_index,
                self.parent_widget._curr_workspace_index,
                self.workspace_index,
            )
            self.parent_widget.set_pending_workspace(self.workspace_index)
            self.komorebic.activate_workspace(self.parent_widget._komorebi_screen["index"], self.workspace_index)
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
        self.button_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self.text_label = QLabel(self.default_label)
        self.text_label.setProperty("class", "label")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.button_layout.addWidget(self.text_label)

        self.preview_widget = WorkspaceLayoutPreview(workspace_index, self, parent_widget)
        self.button_layout.addWidget(self.preview_widget)

        self.icons = []
        self.icon_labels = []
        self.hide()
        self.update_icons()
        self.update_and_redraw(self.status)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.preview_widget.isVisible() and self.preview_widget.geometry().contains(event.pos()):
                self.activate_workspace()
                event.accept()
                return
            icon_label = self._icon_label_at_position(event.pos())
            if icon_label:
                self.parent_widget.focus_workspace_window(self.workspace_index, icon_label.target_hwnd, icon_label.app_key)
                event.accept()
                return
            self.activate_workspace()
            event.accept()
            return
        super().mousePressEvent(event)

    def _icon_label_at_position(self, position) -> "WorkspaceAppIconLabel | None":
        # Give icon clicks a small hit slop so padding/gaps do not fall through to workspace activation.
        padding = max(4, int(self.config.app_icons.size / 3))
        for icon_label in self.icon_labels:
            if icon_label.geometry().adjusted(-padding, -padding, padding, padding).contains(position):
                return icon_label
        return None

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
        if self.preview_widget.isVisible():
            self.preview_widget.refresh_preview_styles()

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

        use_preview = self._should_use_layout_preview(icons_list)
        if use_preview and self.preview_widget.update_preview(icons_list):
            self._hide_row_icons()
            if self.config.app_icons.hide_label and icons_list:
                self.text_label.hide()
            else:
                self.text_label.show()
            return

        self.preview_widget.clear_preview()
        self._show_row_icons(icons_list)

        if self.config.app_icons.hide_label and len(self.icon_labels) > 0:
            self.text_label.hide()
        else:
            self.text_label.show()

    def update_icon_by_hwnd(self, hwnd: int):
        if any(icon_entry["hwnd"] == hwnd for icon_entry in self.icons):
            pixmap = self.parent_widget._get_app_icon(hwnd, self.workspace_index, ignore_cache=True)
            if pixmap:
                self.update_icons(icons={hwnd: pixmap})

    def _hide_row_icons(self) -> None:
        for icon_label in self.icon_labels:
            icon_label.hide()

    def _show_row_icons(self, icons_list: list[dict]) -> None:
        for extra_label in self.icon_labels[len(icons_list) :]:
            self.button_layout.removeWidget(extra_label)
            extra_label.setParent(None)
        self.icon_labels = self.icon_labels[: len(icons_list)]

        for index, icon_entry in enumerate(icons_list):
            if index < len(self.icon_labels):
                self.icon_labels[index].update_icon(icon_entry)
                self.icon_labels[index].show()
            else:
                icon_label = WorkspaceAppIconLabel(self.workspace_index, self.parent_widget)
                icon_label.update_icon(icon_entry)
                self.button_layout.addWidget(icon_label)
                self.icon_labels.append(icon_label)

    def _should_use_layout_preview(self, icons_list: list[dict]) -> bool:
        if self.config.app_icons.display_mode != APP_ICON_DISPLAY_MODE_LAYOUT_PREVIEW:
            return False
        if self.config.app_icons.hide_duplicates or not self.config.app_icons.hide_floating:
            return False
        if not icons_list:
            return False
        if self.config.app_icons.max_icons > 0 and len(icons_list) > self.config.app_icons.max_icons:
            return False
        workspace = self.parent_widget._komorebic.get_workspace_by_index(
            self.parent_widget._komorebi_screen,
            self.workspace_index,
        )
        if not workspace or workspace.get("layer") != "Tiling":
            return False
        return all(icon_entry.get("window_rect") for icon_entry in icons_list)

    def activate_workspace(self):
        try:
            screen_index = (
                self.parent_widget._komorebi_screen.get("index") if self.parent_widget._komorebi_screen else None
            )
            _log_workspace_diag(
                "workspace frame click: monitor=%s current_ws=%s target_ws=%s",
                screen_index,
                self.parent_widget._curr_workspace_index,
                self.workspace_index,
            )
            self.parent_widget.set_pending_workspace(self.workspace_index)
            self.komorebic.activate_workspace(self.parent_widget._komorebi_screen["index"], self.workspace_index)
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


class WorkspacePreviewTile(QFrame):
    def __init__(self, workspace_index: int, parent_widget: "WorkspaceWidget", owner: "WorkspaceLayoutPreview"):
        super().__init__(owner)
        self.workspace_index = workspace_index
        self.parent_widget = parent_widget
        self.owner = owner
        self.target_hwnd = None
        self.app_key = None
        self.icon_label = QLabel(self)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._icon_size = QSize()
        self.setProperty("class", "layout-preview-tile")

    def update_entry(self, icon_entry: dict, tile_class: str) -> None:
        self.target_hwnd = icon_entry["hwnd"]
        self.app_key = icon_entry["app_key"]
        self.setProperty("class", tile_class)
        pixmap = icon_entry["pixmap"]
        if pixmap is not None:
            self.icon_label.setProperty("class", "layout-preview-icon")
            self.icon_label.setPixmap(pixmap)
            try:
                di_size = pixmap.deviceIndependentSize().toSize()
                self._icon_size = QSize(max(1, di_size.width()), max(1, di_size.height()))
            except Exception:
                dpr = pixmap.devicePixelRatio() or 1.0
                self._icon_size = QSize(
                    max(1, int(round(pixmap.width() / dpr))),
                    max(1, int(round(pixmap.height() / dpr))),
                )
            self.icon_label.show()
            refresh_widget_style(self.icon_label)
        else:
            self.icon_label.clear()
            self._icon_size = QSize()
            self.icon_label.hide()
        self._reposition_icon()
        refresh_widget_style(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_icon()

    def _reposition_icon(self) -> None:
        if self._icon_size.isEmpty():
            self.icon_label.hide()
            return
        width = min(self.width(), self._icon_size.width())
        height = min(self.height(), self._icon_size.height())
        x = max(0, int((self.width() - width) / 2))
        y = max(0, int((self.height() - height) / 2))
        self.icon_label.setGeometry(x, y, width, height)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.owner.handle_tile_click(self.target_hwnd, self.app_key)
            event.accept()
            return
        super().mousePressEvent(event)


class WorkspaceLayoutPreview(QFrame):
    def __init__(self, workspace_index: int, parent_button: "WorkspaceButtonWithIcons", parent_widget: "WorkspaceWidget"):
        super().__init__(parent_button)
        self.workspace_index = workspace_index
        self.parent_button = parent_button
        self.parent_widget = parent_widget
        self._tiles: list[WorkspacePreviewTile] = []
        self._entries: list[dict] = []
        self._active = False
        self._preview_failed = False
        self._current_canvas_size = QSize()
        self._overlay = QFrame()
        self._overlay.setProperty("class", "layout-preview")
        self._overlay.setWindowFlag(Qt.WindowType.Tool)
        self._overlay.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self._overlay.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self._overlay.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint)
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setProperty("class", "layout-preview-anchor")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.hide()

    def sizeHint(self) -> QSize:
        if not self._current_canvas_size.isEmpty():
            return QSize(self._current_canvas_size.width(), 1)
        cfg = self.parent_widget.config.app_icons
        height = max(cfg.size + 8, cfg.preview_height)
        width = max(int(height * max(1.2, cfg.preview_aspect_ratio)), cfg.size * 2)
        return QSize(width, 1)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.parent_button.activate_workspace()
            event.accept()
            return
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_overlay_geometry()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_overlay_geometry()

    def showEvent(self, event):
        super().showEvent(event)
        if self._entries:
            self._overlay.show()
            self._sync_overlay_geometry()

    def hideEvent(self, event):
        self._overlay.hide()
        super().hideEvent(event)

    def handle_tile_click(self, target_hwnd: int | None, app_key: str | None) -> None:
        action = self.parent_widget.config.app_icons.click_action
        if action == "activate_workspace":
            self.parent_button.activate_workspace()
            return
        self.parent_widget.focus_workspace_window(self.workspace_index, target_hwnd, app_key)

    def clear_preview(self) -> None:
        self._entries = []
        self._preview_failed = False
        self._current_canvas_size = QSize()
        self.setMinimumSize(0, 0)
        self.setFixedSize(0, 1)
        for tile in self._tiles:
            tile.hide()
        self._overlay.hide()
        self.hide()

    def refresh_preview_styles(self) -> None:
        refresh_widget_style(self._overlay)
        for tile in self._tiles:
            if tile.isVisible():
                refresh_widget_style(tile, tile.icon_label)

    def update_preview(self, icon_entries: list[dict]) -> bool:
        self._entries = list(icon_entries)
        self._preview_failed = False
        if not self._entries:
            self.clear_preview()
            return False
        if not self._apply_layout():
            self.clear_preview()
            self._preview_failed = True
            return False
        self._overlay.show()
        self._sync_overlay_geometry()
        self.show()
        return True

    def _apply_layout(self) -> bool:
        bounds = self._compute_bounds(self._entries)
        if not bounds:
            return False

        cfg = self.parent_widget.config.app_icons
        padding = max(0, cfg.preview_padding)
        icon_footprint = max(1, cfg.size + 2 * padding)
        left, top, right, bottom = bounds
        source_width = max(1, right - left)
        source_height = max(1, bottom - top)
        canvas_size = self._compute_canvas_size(bounds)
        if canvas_size.width() <= 0 or canvas_size.height() <= 0:
            return False

        self._current_canvas_size = canvas_size
        self.setFixedSize(canvas_size.width(), 1)
        self.setMinimumSize(canvas_size.width(), 1)
        self._overlay.setFixedSize(canvas_size)
        canvas = QRect(0, 0, canvas_size.width(), canvas_size.height())

        normalized_rects: list[QRect] = []
        for icon_entry in self._entries:
            rect = self._rect_to_geometry(icon_entry.get("window_rect"))
            if not rect:
                return False

            rel_left = (rect[0] - left) / source_width
            rel_top = (rect[1] - top) / source_height
            rel_width = rect[2] / source_width
            rel_height = rect[3] / source_height

            tile_left = canvas.x() + int(round(rel_left * canvas.width()))
            tile_top = canvas.y() + int(round(rel_top * canvas.height()))
            cell_width = max(1, int(round(rel_width * canvas.width())))
            cell_height = max(1, int(round(rel_height * canvas.height())))

            tile_width = min(cell_width, icon_footprint)
            tile_height = min(cell_height, icon_footprint)
            tile_rect = QRect(
                tile_left + max(0, int((cell_width - tile_width) / 2)),
                tile_top + max(0, int((cell_height - tile_height) / 2)),
                tile_width,
                tile_height,
            )
            if tile_rect.width() <= 0 or tile_rect.height() <= 0:
                return False
            normalized_rects.append(tile_rect)

        while len(self._tiles) < len(self._entries):
            self._tiles.append(WorkspacePreviewTile(self.workspace_index, self.parent_widget, self))
            self._tiles[-1].setParent(self._overlay)

        for extra_tile in self._tiles[len(self._entries) :]:
            extra_tile.hide()

        for index, icon_entry in enumerate(self._entries):
            tile = self._tiles[index]
            tile_rect = normalized_rects[index]
            tile.setGeometry(tile_rect)
            tile_class = "layout-preview-tile"
            if icon_entry.get("focused") and cfg.preview_show_focus:
                tile_class += " focused"
            tile.update_entry(icon_entry, tile_class)
            tile.show()

        self.setProperty("class", "layout-preview")
        refresh_widget_style(self)
        return True

    def _compute_canvas_size(self, bounds: tuple[int, int, int, int]) -> QSize:
        cfg = self.parent_widget.config.app_icons
        padding = max(0, cfg.preview_padding)
        left, top, right, bottom = bounds
        source_width = max(1, right - left)
        source_height = max(1, bottom - top)

        min_source_dimension = None
        for icon_entry in self._entries:
            rect = self._rect_to_geometry(icon_entry.get("window_rect"))
            if not rect:
                continue
            rect_min = max(1, min(rect[2], rect[3]))
            if min_source_dimension is None or rect_min < min_source_dimension:
                min_source_dimension = rect_min

        if not min_source_dimension:
            return QSize()

        icon_footprint = max(1, cfg.size + 2 * padding)
        scale = icon_footprint / float(min_source_dimension)
        width = max(icon_footprint, int(round(source_width * scale)))
        height = max(icon_footprint, int(round(source_height * scale)))
        return QSize(width, height)

    def _sync_overlay_geometry(self) -> None:
        if self._current_canvas_size.isEmpty() or not self.isVisible():
            return
        center_global = self.mapToGlobal(QPoint(max(0, self.width() // 2), max(0, self.height() // 2)))
        x = center_global.x() - int(self._current_canvas_size.width() / 2)
        y = center_global.y() - int(self._current_canvas_size.height() / 2)
        self._overlay.setGeometry(x, y, self._current_canvas_size.width(), self._current_canvas_size.height())

    def _compute_bounds(self, icon_entries: list[dict]) -> tuple[int, int, int, int] | None:
        bounds = [self._rect_to_geometry(icon_entry.get("window_rect")) for icon_entry in icon_entries]
        valid_bounds = [rect for rect in bounds if rect]
        if not valid_bounds:
            return None
        left = min(rect[0] for rect in valid_bounds)
        top = min(rect[1] for rect in valid_bounds)
        right = max(rect[0] + rect[2] for rect in valid_bounds)
        bottom = max(rect[1] + rect[3] for rect in valid_bounds)
        return left, top, right, bottom

    @staticmethod
    def _rect_to_geometry(rect: dict | None) -> tuple[int, int, int, int] | None:
        if not isinstance(rect, dict):
            return None
        try:
            left = int(rect.get("left", 0))
            top = int(rect.get("top", 0))
            width = int(rect.get("right", 0))
            height = int(rect.get("bottom", 0))
        except Exception:
            return None
        if width <= 0 or height <= 0:
            return None
        return left, top, width, height


class WorkspaceWidget(BaseWidget):
    k_signal_connect = pyqtSignal(dict)
    k_signal_update = pyqtSignal(dict, dict)
    k_signal_disconnect = pyqtSignal()
    k_signal_workspace_pending = pyqtSignal(object, object)
    validation_schema = KomorebiWorkspacesConfig
    event_listener = KomorebiEventListener
    _pending_clear_delay_ms = 120
    _focus_diag_sample_delays_ms = (0, 50, 150, 300)
    _title_update_icon_refresh_delay_ms = 75
    def __init__(self, config: KomorebiWorkspacesConfig):
        super().__init__(class_name="komorebi-workspaces")
        self.config = config
        self._event_service = EventService()
        self._komorebic = KomorebiClient()

        self._workspace_app_icons_enabled = (
            self.config.app_icons.enabled_populated or self.config.app_icons.enabled_active
        )
        self._komorebi_screen = None
        self._komorebi_state = None
        self._komorebi_workspaces = []
        self._prev_workspace_index = None
        self._curr_workspace_index = None
        self._pending_workspace_indexes: set[int] = set()
        self._pending_workspace_tokens: dict[int, int] = {}
        self._pending_switch_token = 0
        self._prev_num_windows_in_workspaces = []
        self._curr_num_windows_in_workspaces = []
        self._workspace_last_active_hwnd = {}
        self._workspace_app_last_active_hwnd = {}
        self._pending_cursor_hwnd = None
        self._pending_cursor_workspace_index = None
        self._pending_focus_hwnd = None
        self._pending_focus_workspace_index = None
        self._pending_focus_token = None
        self._icon_focus_suspended_mouse_follows_focus = False
        self._icon_focus_request_id = 0
        self._active_icon_focus_request_id = None
        self._active_icon_focus_hwnd = None
        self._active_icon_focus_workspace_index = None
        self._active_icon_focus_reason = None
        self._pending_title_update_icon_hwnds: dict[int, set[int]] = {}
        self._title_update_icon_flush_token = 0
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
        self._workspace_icon_refresh_events = {
            KomorebiEvent.ChangeLayout.value,
            KomorebiEvent.ToggleTiling.value,
            KomorebiEvent.ToggleMonocle.value,
            KomorebiEvent.ToggleMaximize.value,
            KomorebiEvent.StackWindow.value,
            KomorebiEvent.UnstackWindow.value,
            KomorebiEvent.CycleStack.value,
            KomorebiEvent.FocusStackWindow.value,
        }
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
        self._pending_workspace_indexes = set()
        self._pending_workspace_tokens = {}
        self._pending_switch_token += 1
        self._workspace_last_active_hwnd = {}
        self._workspace_app_last_active_hwnd = {}
        self._pending_cursor_hwnd = None
        self._pending_cursor_workspace_index = None
        self._pending_focus_hwnd = None
        self._pending_focus_workspace_index = None
        self._pending_focus_token = None
        self._icon_focus_suspended_mouse_follows_focus = False
        self._active_icon_focus_request_id = None
        self._active_icon_focus_hwnd = None
        self._active_icon_focus_workspace_index = None
        self._active_icon_focus_reason = None
        self._pending_title_update_icon_hwnds = {}
        self._title_update_icon_flush_token += 1
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
        _log_workspace_diag(
            "external pending request: target=%s monitor_arg=%s widget_monitor=%s focused_monitor=%s",
            target,
            monitor_index,
            self._komorebi_screen.get("index") if self._komorebi_screen else None,
            self._komorebi_state.get("monitors", {}).get("focused") if getattr(self, "_komorebi_state", None) else None,
        )
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
            event_type = event.get("type")
            pending_workspace_confirmed = (
                self._curr_workspace_index is not None
                and active_workspace_changed
                and self._curr_workspace_index in self._pending_workspace_indexes
            )
            if (
                event_type == KomorebiEvent.FocusChange.value
                or active_workspace_changed
                or self._pending_workspace_indexes
            ):
                _log_workspace_diag(
                    "komorebi event: type=%s monitor=%s prev_ws=%s curr_ws=%s active_changed=%s pending_ws=%s "
                    "token=%s focused_hwnd=%s",
                    event_type,
                    self._komorebi_screen.get("index") if self._komorebi_screen else None,
                    self._prev_workspace_index,
                    self._curr_workspace_index,
                    active_workspace_changed,
                    sorted(self._pending_workspace_indexes),
                    self._pending_switch_token,
                    self._get_current_focused_hwnd_for_log(),
                )
            if (
                self._pending_workspace_indexes
                and active_workspace_changed
                and not pending_workspace_confirmed
            ):
                _log_workspace_diag(
                    "workspace switch did not match pending target yet: prev_ws=%s curr_ws=%s pending_ws=%s token=%s",
                    self._prev_workspace_index,
                    self._curr_workspace_index,
                    sorted(self._pending_workspace_indexes),
                    self._pending_switch_token,
                )

            if pending_workspace_confirmed:
                confirmed_workspace_index = self._curr_workspace_index
                try:
                    prev_workspace_button = self._workspace_buttons[self._prev_workspace_index]
                    self._update_button(prev_workspace_button)
                except (IndexError, TypeError):
                    self._add_or_update_buttons()

                self._focus_pending_workspace_window_if_ready()

                pending_token = self._pending_workspace_tokens.get(confirmed_workspace_index)
                _log_workspace_diag(
                    "workspace switch confirmed by event: prev_ws=%s curr_ws=%s pending_ws=%s "
                    "clear_delay_ms=%s token=%s",
                    self._prev_workspace_index,
                    self._curr_workspace_index,
                    confirmed_workspace_index,
                    self._pending_clear_delay_ms,
                    pending_token,
                )
                if pending_token is not None:
                    QTimer.singleShot(
                        self._pending_clear_delay_ms,
                        lambda workspace_index=confirmed_workspace_index, token=pending_token: self.clear_pending_workspace(
                            workspace_index=workspace_index,
                            token=token,
                        ),
                    )

            if event["type"] == KomorebiEvent.FocusChange.value:
                self._remember_active_window()
                self._log_focus_diag("focuschange-event", self._pending_cursor_hwnd, self._pending_cursor_workspace_index)
                QTimer.singleShot(16, self._finalize_pending_cursor_move)
            if self._workspace_app_icons_enabled:
                try:
                    if event["type"] in ["ToggleFloat"]:
                        self._workspace_buttons[self._curr_workspace_index].update_icons()
                    if event["type"] == KomorebiEvent.FocusChange.value and self._curr_workspace_index is not None:
                        self._workspace_buttons[self._curr_workspace_index].update_icons()
                    if event["type"] in self._workspace_icon_refresh_events:
                        target_indexes = range(len(self._komorebi_workspaces))
                        for i in target_indexes:
                            self._workspace_buttons[i].update_icons()
                        QTimer.singleShot(90, self._refresh_all_workspace_icons)
                    if active_workspace_changed:
                        self._workspace_buttons[self._prev_workspace_index].update_icons()
                        self._workspace_buttons[self._curr_workspace_index].update_icons()
                    for i in range(len(self._komorebi_workspaces)):
                        if self._prev_num_windows_in_workspaces[i] != self._curr_num_windows_in_workspaces[i]:
                            self._workspace_buttons[i].update_icons()
                        elif event["type"] in [KomorebiEvent.TitleUpdate.value]:
                            hwnd = event["content"][1]["hwnd"]
                            self._queue_title_update_icon_refresh(i, hwnd)
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
                    if (
                        self._curr_workspace_index not in self._pending_workspace_indexes
                    ):
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

    def _get_workspace_focused_hwnd(self, workspace_index: int) -> int | None:
        workspace = self._komorebic.get_workspace_by_index(self._komorebi_screen, workspace_index)
        if not workspace:
            return None

        focused_window = self._get_current_workspace_focused_window(workspace)
        if not focused_window:
            return None
        return focused_window.get("hwnd")

    def _is_workspace_default_focus_hwnd(self, workspace_index: int, hwnd: int | None) -> bool:
        if not hwnd:
            return False

        focused_hwnd = self._get_workspace_focused_hwnd(workspace_index)
        if focused_hwnd:
            return focused_hwnd == hwnd

        cached_hwnd = self._workspace_last_active_hwnd.get(workspace_index)
        return cached_hwnd == hwnd and self._workspace_contains_hwnd(workspace_index, hwnd)

    def _is_hwnd_already_focused(self, workspace_index: int, hwnd: int | None) -> bool:
        if not hwnd or self._curr_workspace_index != workspace_index:
            return False

        try:
            foreground_hwnd = get_foreground_hwnd()
        except Exception:
            foreground_hwnd = None
        return foreground_hwnd == hwnd and self._get_workspace_focused_hwnd(workspace_index) == hwnd

    def _get_current_focused_hwnd_for_log(self) -> int | None:
        try:
            focused_workspace = self._get_focused_workspace()
            if not focused_workspace:
                return None
            focused_window = self._get_current_workspace_focused_window(focused_workspace)
            return focused_window.get("hwnd") if focused_window else None
        except Exception:
            return None

    def _log_focus_diag(
        self,
        reason: str,
        target_hwnd: int | None = None,
        workspace_index: int | None = None,
    ) -> None:
        foreground_hwnd = None
        try:
            foreground_hwnd = get_foreground_hwnd()
        except Exception:
            pass

        _log_workspace_diag(
            "focus diag: reason=%s monitor=%s curr_ws=%s target_ws=%s target_hwnd=%s "
            "foreground_hwnd=%s komorebi_focused_hwnd=%s remembered_hwnd=%s pending_cursor_hwnd=%s "
            "pending_focus_hwnd=%s pending_token=%s mff_enabled=%s mff_suspended=%s",
            reason,
            self._komorebi_screen.get("index") if self._komorebi_screen else None,
            self._curr_workspace_index,
            workspace_index,
            target_hwnd,
            foreground_hwnd,
            self._get_current_focused_hwnd_for_log(),
            self._workspace_last_active_hwnd.get(workspace_index) if workspace_index is not None else None,
            self._pending_cursor_hwnd,
            self._pending_focus_hwnd,
            self._pending_focus_token,
            self._mouse_follows_focus_enabled(),
            self._icon_focus_suspended_mouse_follows_focus,
        )

    def _clear_active_icon_focus_request(self) -> None:
        self._active_icon_focus_request_id = None
        self._active_icon_focus_hwnd = None
        self._active_icon_focus_workspace_index = None
        self._active_icon_focus_reason = None

    def _begin_icon_focus_request(self, workspace_index: int, hwnd: int, reason: str) -> int:
        self._icon_focus_request_id += 1
        request_id = self._icon_focus_request_id
        self._active_icon_focus_request_id = request_id
        self._active_icon_focus_workspace_index = workspace_index
        self._active_icon_focus_hwnd = hwnd
        self._active_icon_focus_reason = reason
        _log_workspace_diag(
            "icon focus request started: id=%s workspace=%s hwnd=%s source=%s",
            request_id,
            workspace_index,
            hwnd,
            reason,
        )
        return request_id

    def _cancel_icon_focus_request(self, reason: str, clear_pending_workspace: bool = False) -> None:
        active_request_id = self._active_icon_focus_request_id
        active_workspace_index = self._active_icon_focus_workspace_index
        active_hwnd = self._active_icon_focus_hwnd
        active_reason = self._active_icon_focus_reason
        had_pending_focus = self._pending_focus_hwnd or self._pending_focus_workspace_index is not None
        had_pending_cursor = self._pending_cursor_hwnd or self._pending_cursor_workspace_index is not None
        had_pending_workspace = bool(self._pending_workspace_indexes)

        if (
            active_request_id is None
            and not had_pending_focus
            and not had_pending_cursor
            and not self._icon_focus_suspended_mouse_follows_focus
            and not (clear_pending_workspace and had_pending_workspace)
        ):
            return

        _log_workspace_diag(
            "icon focus request cancelled: reason=%s id=%s workspace=%s hwnd=%s source=%s",
            reason,
            active_request_id,
            active_workspace_index,
            active_hwnd,
            active_reason,
        )
        self._pending_cursor_hwnd = None
        self._pending_cursor_workspace_index = None
        if had_pending_focus:
            self._clear_pending_workspace_focus(reason)
        else:
            self._restore_mouse_follows_focus_after_icon_focus(reason)
        self._clear_active_icon_focus_request()
        if clear_pending_workspace and had_pending_workspace:
            self.clear_pending_workspace()

    def _complete_icon_focus_request(self, reason: str) -> None:
        request_id = self._active_icon_focus_request_id
        workspace_index = self._active_icon_focus_workspace_index
        hwnd = self._active_icon_focus_hwnd
        source = self._active_icon_focus_reason
        if request_id is None or workspace_index is None or not hwnd:
            return

        _log_workspace_diag(
            "icon focus request completed: reason=%s id=%s workspace=%s hwnd=%s source=%s",
            reason,
            request_id,
            workspace_index,
            hwnd,
            source,
        )
        self._clear_active_icon_focus_request()
        self._restore_mouse_follows_focus_after_icon_focus(reason)

    def _schedule_focus_diag_samples(
        self,
        reason: str,
        target_hwnd: int | None = None,
        workspace_index: int | None = None,
    ) -> None:
        for delay_ms in self._focus_diag_sample_delays_ms:
            QTimer.singleShot(
                delay_ms,
                lambda delay=delay_ms, r=reason, hwnd=target_hwnd, ws=workspace_index: self._log_focus_diag(
                    f"{r}+{delay}ms",
                    hwnd,
                    ws,
                ),
            )

    def _queue_title_update_icon_refresh(self, workspace_index: int, hwnd: int) -> None:
        if hwnd <= 0:
            return

        pending_hwnds = self._pending_title_update_icon_hwnds.setdefault(workspace_index, set())
        pending_hwnds.add(hwnd)
        flush_token = self._title_update_icon_flush_token + 1
        self._title_update_icon_flush_token = flush_token
        QTimer.singleShot(
            self._title_update_icon_refresh_delay_ms,
            lambda token=flush_token: self._flush_pending_title_update_icon_refreshes(token),
        )

    def _flush_pending_title_update_icon_refreshes(self, token: int) -> None:
        if token != self._title_update_icon_flush_token:
            return
        if not self._pending_title_update_icon_hwnds:
            return

        pending_refreshes = self._pending_title_update_icon_hwnds
        self._pending_title_update_icon_hwnds = {}
        for workspace_index, hwnds in pending_refreshes.items():
            try:
                workspace_button = self._workspace_buttons[workspace_index]
            except (IndexError, TypeError):
                continue
            for hwnd in hwnds:
                workspace_button.update_icon_by_hwnd(hwnd)

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
            _log_workspace_diag(
                "pending ignored: reason=missing_state target_ws=%s monitor=%s current_ws=%s",
                workspace_index,
                self._komorebi_screen.get("index") if self._komorebi_screen else None,
                self._curr_workspace_index,
            )
            return
        if workspace_index == self._curr_workspace_index:
            _log_workspace_diag(
                "pending ignored: reason=already_active monitor=%s current_ws=%s target_ws=%s",
                self._komorebi_screen.get("index"),
                self._curr_workspace_index,
                workspace_index,
            )
            return
        if workspace_index < 0 or workspace_index >= len(self._workspace_buttons):
            _log_workspace_diag(
                "pending ignored: reason=out_of_range monitor=%s current_ws=%s target_ws=%s button_count=%s",
                self._komorebi_screen.get("index"),
                self._curr_workspace_index,
                workspace_index,
                len(self._workspace_buttons),
            )
            return

        affected_workspace_indexes = {self._curr_workspace_index, workspace_index}
        self._pending_switch_token += 1
        self._pending_workspace_indexes.add(workspace_index)
        self._pending_workspace_tokens[workspace_index] = self._pending_switch_token
        _log_workspace_diag(
            "pending set: monitor=%s current_ws=%s target_ws=%s pending_ws=%s token=%s",
            self._komorebi_screen.get("index"),
            self._curr_workspace_index,
            workspace_index,
            sorted(self._pending_workspace_indexes),
            self._pending_switch_token,
        )
        self._redraw_pending_workspace_buttons(affected_workspace_indexes)
        QApplication.processEvents()

        token = self._pending_switch_token
        QTimer.singleShot(
            2000,
            lambda workspace_index=workspace_index, pending_token=token: self.clear_pending_workspace(
                workspace_index=workspace_index,
                token=pending_token,
            ),
        )

    def clear_pending_workspace(self, workspace_index: int | None = None, token: int | None = None) -> None:
        if workspace_index is None:
            if not self._pending_workspace_indexes:
                return
            cleared_workspace_indexes = set(self._pending_workspace_indexes)
            self._pending_workspace_indexes.clear()
            self._pending_workspace_tokens.clear()
        else:
            current_token = self._pending_workspace_tokens.get(workspace_index)
            if current_token is None:
                return
            if token is not None and token != current_token:
                _log_workspace_diag(
                    "pending clear ignored: reason=stale_token workspace=%s token=%s current_token=%s pending_ws=%s",
                    workspace_index,
                    token,
                    current_token,
                    sorted(self._pending_workspace_indexes),
                )
                return
            cleared_workspace_indexes = {workspace_index}
            self._pending_workspace_indexes.discard(workspace_index)
            self._pending_workspace_tokens.pop(workspace_index, None)

        if self._pending_focus_workspace_index in cleared_workspace_indexes:
            self._clear_pending_workspace_focus("pending_cleared")

        _log_workspace_diag(
            "pending cleared: monitor=%s current_ws=%s cleared_ws=%s remaining_pending_ws=%s token=%s",
            self._komorebi_screen.get("index") if self._komorebi_screen else None,
            self._curr_workspace_index,
            sorted(cleared_workspace_indexes),
            sorted(self._pending_workspace_indexes),
            self._pending_switch_token,
        )

        self._redraw_pending_workspace_buttons(cleared_workspace_indexes | {self._curr_workspace_index})

    def _redraw_pending_workspace_buttons(self, workspace_indexes: set[int | None] | None = None) -> None:
        pending_workspace_indexes = self._pending_workspace_indexes
        if workspace_indexes is None:
            workspace_indexes = set(pending_workspace_indexes)
            workspace_indexes.add(self._curr_workspace_index)

        for workspace_index in workspace_indexes:
            if workspace_index is None:
                continue
            try:
                self._sync_workspace_button_state(self._workspace_buttons[workspace_index], update_layer=False)
            except (IndexError, TypeError):
                continue

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

    def _sync_workspace_button_state(self, workspace_btn: WorkspaceButton, update_layer: bool = True) -> None:
        self._refresh_button_labels(workspace_btn)
        workspace_index = workspace_btn.workspace_index
        workspace = self._komorebic.get_workspace_by_index(self._komorebi_screen, workspace_index)
        workspace_status = self._get_workspace_new_status(workspace)
        is_pending = workspace_index in self._pending_workspace_indexes
        if self.config.hide_empty_workspaces and workspace_status == WORKSPACE_STATUS_EMPTY and not is_pending:
            workspace_btn.hide()
        else:
            current_classes = str(workspace_btn.property("class") or "").split()
            if (
                workspace_btn.status != workspace_status
                or "pending" in current_classes
                or workspace_status.lower() not in current_classes
            ):
                workspace_btn.update_and_redraw(workspace_status)
            if is_pending:
                _set_workspace_button_class(
                    workspace_btn,
                    self._get_workspace_non_active_status(workspace_index),
                    pending=True,
                )
                refresh_widget_style(workspace_btn)
            workspace_btn.show()
            workspace_btn.update_visible_buttons()
        if update_layer:
            self._get_workspace_layer(workspace_index)

    def _update_button(self, workspace_btn: WorkspaceButton) -> None:
        self._sync_workspace_button_state(workspace_btn, update_layer=True)

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

    def _refresh_all_workspace_icons(self) -> None:
        if not self._workspace_app_icons_enabled:
            return
        try:
            for workspace_button in self._workspace_buttons:
                workspace_button.update_icons()
        except Exception:
            logging.exception("Failed to refresh workspace icons")

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
        focused_hwnd = self._get_workspace_focused_hwnd(workspace_index)
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
                    "window_rect": window.get("rect"),
                    "focused": hwnd == focused_hwnd,
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

    def _get_workspace_focused_app_hwnd(self, workspace_index: int, app_key: str | None) -> int | None:
        if not app_key:
            return None

        workspace = self._komorebic.get_workspace_by_index(self._komorebi_screen, workspace_index)
        if not workspace:
            return None

        focused_window = self._get_current_workspace_focused_window(workspace)
        if not focused_window:
            return None

        hwnd = focused_window.get("hwnd")
        if hwnd and self._get_app_key(hwnd) == app_key:
            return hwnd
        return None

    def _resolve_workspace_target_hwnd(self, workspace_index: int, target_hwnd: int | None, app_key: str | None) -> int | None:
        if (
            not self.config.app_icons.hide_duplicates
            and target_hwnd
            and self._workspace_contains_hwnd(workspace_index, target_hwnd)
        ):
            return target_hwnd

        if app_key:
            cached_hwnd = self._workspace_app_last_active_hwnd.get((workspace_index, app_key))
            if cached_hwnd and self._workspace_contains_hwnd(workspace_index, cached_hwnd):
                return cached_hwnd

            focused_app_hwnd = self._get_workspace_focused_app_hwnd(workspace_index, app_key)
            if focused_app_hwnd and self._workspace_contains_hwnd(workspace_index, focused_app_hwnd):
                return focused_app_hwnd

            for window in self._get_all_windows_in_workspace(workspace_index):
                hwnd = window["hwnd"]
                if self._get_app_key(hwnd) == app_key:
                    return hwnd

        if target_hwnd and self._workspace_contains_hwnd(workspace_index, target_hwnd):
            return target_hwnd

        cached_workspace_hwnd = self._workspace_last_active_hwnd.get(workspace_index)
        if cached_workspace_hwnd and self._workspace_contains_hwnd(workspace_index, cached_workspace_hwnd):
            return cached_workspace_hwnd

        windows_in_workspace = self._get_all_windows_in_workspace(workspace_index)
        if windows_in_workspace:
            return windows_in_workspace[0]["hwnd"]
        return None

    def _get_tiling_window_location(self, workspace_index: int, hwnd: int) -> dict | None:
        workspace = self._komorebic.get_workspace_by_index(self._komorebi_screen, workspace_index)
        if not workspace or workspace.get("layer") != "Tiling":
            return None

        containers = workspace.get("containers", {})
        container_elements = containers.get("elements") if isinstance(containers, dict) else None
        if not isinstance(container_elements, list):
            return None

        focused_container_index = containers.get("focused")
        if not isinstance(focused_container_index, int):
            return None

        for container_index, container in enumerate(container_elements):
            windows = self._komorebic.get_windows(container)
            for window_index, window in enumerate(windows):
                if window.get("hwnd") == hwnd:
                    focused_window_index = container.get("windows", {}).get("focused")
                    return {
                        "container_index": container_index,
                        "container_count": len(container_elements),
                        "focused_container_index": focused_container_index,
                        "window_index": window_index,
                        "focused_window_index": focused_window_index if isinstance(focused_window_index, int) else None,
                    }
        return None

    def _focus_same_workspace_window_native(self, workspace_index: int, hwnd: int) -> bool:
        if not self._is_focused_monitor():
            _log_workspace_diag(
                "native same-workspace focus skipped: reason=monitor_not_focused workspace=%s hwnd=%s",
                workspace_index,
                hwnd,
            )
            return False

        location = self._get_tiling_window_location(workspace_index, hwnd)
        if not location:
            _log_workspace_diag(
                "native same-workspace focus skipped: reason=target_not_tiling workspace=%s hwnd=%s",
                workspace_index,
                hwnd,
            )
            return False

        container_index = location["container_index"]
        focused_container_index = location["focused_container_index"]
        container_count = location["container_count"]
        window_index = location["window_index"]
        focused_window_index = location["focused_window_index"]

        if container_count <= 0:
            return False

        forward_steps = (container_index - focused_container_index) % container_count
        backward_steps = (focused_container_index - container_index) % container_count
        if forward_steps <= backward_steps:
            direction = "next"
            steps = forward_steps
        else:
            direction = "previous"
            steps = backward_steps

        _log_workspace_diag(
            "native same-workspace focus executing: workspace=%s hwnd=%s container=%s focused_container=%s "
            "steps=%s direction=%s window_index=%s focused_window_index=%s",
            workspace_index,
            hwnd,
            container_index,
            focused_container_index,
            steps,
            direction,
            window_index,
            focused_window_index,
        )

        try:
            for _ in range(steps):
                self._komorebic.cycle_focus(direction, wait=True)
            if focused_window_index != window_index:
                self._komorebic.focus_stack_window(window_index, wait=True)
            return True
        except Exception:
            logging.exception("Failed to use native komorebi focus for HWND %s", hwnd)
            return False

    def _focus_hwnd(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        _log_workspace_diag("windows focus request: hwnd=%s", hwnd)
        self._log_focus_diag("before-win32-focus", hwnd, self._curr_workspace_index)
        try:
            restore_window(hwnd)
        except Exception:
            pass
        try:
            show_window(hwnd)
            set_foreground(hwnd)
            self._log_focus_diag("after-win32-focus", hwnd, self._curr_workspace_index)
            _log_workspace_diag("windows focus request completed: hwnd=%s", hwnd)
            return True
        except Exception:
            logging.exception("Failed to focus window with HWND %s", hwnd)
            return False

    def _finalize_pending_cursor_move(self) -> None:
        pending_hwnd = self._pending_cursor_hwnd
        pending_workspace_index = self._pending_cursor_workspace_index
        if not pending_hwnd or pending_workspace_index is None:
            return

        try:
            if self._curr_workspace_index != pending_workspace_index:
                _log_workspace_diag(
                    "cursor move deferred ignored: reason=workspace_mismatch current_ws=%s pending_ws=%s hwnd=%s",
                    self._curr_workspace_index,
                    pending_workspace_index,
                    pending_hwnd,
                )
                return
            if self._workspace_last_active_hwnd.get(pending_workspace_index) != pending_hwnd:
                _log_workspace_diag(
                    "cursor move deferred cleared: reason=focused_hwnd_mismatch workspace=%s expected_hwnd=%s "
                    "remembered_hwnd=%s",
                    pending_workspace_index,
                    pending_hwnd,
                    self._workspace_last_active_hwnd.get(pending_workspace_index),
                )
                self._pending_cursor_hwnd = None
                self._pending_cursor_workspace_index = None
                return
        except Exception:
            return

        self._pending_cursor_hwnd = None
        self._pending_cursor_workspace_index = None
        _log_workspace_diag("cursor move executing: workspace=%s hwnd=%s", pending_workspace_index, pending_hwnd)
        move_cursor_to_window_center(pending_hwnd)

    def _move_cursor_after_icon_focus(self, workspace_index: int, hwnd: int, reason: str) -> None:
        if self._curr_workspace_index != workspace_index:
            _log_workspace_diag(
                "icon cursor move ignored: reason=workspace_mismatch source=%s current_ws=%s target_ws=%s hwnd=%s",
                reason,
                self._curr_workspace_index,
                workspace_index,
                hwnd,
            )
            return

        self._pending_cursor_hwnd = None
        self._pending_cursor_workspace_index = None
        _log_workspace_diag("icon cursor move executing: source=%s workspace=%s hwnd=%s", reason, workspace_index, hwnd)
        move_cursor_to_window_center(hwnd)

    def _mouse_follows_focus_enabled(self) -> bool:
        try:
            value = self._komorebi_state.get("mouse_follows_focus") if self._komorebi_state else None
        except Exception:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"enable", "enabled", "true", "yes", "on"}
        return False

    def _suspend_mouse_follows_focus_for_icon_focus(self) -> None:
        if self._icon_focus_suspended_mouse_follows_focus:
            return
        if not self._mouse_follows_focus_enabled():
            return

        self._icon_focus_suspended_mouse_follows_focus = True
        _log_workspace_diag("icon focus suspending komorebi mouse-follows-focus")
        self._komorebic.set_mouse_follows_focus(False, wait=True)

    def _restore_mouse_follows_focus_after_icon_focus(self, reason: str) -> None:
        if not self._icon_focus_suspended_mouse_follows_focus:
            return

        self._icon_focus_suspended_mouse_follows_focus = False
        _log_workspace_diag("icon focus restoring komorebi mouse-follows-focus: reason=%s", reason)
        self._komorebic.set_mouse_follows_focus(True, wait=False)

    def _set_pending_workspace_focus(self, workspace_index: int, hwnd: int) -> None:
        self._pending_focus_hwnd = hwnd
        self._pending_focus_workspace_index = workspace_index
        self._pending_focus_token = self._pending_switch_token
        _log_workspace_diag(
            "delayed icon focus armed: workspace=%s hwnd=%s token=%s",
            workspace_index,
            hwnd,
            self._pending_focus_token,
        )

    def _clear_pending_workspace_focus(self, reason: str) -> None:
        if self._pending_focus_hwnd or self._pending_focus_workspace_index is not None:
            _log_workspace_diag(
                "delayed icon focus cleared: reason=%s workspace=%s hwnd=%s token=%s",
                reason,
                self._pending_focus_workspace_index,
                self._pending_focus_hwnd,
                self._pending_focus_token,
            )
        self._pending_focus_hwnd = None
        self._pending_focus_workspace_index = None
        self._pending_focus_token = None
        if reason != "focus_started":
            self._restore_mouse_follows_focus_after_icon_focus(reason)

    def _focus_pending_workspace_window_if_ready(self) -> None:
        pending_hwnd = self._pending_focus_hwnd
        pending_workspace_index = self._pending_focus_workspace_index
        pending_token = self._pending_focus_token
        if not pending_hwnd or pending_workspace_index is None:
            return

        if pending_token != self._pending_switch_token:
            self._clear_pending_workspace_focus("stale_token")
            return
        if self._curr_workspace_index != pending_workspace_index:
            return

        self._pending_cursor_hwnd = pending_hwnd
        self._pending_cursor_workspace_index = pending_workspace_index
        self._clear_pending_workspace_focus("focus_started")
        _log_workspace_diag(
            "delayed icon focus executing: workspace=%s hwnd=%s token=%s",
            pending_workspace_index,
            pending_hwnd,
            pending_token,
        )
        self._schedule_focus_diag_samples("before-delayed-icon-focus", pending_hwnd, pending_workspace_index)
        if self._is_hwnd_already_focused(pending_workspace_index, pending_hwnd):
            _log_workspace_diag(
                "delayed icon focus skipped: reason=already_focused workspace=%s hwnd=%s token=%s",
                pending_workspace_index,
                pending_hwnd,
                pending_token,
            )
            self._move_cursor_after_icon_focus(
                pending_workspace_index,
                pending_hwnd,
                "delayed_icon_focus_already_focused",
            )
            self._complete_icon_focus_request("delayed_focus_already_focused")
            return

        if not self._focus_hwnd(pending_hwnd):
            self._pending_cursor_hwnd = None
            self._pending_cursor_workspace_index = None
            _log_workspace_diag(
                "delayed icon focus failed: workspace=%s hwnd=%s token=%s",
                pending_workspace_index,
                pending_hwnd,
                pending_token,
            )
            self._cancel_icon_focus_request("delayed_focus_failed")
        else:
            self._schedule_focus_diag_samples("after-delayed-icon-focus", pending_hwnd, pending_workspace_index)
            self._move_cursor_after_icon_focus(pending_workspace_index, pending_hwnd, "delayed_icon_focus")
            self._complete_icon_focus_request("delayed_focus_complete")

    def focus_workspace_window(self, workspace_index: int, target_hwnd: int | None, app_key: str | None = None) -> None:
        try:
            if not self._komorebi_screen:
                _log_workspace_diag(
                    "icon click ignored: reason=missing_screen target_ws=%s target_hwnd=%s app_key=%s",
                    workspace_index,
                    target_hwnd,
                    app_key,
                )
                return

            resolved_hwnd = self._resolve_workspace_target_hwnd(workspace_index, target_hwnd, app_key)
            _log_workspace_diag(
                "icon click: monitor=%s current_ws=%s target_ws=%s target_hwnd=%s app_key=%s resolved_hwnd=%s",
                self._komorebi_screen.get("index"),
                self._curr_workspace_index,
                workspace_index,
                target_hwnd,
                app_key,
                resolved_hwnd,
            )
            self._log_focus_diag("icon-click-resolved", resolved_hwnd, workspace_index)
            if not resolved_hwnd:
                if self._curr_workspace_index != workspace_index:
                    self.set_pending_workspace(workspace_index)
                    _log_workspace_diag(
                        "icon click fallback activates workspace: monitor=%s target_ws=%s reason=no_resolved_hwnd",
                        self._komorebi_screen.get("index"),
                        workspace_index,
                    )
                    self._komorebic.activate_workspace(self._komorebi_screen["index"], workspace_index)
                return

            if (
                self._curr_workspace_index != workspace_index
                and self._is_workspace_default_focus_hwnd(workspace_index, resolved_hwnd)
            ):
                self._cancel_icon_focus_request(
                    "superseded_by_cross_workspace_default_focus_fast_path",
                    clear_pending_workspace=True,
                )
                self.set_pending_workspace(workspace_index)
                _log_workspace_diag(
                    "icon click fast path activates workspace only: monitor=%s target_ws=%s resolved_hwnd=%s",
                    self._komorebi_screen.get("index"),
                    workspace_index,
                    resolved_hwnd,
                )
                self._komorebic.activate_workspace(self._komorebi_screen["index"], workspace_index)
                return

            self._cancel_icon_focus_request("superseded_by_new_request", clear_pending_workspace=True)
            self._begin_icon_focus_request(
                workspace_index,
                resolved_hwnd,
                "cross_workspace_icon_focus" if self._curr_workspace_index != workspace_index else "same_workspace_icon_focus",
            )

            if self._curr_workspace_index != workspace_index:
                self.set_pending_workspace(workspace_index)
                self._set_pending_workspace_focus(workspace_index, resolved_hwnd)
                self._suspend_mouse_follows_focus_for_icon_focus()
                _log_workspace_diag(
                    "icon click activates workspace before focus: monitor=%s target_ws=%s resolved_hwnd=%s",
                    self._komorebi_screen.get("index"),
                    workspace_index,
                    resolved_hwnd,
                )
                self._schedule_focus_diag_samples("before-workspace-activate-for-icon-focus", resolved_hwnd, workspace_index)
                self._komorebic.activate_workspace(self._komorebi_screen["index"], workspace_index)
                return

            if self._workspace_last_active_hwnd.get(workspace_index) == resolved_hwnd:
                self._log_focus_diag("same-workspace-already-focused", resolved_hwnd, workspace_index)
                self._move_cursor_after_icon_focus(workspace_index, resolved_hwnd, "same_workspace_already_focused")
                self._complete_icon_focus_request("already_focused")
                return

            self._pending_cursor_hwnd = resolved_hwnd
            self._pending_cursor_workspace_index = workspace_index
            self._schedule_focus_diag_samples("before-same-workspace-icon-focus", resolved_hwnd, workspace_index)

            if self._focus_same_workspace_window_native(workspace_index, resolved_hwnd):
                self._schedule_focus_diag_samples("after-same-workspace-native-focus", resolved_hwnd, workspace_index)
                self._move_cursor_after_icon_focus(workspace_index, resolved_hwnd, "same_workspace_native_focus")
                self._complete_icon_focus_request("same_workspace_native_focus_complete")
                return

            if not self._focus_hwnd(resolved_hwnd):
                self._pending_cursor_hwnd = None
                self._pending_cursor_workspace_index = None
                _log_workspace_diag(
                    "icon click focus failed: target_ws=%s target_hwnd=%s resolved_hwnd=%s",
                    workspace_index,
                    target_hwnd,
                    resolved_hwnd,
                )
                self._cancel_icon_focus_request("same_workspace_focus_failed")
            else:
                self._schedule_focus_diag_samples("after-same-workspace-icon-focus", resolved_hwnd, workspace_index)
                self._move_cursor_after_icon_focus(workspace_index, resolved_hwnd, "same_workspace_icon_focus")
                self._complete_icon_focus_request("same_workspace_focus_complete")
        except Exception:
            self._cancel_icon_focus_request("icon_focus_exception", clear_pending_workspace=True)
            logging.exception(
                "Failed to focus workspace window for workspace %s and HWND %s",
                workspace_index,
                target_hwnd,
            )
