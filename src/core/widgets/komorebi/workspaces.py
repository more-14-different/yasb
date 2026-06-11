import logging
import math
from contextlib import suppress
from typing import Literal

from PIL import Image
from PyQt6.QtCore import QPoint, QRect, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget
from win32con import HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE

from core.events.komorebi import KomorebiEvent
from core.events.service import EventService
from core.utils.utilities import refresh_widget_style
from core.utils.win32.bindings import SetWindowPos
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
    current_classes = current_class.split()
    button_classes = [cls for cls in current_classes if cls.startswith("button-")]
    pseudo_classes = [cls for cls in current_classes if cls.startswith("pseudo-")]
    classes = ["ws-btn"]
    if pending:
        classes.append("pending")
    classes.append(status.lower())
    classes.extend(button_classes)
    classes.extend(pseudo_classes)
    widget.setProperty("class", " ".join(classes))


class WorkspaceButtonMixin:
    """Shared behavior mixin for workspace button variants (WorkspaceButton, WorkspaceButtonWithIcons)."""

    def update_visible_buttons(self):
        visible_buttons = [btn for btn in self.parent_widget._workspace_buttons if not btn.isHidden()]
        for index, button in enumerate(visible_buttons):
            target_widget = button.widget_to_style
            current_class = str(target_widget.property("class") or "")
            new_class = " ".join([cls for cls in current_class.split() if not cls.startswith("button-")])
            new_class = f"{new_class} button-{index + 1}"
            target_widget.setProperty("class", new_class)
            refresh_widget_style(target_widget)

    def activate_workspace(self):
        try:
            screen_index = (
                self.parent_widget._komorebi_screen.get("index") if self.parent_widget._komorebi_screen else None
            )
            _log_workspace_diag(
                "workspace click: monitor=%s current_ws=%s target_ws=%s",
                screen_index,
                self.parent_widget._curr_workspace_index,
                self.workspace_index,
            )
            self.parent_widget.set_pending_workspace(self.workspace_index)
            self.komorebic.activate_workspace(self.parent_widget._komorebi_screen["index"], self.workspace_index)
        except Exception:
            self.parent_widget.clear_pending_workspace()
            logging.exception("Failed to focus workspace at index %s", self.workspace_index)


class WorkspaceButton(WorkspaceButtonMixin, QPushButton):
    @property
    def widget_to_style(self):
        return self

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
        self.default_label = label if label and label.strip() else str(workspace_index + 1)
        self.active_label = active_label if active_label and active_label.strip() else self.default_label
        self.populated_label = populated_label if populated_label and populated_label.strip() else self.default_label
        self.setText(self.default_label)
        self.clicked.connect(self.activate_workspace)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, self.sizePolicy().verticalPolicy())
        self.hide()
        self.update_and_redraw(self.status)

    def update_and_redraw(self, status: WorkspaceStatus):
        self.status = status
        _set_workspace_button_class(self.widget_to_style, status)
        if status == WORKSPACE_STATUS_ACTIVE:
            self.setText(self.active_label)
        elif status == WORKSPACE_STATUS_POPULATED:
            self.setText(self.populated_label)
        else:
            self.setText(self.default_label)
        refresh_widget_style(self.widget_to_style)


class WorkspaceTextLabel(QLabel):
    def __init__(self, text: str, parent_button: "WorkspaceButtonWithIcons"):
        super().__init__(text)
        self.parent_button = parent_button

    def enterEvent(self, event):
        super().enterEvent(event)
        self.parent_button._on_text_label_enter()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.parent_button._on_text_label_leave()


class WorkspaceButtonWithIcons(WorkspaceButtonMixin, QFrame):
    @property
    def widget_to_style(self):
        return self.text_label

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
        self.setProperty("class", "ws-btn-container")
        self.default_label = label if label and label.strip() else str(workspace_index + 1)
        self.active_label = active_label if active_label and active_label.strip() else self.default_label
        self.populated_label = populated_label if populated_label and populated_label.strip() else self.default_label

        self.setSizePolicy(QSizePolicy.Policy.Fixed, self.sizePolicy().verticalPolicy())

        self.button_layout = QHBoxLayout(self)
        self.button_layout.setContentsMargins(0, 0, 0, 0)
        self.button_layout.setSpacing(0)
        self.button_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self.text_label = WorkspaceTextLabel(self.default_label, self)
        self.text_label.setProperty("class", "ws-btn")
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
            self.activate_workspace()
            event.accept()
            return
        super().mousePressEvent(event)

    def set_pseudo_hover(self, hovered: bool):
        current_class = str(self.text_label.property("class") or "")
        classes = set(current_class.split())
        if hovered:
            classes.add("pseudo-hover")
        else:
            classes.discard("pseudo-hover")
        new_class = " ".join(classes)
        if new_class != current_class:
            self.text_label.setProperty("class", new_class)
            refresh_widget_style(self.text_label)

    def set_pseudo_pending(self, pending: bool):
        current_class = str(self.text_label.property("class") or "")
        classes = set(current_class.split())
        if pending:
            classes.add("pseudo-pending")
        else:
            classes.discard("pseudo-pending")
        new_class = " ".join(classes)
        if new_class != current_class:
            self.text_label.setProperty("class", new_class)
            refresh_widget_style(self.text_label)

    def _on_text_label_enter(self):
        self._update_icons_paint()

    def _on_text_label_leave(self):
        self._update_icons_paint()

    def _update_icons_paint(self):
        for icon in self.icon_labels:
            icon.update()
        if hasattr(self, "preview_widget") and self.preview_widget:
            for tile in self.preview_widget._tiles:
                tile.update()

    def update_and_redraw(self, status: WorkspaceStatus):
        self.status = status
        _set_workspace_button_class(self.widget_to_style, status)
        if status == WORKSPACE_STATUS_ACTIVE:
            self.text_label.setText(self.active_label)
        elif status == WORKSPACE_STATUS_POPULATED:
            self.text_label.setText(self.populated_label)
        else:
            self.text_label.setText(self.default_label)
        refresh_widget_style(self.widget_to_style)
        
        
        logging.info(f"[DEBUG YASB] Workspace {self.workspace_index} text_label: status={status}, repr(text)={repr(self.text_label.text())}, isVisible={self.text_label.isVisible()}, isHidden={self.text_label.isHidden()}")

        if self.preview_widget.isVisible():
            self.preview_widget.refresh_preview_styles()
        self._update_icons_paint()

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
            self.text_label.show()
            return

        self.preview_widget.clear_preview()
        self._show_row_icons(icons_list)
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
        for label in self.icon_labels:
            self.button_layout.removeWidget(label)
            label.setParent(None)
            label.deleteLater()
        self.icon_labels = []

        for index, icon_entry in enumerate(icons_list):
            icon_label = WorkspaceAppIconLabel(self.workspace_index, self.parent_widget, self)
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
        if not workspace:
            return False
        return all(icon_entry.get("window_rect") for icon_entry in icons_list)


class WorkspaceAppIconLabel(QLabel):
    def __init__(self, workspace_index: int, parent_widget: "WorkspaceWidget", parent_button: "WorkspaceButtonWithIcons" = None):
        super().__init__()
        self.workspace_index = workspace_index
        self.parent_widget = parent_widget
        self.parent_button = parent_button
        self.target_hwnd = None
        self.app_key = None
        self._is_hovered = False
        self._is_pending_jump = False

    def enterEvent(self, event):
        self._is_hovered = True
        self.update()
        button = self.parent_button
        if hasattr(button, "set_pseudo_hover"):
            button.set_pseudo_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        self.update()
        button = self.parent_button
        if hasattr(button, "set_pseudo_hover"):
            button.set_pseudo_hover(False)
        super().leaveEvent(event)

    def hideEvent(self, event):
        if self._is_hovered:
            self._is_hovered = False
            button = self.parent_button
            if hasattr(button, "set_pseudo_hover"):
                button.set_pseudo_hover(False)
        super().hideEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        from PyQt6.QtGui import QPainter, QColor, QPen
        from PyQt6.QtCore import Qt
        painter = QPainter(self)
        
        classes = str(self.property("class") or "").split()
        is_focused = "focused" in classes
        is_last_focused = "last-focused" in classes
        
        is_active_workspace = self.workspace_index == self.parent_widget._curr_workspace_index

        button = self.parent_button
        is_workspace_hovered = False
        is_workspace_pending = False
        if hasattr(button, "text_label") and button.text_label:
            is_workspace_hovered = button.text_label.underMouse()
            btn_label_classes = str(button.text_label.property("class") or "").split()
            # Only 'pending' (digit-click / keyboard switch) spreads cyan to sibling icons.
            # 'pseudo-pending' (icon-click) must NOT spread cyan beyond the clicked icon.
            is_workspace_pending = "pending" in btn_label_classes and "pseudo-pending" not in btn_label_classes

        is_focused_or_last = is_focused or is_last_focused

        if self._is_pending_jump or (is_workspace_pending and is_focused_or_last):
            painter.fillRect(self.rect(), QColor(156, 207, 216, 51))
            pen = QPen(QColor(156, 207, 216, 255), 1, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([2, 2])
            painter.setPen(pen)
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        elif self._is_hovered or (is_workspace_hovered and is_focused_or_last):
            painter.fillRect(self.rect(), QColor(246, 193, 119, 51))
            pen = QPen(QColor(246, 193, 119, 255), 1, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([2, 2])
            painter.setPen(pen)
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        elif is_focused and is_active_workspace:
            painter.setPen(QPen(QColor(246, 193, 119, 255), 1, Qt.PenStyle.SolidLine))
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        elif (is_focused and not is_active_workspace) or is_last_focused:
            painter.setPen(QPen(QColor(141, 163, 184, 255), 1, Qt.PenStyle.SolidLine))
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
            
        painter.end()

    def update_icon(self, icon_entry: dict):
        self._is_pending_jump = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        button = self.parent_button
        if hasattr(button, "set_pseudo_pending"):
            button.set_pseudo_pending(False)
        self.target_hwnd = icon_entry["hwnd"]
        self.app_key = icon_entry["app_key"]
        self.setProperty("class", icon_entry["class_name"])
        self.setPixmap(icon_entry["pixmap"])
        refresh_widget_style(self)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_pending_jump = True
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            
            button = self.parent_button
            if hasattr(button, "set_pseudo_pending"):
                button.set_pseudo_pending(True)
            
            # Force immediate synchronous repaint before any blocking focus calls
            self.repaint()
            if hasattr(button, "text_label") and button.text_label:
                button.text_label.repaint()
                
            self.parent_widget.focus_workspace_window(self.workspace_index, self.target_hwnd, self.app_key)
            self._apply_instant_focus()
            event.accept()
            return
        super().mousePressEvent(event)

    def _apply_instant_focus(self):
        try:
            button = self.parent_button
            
            # 1. Update icon label styles instantly
            for icon in button.icon_labels:
                old_class = str(icon.property("class") or "")
                if " focused" in old_class or " last-focused" in old_class:
                    icon.setProperty("class", old_class.replace(" focused", "").replace(" last-focused", ""))
                    refresh_widget_style(icon)
            
            new_class = str(self.property("class") or "")
            if " focused" not in new_class:
                self.setProperty("class", new_class.replace(" last-focused", "") + " focused")
                refresh_widget_style(self)

            # 2. Update layout preview tile styles instantly
            if button.preview_widget:
                for tile in button.preview_widget._tiles:
                    tile_class = str(tile.property("class") or "")
                    if " focused" in tile_class or " last-focused" in tile_class:
                        tile.setProperty("class", tile_class.replace(" focused", "").replace(" last-focused", ""))
                        refresh_widget_style(tile)
                    if getattr(tile, "target_hwnd", None) == self.target_hwnd:
                        new_tile_class = str(tile.property("class") or "")
                        if " focused" not in new_tile_class:
                            tile.setProperty("class", new_tile_class.replace(" last-focused", "") + " focused")
                            refresh_widget_style(tile)
                            self.parent_widget._set_workspace_focused_tile(self.workspace_index, tile)
        except Exception:
            pass


class WorkspacePreviewTile(QFrame):
    def __init__(self, workspace_index: int, parent_widget: "WorkspaceWidget", owner: "WorkspaceLayoutPreview"):
        super().__init__(owner)
        self.workspace_index = workspace_index
        self.parent_widget = parent_widget
        self.owner = owner
        self.parent_button = owner.parent_button
        self.target_hwnd = None
        self.app_key = None
        self.icon_label = QLabel(self)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._icon_size = QSize()
        self.setProperty("class", "layout-preview-tile")
        self._is_hovered = False
        self._is_pending_jump = False
        
        # Add drop shadow for stack/deck of cards effect
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        from PyQt6.QtGui import QColor
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 100))
        shadow.setOffset(2, 2)
        self.setGraphicsEffect(shadow)

    def enterEvent(self, event):
        self._is_hovered = True
        self.update()
        button = self.parent_button
        if hasattr(button, "set_pseudo_hover"):
            button.set_pseudo_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        self.update()
        button = self.parent_button
        if hasattr(button, "set_pseudo_hover"):
            button.set_pseudo_hover(False)
        super().leaveEvent(event)

    def hideEvent(self, event):
        if self._is_hovered:
            self._is_hovered = False
            button = self.parent_button
            if hasattr(button, "set_pseudo_hover"):
                button.set_pseudo_hover(False)
        super().hideEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        from PyQt6.QtGui import QPainter, QColor, QPen
        from PyQt6.QtCore import Qt
        painter = QPainter(self)
        
        classes = str(self.property("class") or "").split()
        is_focused = "focused" in classes
        is_last_focused = "last-focused" in classes
        
        is_active_workspace = self.workspace_index == self.parent_widget._curr_workspace_index

        button = self.parent_button
        is_workspace_hovered = False
        is_workspace_pending = False
        if hasattr(button, "text_label") and button.text_label:
            is_workspace_hovered = button.text_label.underMouse()
            btn_label_classes = str(button.text_label.property("class") or "").split()
            # Only 'pending' (digit-click / keyboard switch) spreads cyan to sibling tiles.
            # 'pseudo-pending' (icon-click) must NOT spread cyan beyond the clicked tile.
            is_workspace_pending = "pending" in btn_label_classes and "pseudo-pending" not in btn_label_classes

        is_focused_or_last = is_focused or is_last_focused

        if self._is_pending_jump or (is_workspace_pending and is_focused_or_last):
            painter.fillRect(self.rect(), QColor(156, 207, 216, 51))
            pen = QPen(QColor(156, 207, 216, 255), 1, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([2, 2])
            painter.setPen(pen)
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        elif self._is_hovered or (is_workspace_hovered and is_focused_or_last):
            painter.fillRect(self.rect(), QColor(246, 193, 119, 51))
            pen = QPen(QColor(246, 193, 119, 255), 1, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([2, 2])
            painter.setPen(pen)
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        elif is_focused and is_active_workspace:
            painter.setPen(QPen(QColor(246, 193, 119, 255), 1, Qt.PenStyle.SolidLine))
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        elif (is_focused and not is_active_workspace) or is_last_focused:
            painter.setPen(QPen(QColor(141, 163, 184, 255), 1, Qt.PenStyle.SolidLine))
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
            
        painter.end()

    def update_entry(self, icon_entry: dict, tile_class: str) -> None:
        self._is_pending_jump = False
        button = self.parent_button
        if hasattr(button, "set_pseudo_pending"):
            button.set_pseudo_pending(False)
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
            self._is_pending_jump = True
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            
            button = self.parent_button
            if hasattr(button, "set_pseudo_pending"):
                button.set_pseudo_pending(True)
                
            # Force immediate synchronous repaint before any blocking focus calls
            self.repaint()
            if hasattr(button, "text_label") and button.text_label:
                button.text_label.repaint()
                
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
            return QSize(self._current_canvas_size.width(), self._current_canvas_size.height())
        cfg = self.parent_widget.config.app_icons
        height = max(cfg.size + 8, cfg.preview_height)
        width = max(int(height * max(1.2, cfg.preview_aspect_ratio)), cfg.size * 2)
        return QSize(width, height)

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
            if tile.isVisible():
                tile.hide()
        if self._overlay.isVisible():
            self._overlay.hide()
        if self.isVisible():
            self.hide()

    def refresh_preview_styles(self) -> None:
        self._sync_overlay_class()
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
        self.show()
        if not self._overlay.isVisible():
            self._overlay.show()
        self._sync_overlay_geometry()
        self._raise_overlay()
        return True

    def _get_row_icon_padding_horizontal(self) -> int:
        try:
            from core.bar_helper import ThemeState
            stylesheet = ThemeState.stylesheet()
            if not stylesheet:
                return 4

            import re
            blocks = re.findall(r'([^{]+)\{([^}]+)\}', stylesheet)

            best_padding = None
            for selector, rules in blocks:
                selector = selector.strip()
                if '.icon' in selector:
                    padding_match = re.search(r'\bpadding\s*:\s*([^;]+)', rules)
                    padding_left_match = re.search(r'\bpadding-left\s*:\s*([^;]+)', rules)
                    padding_right_match = re.search(r'\bpadding-right\s*:\s*([^;]+)', rules)

                    if padding_left_match or padding_right_match:
                        val_left = 0
                        val_right = 0
                        if padding_left_match:
                            m = re.search(r'(\d+)\s*px', padding_left_match.group(1))
                            if m:
                                val_left = int(m.group(1))
                        if padding_right_match:
                            m = re.search(r'(\d+)\s*px', padding_right_match.group(1))
                            if m:
                                val_right = int(m.group(1))
                        if val_left > 0 or val_right > 0:
                            best_padding = max(val_left, val_right)
                    elif padding_match:
                        padding_val = padding_match.group(1).strip()
                        parts = re.findall(r'(\d+)\s*(?:px)?', padding_val)
                        if parts:
                            if len(parts) == 1:
                                best_padding = int(parts[0])
                            elif len(parts) == 2:
                                best_padding = int(parts[1])
                            elif len(parts) == 3:
                                best_padding = int(parts[1])
                            elif len(parts) == 4:
                                best_padding = int(parts[3])

                    if '.komorebi-workspaces' in selector and best_padding is not None:
                        return best_padding
            if best_padding is not None:
                return best_padding
        except Exception as e:
            logging.exception("Failed to parse icon padding from stylesheet: %s", e)
        return 4

    def _apply_layout(self) -> bool:
        bounds = self._compute_bounds(self._entries)
        if not bounds:
            return False

        cfg = self.parent_widget.config.app_icons
        padding = max(0, self._get_row_icon_padding_horizontal() // 2)
        icon_footprint = max(1, cfg.size + 2 * padding)
        rects: list[tuple[int, int, int, int]] = []
        for icon_entry in self._entries:
            rect = self._rect_to_geometry(icon_entry.get("window_rect"))
            if not rect:
                return False
            rects.append(rect)

        normalized_rects, content_size = self._compact_layout_rects(rects, icon_footprint)
        if content_size.width() <= 0 or content_size.height() <= 0:
            return False
        frame_padding = max(3, padding + 2)
        normalized_rects = [rect.translated(frame_padding, frame_padding) for rect in normalized_rects]
        content_size = QSize(content_size.width() + frame_padding * 2, content_size.height() + frame_padding * 2)

        previous_size = QSize(self._current_canvas_size)
        self._current_canvas_size = content_size
        self.setFixedSize(content_size.width(), content_size.height())
        self.setMinimumSize(content_size.width(), content_size.height())
        self._overlay.setFixedSize(content_size)
        if previous_size != content_size:
            self._request_parent_layout_update()

        # Reuse existing tiles to prevent EVENT_OBJECT_CREATE/DESTROY storms
        while len(self._tiles) > len(self._entries):
            tile = self._tiles.pop()
            tile.hide()
            tile.setParent(None)
            tile.deleteLater()

        while len(self._tiles) < len(self._entries):
            tile = WorkspacePreviewTile(self.workspace_index, self.parent_widget, self)
            tile.setParent(self._overlay)
            self._tiles.append(tile)

        # Sort rendering order so focused tiles are raised last (on top of the deck)
        render_order = []
        for index, icon_entry in enumerate(self._entries):
            if icon_entry.get("focused"):
                render_order.append(index)
            else:
                render_order.insert(0, index) # Non-focused first
                
        # Actually, we want to keep the original order for non-focused to maintain predictable stacking,
        # but just move focused to the end.
        non_focused = [i for i, e in enumerate(self._entries) if not e.get("focused")]
        focused = [i for i, e in enumerate(self._entries) if e.get("focused")]
        render_order = non_focused + focused

        for index in render_order:
            icon_entry = self._entries[index]
            tile = self._tiles[index]
            tile_rect = normalized_rects[index]
            tile.setGeometry(tile_rect)
            tile_class = "layout-preview-tile"
            is_focused = icon_entry.get("focused") and cfg.preview_show_focus
            if is_focused:
                tile_class += " focused"
            elif icon_entry.get("last_focused"):
                tile_class += " last-focused"
            tile.update_entry(icon_entry, tile_class)

            if is_focused:
                self.parent_widget._set_workspace_focused_tile(self.workspace_index, tile)

            tile.show()
            tile.raise_()

        self.setProperty("class", "layout-preview-anchor")
        self._sync_overlay_class()
        refresh_widget_style(self)
        return True

    def _sync_overlay_class(self) -> None:
        status = self.parent_button.status.lower()
        status_class = f" {status}" if status else ""
        self._overlay.setProperty("class", f"layout-preview{status_class}")

    def _compact_layout_rects(self, rects: list[tuple[int, int, int, int]], icon_footprint: int) -> tuple[list[QRect], QSize]:
        if not rects:
            return [], QSize()

        # Step 1: Cluster identical rects (stacks)
        clusters: dict[tuple[int, int, int, int], list[int]] = {}
        for i, r in enumerate(rects):
            clusters.setdefault(r, []).append(i)

        unique_rects = list(clusters.keys())
        indexed_unique_rects = list(enumerate(unique_rects))

        # Step 2: Compute layout for unique rects
        layout_items = self._build_compact_tree_layout(indexed_unique_rects)
        if layout_items:
            positions, width_units, height_units = layout_items
            normalized_rects = [QRect() for _ in rects]

            # Step 3: Apply positions and offsets for stacks
            STACK_OFFSET_UNITS = 0.15  # 15% of icon_footprint offset per stacked window
            MAX_STACK_WIDTH_ADD = 0.5  # Max total offset added to width

            max_x = width_units
            max_y = height_units

            for unique_index, x_units, y_units in positions:
                rect_key = unique_rects[unique_index]
                stack_indices = clusters[rect_key]

                for stack_pos, original_index in enumerate(stack_indices):
                    # Apply offset (capped)
                    offset = min(stack_pos * STACK_OFFSET_UNITS, MAX_STACK_WIDTH_ADD)
                    final_x = x_units + offset
                    final_y = y_units + offset

                    normalized_rects[original_index] = QRect(
                        int(round(final_x * icon_footprint)),
                        int(round(final_y * icon_footprint)),
                        icon_footprint,
                        icon_footprint,
                    )

                    max_x = max(max_x, final_x + 1.0)
                    max_y = max(max_y, final_y + 1.0)

            content_size = QSize(
                max(1, int(math.ceil(max_x * icon_footprint))),
                max(1, int(math.ceil(max_y * icon_footprint))),
            )
            return normalized_rects, content_size

        return [], QSize()

    def _build_compact_tree_layout(
        self,
        indexed_rects: list[tuple[int, tuple[int, int, int, int]]],
    ) -> tuple[list[tuple[int, float, float]], float, float] | None:
        if len(indexed_rects) == 1:
            return [(indexed_rects[0][0], 0.0, 0.0)], 1.0, 1.0

        axis_groups = self._find_split_groups(indexed_rects, "x")
        if len(axis_groups) <= 1:
            axis_groups = self._find_split_groups(indexed_rects, "y")
            axis = "y"
        else:
            y_groups = self._find_split_groups(indexed_rects, "y")
            axis = "x" if len(y_groups) <= 1 or len(axis_groups) <= len(y_groups) else "y"
            if axis == "y":
                axis_groups = y_groups

        if len(axis_groups) <= 1:
            return None

        child_layouts = [self._build_compact_tree_layout(group) for group in axis_groups]
        if any(child_layout is None for child_layout in child_layouts):
            return None

        positions: list[tuple[int, float, float]] = []
        if axis == "x":
            total_width = sum(child_layout[1] for child_layout in child_layouts if child_layout)
            total_height = max(child_layout[2] for child_layout in child_layouts if child_layout)
            cursor_x = 0.0
            for child_positions, child_width, child_height in child_layouts:
                offset_y = (total_height - child_height) / 2
                positions.extend((index, x + cursor_x, y + offset_y) for index, x, y in child_positions)
                cursor_x += child_width
            return positions, total_width, total_height

        total_width = max(child_layout[1] for child_layout in child_layouts if child_layout)
        total_height = sum(child_layout[2] for child_layout in child_layouts if child_layout)
        cursor_y = 0.0
        for child_positions, child_width, child_height in child_layouts:
            offset_x = (total_width - child_width) / 2
            positions.extend((index, x + offset_x, y + cursor_y) for index, x, y in child_positions)
            cursor_y += child_height
        return positions, total_width, total_height

    def _find_split_groups(
        self,
        indexed_rects: list[tuple[int, tuple[int, int, int, int]]],
        axis: str,
    ) -> list[list[tuple[int, tuple[int, int, int, int]]]]:
        sorted_items = sorted(indexed_rects, key=lambda item: self._axis_interval(item[1], axis)[0])
        groups: list[list[tuple[int, tuple[int, int, int, int]]]] = []
        if not sorted_items:
            return groups

        current_group = [sorted_items[0]]
        current_max_end = self._axis_interval(sorted_items[0][1], axis)[1]

        for item in sorted_items[1:]:
            interval = self._axis_interval(item[1], axis)
            if interval[0] < current_max_end:
                current_group.append(item)
                current_max_end = max(current_max_end, interval[1])
            else:
                groups.append(current_group)
                current_group = [item]
                current_max_end = interval[1]
                
        if current_group:
            groups.append(current_group)

        groups.sort(key=lambda group: min(self._axis_center(rect, axis) for _idx, rect in group))
        return groups

    def _compact_axis_positions(self, rects: list[tuple[int, int, int, int]], axis: str) -> list[int]:
        constraints: list[tuple[int, int]] = []
        for left_index, left_rect in enumerate(rects):
            for right_index, right_rect in enumerate(rects):
                if left_index == right_index:
                    continue
                if axis == "x":
                    if self._axis_center(left_rect, "x") < self._axis_center(right_rect, "x") and self._intervals_overlap(
                        self._axis_interval(left_rect, "y"),
                        self._axis_interval(right_rect, "y"),
                    ):
                        constraints.append((left_index, right_index))
                elif self._axis_center(left_rect, "y") < self._axis_center(right_rect, "y") and (
                    self._intervals_overlap(self._axis_interval(left_rect, "x"), self._axis_interval(right_rect, "x"))
                    or not self._rects_overlap_on_any_axis(left_rect, right_rect)
                ):
                    constraints.append((left_index, right_index))

        positions = [0] * len(rects)
        for _ in range(len(rects)):
            changed = False
            for before_index, after_index in constraints:
                next_position = positions[before_index] + 1
                if positions[after_index] < next_position:
                    positions[after_index] = next_position
                    changed = True
            if not changed:
                break
        minimum = min(positions) if positions else 0
        return [position - minimum for position in positions]

    @staticmethod
    def _axis_center(rect: tuple[int, int, int, int], axis: str) -> float:
        return rect[0] + rect[2] / 2 if axis == "x" else rect[1] + rect[3] / 2

    @staticmethod
    def _axis_interval(rect: tuple[int, int, int, int], axis: str) -> tuple[int, int]:
        start = rect[0] if axis == "x" else rect[1]
        size = rect[2] if axis == "x" else rect[3]
        return start, start + size

    @staticmethod
    def _intervals_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
        return min(first[1], second[1]) > max(first[0], second[0])

    def _rects_overlap_on_any_axis(self, first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
        return self._intervals_overlap(self._axis_interval(first, "x"), self._axis_interval(second, "x")) or self._intervals_overlap(
            self._axis_interval(first, "y"),
            self._axis_interval(second, "y"),
        )

    def _sync_overlay_geometry(self) -> None:
        if self._current_canvas_size.isEmpty() or not self.isVisible():
            return
        center_global = self.mapToGlobal(QPoint(max(0, self.width() // 2), 0))
        try:
            bar_center_y = self.window().mapToGlobal(QPoint(0, max(0, self.window().height() // 2))).y()
            center_global.setY(bar_center_y)
        except Exception:
            center_global.setY(self.mapToGlobal(QPoint(0, max(0, self.height() // 2))).y())
        x = center_global.x() - int(self._current_canvas_size.width() / 2)
        y = center_global.y() - int(self._current_canvas_size.height() / 2)
        self._overlay.setGeometry(x, y, self._current_canvas_size.width(), self._current_canvas_size.height())
        self._raise_overlay()

    def _raise_overlay(self) -> None:
        if not self._overlay.isVisible():
            return
        self._overlay.raise_()

    def _request_parent_layout_update(self) -> None:
        self.updateGeometry()
        self.parent_button.updateGeometry()
        for layout in (
            self.parent_button.layout(),
            self.parent_widget._workspace_container_layout,
            self.parent_widget.widget_layout,
        ):
            if layout:
                layout.invalidate()
                layout.activate()
        self.parent_widget._workspace_container.updateGeometry()
        self.parent_widget.updateGeometry()
        QTimer.singleShot(0, self.parent_widget._sync_all_layout_preview_overlays)

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
    k_signal_layout_command = pyqtSignal(dict)
    validation_schema = KomorebiWorkspacesConfig
    event_listener = KomorebiEventListener
    _pending_clear_delay_ms = 0
    _focus_diag_sample_delays_ms = (0, 50, 150, 300)
    _title_update_icon_refresh_delay_ms = 30
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
        self._prev_workspace_layout_signatures = []
        self._curr_workspace_layout_signatures = []
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
        self._workspace_focus_events = frozenset([
            KomorebiEvent.CycleFocusWorkspace.value,
            KomorebiEvent.CycleFocusMonitor.value,
            KomorebiEvent.FocusMonitorWorkspaceNumber.value,
            KomorebiEvent.FocusMonitorNumber.value,
            KomorebiEvent.FocusWorkspaceNumber.value,
            KomorebiEvent.ToggleWorkspaceLayer.value,
        ])
        self._update_buttons_event_watchlist = frozenset([
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
        ])
        self._workspace_icon_refresh_events = frozenset([
            KomorebiEvent.ChangeLayout.value,
            KomorebiEvent.ToggleTiling.value,
            KomorebiEvent.ToggleMonocle.value,
            KomorebiEvent.ToggleMaximize.value,
            KomorebiEvent.StackWindow.value,
            KomorebiEvent.UnstackWindow.value,
            KomorebiEvent.CycleStack.value,
            KomorebiEvent.FocusStackWindow.value,
            KomorebiEvent.Unmanage.value,
            "Destroy",
            "Hide",
            "CloseWindow"
        ])
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

        self._layout_command_debounce_timer = QTimer()
        self._layout_command_debounce_timer.setSingleShot(True)
        self._layout_command_debounce_timer.timeout.connect(self._refresh_layout_preview_from_current_state)

        self._register_signals_and_events()

    def _register_signals_and_events(self):
        self.k_signal_connect.connect(self._on_komorebi_connect_event)
        self.k_signal_update.connect(self._on_komorebi_update_event)
        self.k_signal_disconnect.connect(self._on_komorebi_disconnect_event)
        self.k_signal_workspace_pending.connect(self._on_workspace_pending_event)
        self.k_signal_layout_command.connect(self._on_layout_command_event)
        self._event_service.register_event(KomorebiEvent.KomorebiConnect, self.k_signal_connect)
        self._event_service.register_event(KomorebiEvent.KomorebiDisconnect, self.k_signal_disconnect)
        self._event_service.register_event(KomorebiEvent.KomorebiUpdate, self.k_signal_update)
        self._event_service.register_event("komorebi_workspace_pending", self.k_signal_workspace_pending)
        self._event_service.register_event("komorebi_layout_command", self.k_signal_layout_command)
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
            self._event_service.unregister_event("komorebi_layout_command", self.k_signal_layout_command)
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
        self._prev_workspace_layout_signatures = []
        self._curr_workspace_layout_signatures = []
        self._workspace_buttons = []
        self._workspace_focused_tiles = {}
        self._clear_container_layout()

    def _set_workspace_focused_tile(self, workspace_index: int, new_tile) -> None:
        old_tile = self._workspace_focused_tiles.get(workspace_index)
        if old_tile is not None and old_tile != new_tile:
            try:
                old_class = str(old_tile.property("class") or "")
                if " focused" in old_class:
                    old_tile.setProperty("class", old_class.replace(" focused", ""))
                    refresh_widget_style(old_tile)
            except Exception:
                pass
        self._workspace_focused_tiles[workspace_index] = new_tile

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

    def _on_layout_command_event(self, payload: dict) -> None:
        if not self._workspace_app_icons_enabled:
            return
        if not self._komorebi_screen:
            return
        monitor_index = payload.get("monitor_index") if isinstance(payload, dict) else None
        if monitor_index is not None and monitor_index != self._komorebi_screen.get("index"):
            return
        self._refresh_layout_preview_from_current_state()
        self._layout_command_debounce_timer.stop()
        self._layout_command_debounce_timer.start(150)

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
                
                # Check if we should correct Komorebi's native mouse-follows-focus
                if not self._pending_cursor_hwnd and self._mouse_follows_focus_enabled():
                    if self._is_active_monitor():
                        global_hwnd = self._get_global_focused_hwnd()
                        if global_hwnd:
                            QTimer.singleShot(150, lambda h=global_hwnd: self._correct_komorebi_cursor(h))

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
                        QTimer.singleShot(30, self._refresh_all_workspace_icons)
                    if active_workspace_changed:
                        self._workspace_buttons[self._prev_workspace_index].update_icons()
                        self._workspace_buttons[self._curr_workspace_index].update_icons()
                    for i in range(len(self._komorebi_workspaces)):
                        layout_signature_changed = (
                            i < len(self._prev_workspace_layout_signatures)
                            and i < len(self._curr_workspace_layout_signatures)
                            and self._prev_workspace_layout_signatures[i] != self._curr_workspace_layout_signatures[i]
                        )
                        if (
                            self._prev_num_windows_in_workspaces[i] != self._curr_num_windows_in_workspaces[i]
                            or layout_signature_changed
                        ):
                            self._workspace_buttons[i].update_icons()
                        elif event["type"] in [KomorebiEvent.TitleUpdate.value]:
                            hwnd = event["content"][1]["hwnd"]
                            self._queue_title_update_icon_refresh(i, hwnd)
                except (IndexError, TypeError):
                    pass
                QTimer.singleShot(0, self._refresh_all_workspace_icons)

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
                self._curr_workspace_layout_signatures = self._curr_workspace_layout_signatures[
                    : len(self._komorebi_workspaces)
                ] + [()] * (len(self._komorebi_workspaces) - len(self._curr_workspace_layout_signatures))
                self._prev_workspace_layout_signatures = self._curr_workspace_layout_signatures.copy()
                for i in range(len(self._komorebi_workspaces)):
                    windows = self._get_all_windows_in_workspace(i)
                    self._curr_num_windows_in_workspaces[i] = len(windows) if windows else 0
                    self._curr_workspace_layout_signatures[i] = self._get_windows_layout_signature(windows)

                return True
        except TypeError:
            return False

    @staticmethod
    def _get_windows_layout_signature(windows: list[dict] | None) -> tuple:
        signature = []
        for window in windows or []:
            rect = window.get("rect") if isinstance(window, dict) else None
            if isinstance(rect, dict):
                rect_signature = (
                    rect.get("left"),
                    rect.get("top"),
                    rect.get("right"),
                    rect.get("bottom"),
                )
            else:
                rect_signature = None
            signature.append((window.get("hwnd"), rect_signature))
        return tuple(signature)

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
            target_widget = workspace_btn.widget_to_style
            current_classes = str(target_widget.property("class") or "").split()
            if (
                workspace_btn.status != workspace_status
                or "pending" in current_classes
                or workspace_status.lower() not in current_classes
            ):
                workspace_btn.update_and_redraw(workspace_status)
            if is_pending:
                _set_workspace_button_class(
                    target_widget,
                    self._get_workspace_non_active_status(workspace_index),
                    pending=True,
                )
                refresh_widget_style(target_widget)
            workspace_btn.show()
            workspace_btn.update_visible_buttons()
            if hasattr(workspace_btn, "_update_icons_paint"):
                workspace_btn._update_icons_paint()
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
        
        default_label = default_label if default_label and default_label.strip() else ws_name
        active_label = active_label if active_label and active_label.strip() else default_label
        populated_label = populated_label if populated_label and populated_label.strip() else default_label
        
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

    def _sync_all_layout_preview_overlays(self) -> None:
        if not self._workspace_app_icons_enabled:
            return
        for workspace_button in self._workspace_buttons:
            preview_widget = getattr(workspace_button, "preview_widget", None)
            if preview_widget and preview_widget.isVisible():
                preview_widget._sync_overlay_geometry()
                preview_widget._raise_overlay()

    def _refresh_layout_preview_from_current_state(self) -> None:
        if not self._workspace_app_icons_enabled:
            return
        try:
            state = self._komorebic.query_state()
            if not state:
                return
            if self._update_komorebi_state(state):
                self._refresh_all_workspace_icons()
        except Exception:
            logging.exception("Failed to refresh layout preview from komorebi state")

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
        monocle_container = self._komorebic.get_monocle_container(workspace)
        if monocle_container:
            focused_monocle_window = self._komorebic.get_focused_window(monocle_container)
            if focused_monocle_window:
                return [focused_monocle_window]
            return self._komorebic.get_windows(monocle_container)[:1]

        maximized_window = workspace.get("maximized_window")
        if isinstance(maximized_window, dict):
            return [maximized_window]

        containers = self._komorebic.get_containers(workspace, get_monocle=False)
        
        # Calculate Python layout
        try:
            from core.widgets.komorebi.layout_engine import calculate_layout
            import logging
            monitor_state = self._komorebi_screen
            work_area = monitor_state.get("work_area_size")
            if work_area and containers:
                layout_config = workspace.get("layout", {})
                layout_type = layout_config.get("Default", "BSP") if isinstance(layout_config, dict) else layout_config
                layout_flip = workspace.get("layout_flip")
                if not layout_flip:
                    layout_flip = "None"
                    
                if layout_type and layout_type != "Monocle":
                    computed_rects = calculate_layout(
                        layout_type=layout_type,
                        work_area=work_area,
                        num_windows=len(containers),
                        layout_flip=layout_flip,
                        layout_options={}
                    )
                    
                    if computed_rects and len(computed_rects) == len(containers):
                        for i, container in enumerate(containers):
                            c_rect = computed_rects[i]
                            rect_dict = {"left": c_rect["left"], "top": c_rect["top"], "right": c_rect["width"], "bottom": c_rect["height"]}
                            # Override rect for all windows in this container
                            for window in container.get("windows", {}).get("elements", []):
                                window["rect"] = rect_dict
        except Exception as e:
            import logging
            logging.error(f"Failed to calculate logical layout for preview: {e}")

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
        unique_app_keys: set = set()
        icon_entries = []
        focused_hwnd = self._get_workspace_focused_hwnd(workspace_index)
        last_active_hwnd = self._workspace_last_active_hwnd.get(workspace_index)
        
        has_focused = any(w["hwnd"] == focused_hwnd for w in windows_in_workspace)
        
        for index, window in enumerate(windows_in_workspace):
            hwnd = window["hwnd"]
            pixmap = self._get_app_icon(hwnd, workspace_index, unique_app_keys=unique_app_keys)
            if pixmap is None:
                continue
            class_name = f"icon icon-{index + 1}"
            
            is_focused = (hwnd == focused_hwnd)
            is_last_focused = (hwnd == last_active_hwnd and not has_focused)
            
            if is_focused:
                class_name += " focused"
            elif is_last_focused:
                class_name += " last-focused"
                
            icon_entries.append(
                {
                    "hwnd": hwnd,
                    "app_key": self._get_app_key(hwnd),
                    "pixmap": pixmap,
                    "class_name": class_name,
                    "window_rect": window.get("rect"),
                    "focused": is_focused,
                    "last_focused": is_last_focused,
                }
            )
        return icon_entries

    def _get_app_icon(self, hwnd: int, workspace_index: int, ignore_cache: bool = False, unique_app_keys: set | None = None) -> QPixmap | None:
        try:
            if self.config.app_icons.hide_duplicates and unique_app_keys is not None:
                app_key = self._get_app_key(hwnd)
                if app_key and app_key not in unique_app_keys:
                    unique_app_keys.add(app_key)
                elif app_key:
                    return None
                else:
                    process = get_process_info(hwnd)
                    pid = process["pid"]
                    if pid not in unique_app_keys:
                        unique_app_keys.add(pid)
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

    def _is_active_monitor(self) -> bool:
        try:
            focused_monitor_idx = self._komorebi_state["monitors"]["focused"]
            focused_monitor = self._komorebi_state["monitors"]["elements"][focused_monitor_idx]
            return focused_monitor.get("id") == self._screen_hwnd
        except Exception:
            return False

    def _get_global_focused_hwnd(self) -> int | None:
        try:
            focused_monitor_idx = self._komorebi_state["monitors"]["focused"]
            focused_monitor = self._komorebi_state["monitors"]["elements"][focused_monitor_idx]
            focused_ws = self._komorebic.get_focused_workspace(focused_monitor)
            if not focused_ws:
                return None
            focused_window = self._get_current_workspace_focused_window(focused_ws)
            return focused_window.get("hwnd") if focused_window else None
        except Exception:
            return None

    def _correct_komorebi_cursor(self, hwnd: int) -> None:
        if not hwnd:
            return
        try:
            from core.utils.win32.utils import get_window_rect
            import win32api
            
            rect_dict = get_window_rect(hwnd)
            if not rect_dict:
                return
                
            x, y = win32api.GetCursorPos()
            left = rect_dict.get("x", 0)
            top = rect_dict.get("y", 0)
            right = left + rect_dict.get("width", 0)
            bottom = top + rect_dict.get("height", 0)
            
            if left <= x <= right and top <= y <= bottom:
                return
                
            center_x = left + rect_dict.get("width", 0) // 2
            center_y = top + rect_dict.get("height", 0) // 2
            
            _log_workspace_diag(
                "correcting komorebi mouse position: hwnd=%s old_pos=(%s,%s) new_pos=(%s,%s)",
                hwnd, x, y, center_x, center_y
            )
            win32api.SetCursorPos((center_x, center_y))
        except Exception:
            pass

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
                self._komorebic.activate_workspace(self._komorebi_screen["index"], workspace_index)
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
