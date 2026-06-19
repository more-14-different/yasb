from PyQt6.QtCore import pyqtSignal, Qt, QPoint, QTimer
from PyQt6.QtGui import QPainter, QColor, QMouseEvent, QCursor
from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QFrame, QLabel
from core.validation.widgets.yasb.pieces_toggle import PiecesToggleConfig
from core.widgets.base import BaseWidget
import re

class PiecesToggleIcon(QLabel):
    def __init__(self, text, parent_widget, is_top=False):
        super().__init__(text)
        self.parent_widget = parent_widget
        self.is_top = is_top
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        painter.end()
        super().paintEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.is_top:
                self.parent_widget._toggle_time_source()
            else:
                self.parent_widget._run_callback(self.parent_widget.callback_left)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.parent_widget._run_callback(self.parent_widget.callback_middle)
        elif event.button() == Qt.MouseButton.RightButton:
            self.parent_widget._run_callback(self.parent_widget.callback_right)
        event.accept()

class PiecesToggleWidget(BaseWidget):
    validation_schema = PiecesToggleConfig
    
    _state_changed_signal = pyqtSignal(bool, str)
    
    def __init__(self, config: PiecesToggleConfig):
        super().__init__(class_name=f"pieces-toggle-widget {config.class_name}")
        self.config = config
        self._is_on = True
        self._time_is_on = True # True = OBS time, False = Boot time
        
        # Override the base horizontal layout with a vertical layout
        self._widget_container_layout = QVBoxLayout()
        self._widget_container_layout.setSpacing(0) # Let alignment dictate spacing
        self._widget_container_layout.setContentsMargins(0, 0, 0, 0)
        
        self._widget_container = QFrame()
        self._widget_container.setLayout(self._widget_container_layout)
        self._widget_container.setProperty("class", "widget-container")
        self.widget_layout.addWidget(self._widget_container)
        
        self._time_widgets = []
        self._time_widgets_alt = []
        self._pieces_widgets = []
        self._pieces_widgets_alt = []
        
        self._build_two_toggles()

        
        self.register_callback("toggle_pieces", self._toggle_pieces)
        
        # We handle clicking via mouseReleaseEvent based on Y coordinate
        self.callback_left = self.config.callbacks.on_left
        self.callback_right = self.config.callbacks.on_right
        self.callback_middle = self.config.callbacks.on_middle
        
        self._event_service.register_event("pieces_widget_state_changed", self._state_changed_signal)
        self._state_changed_signal.connect(self._on_state_changed)
        
        self._update_labels()
        
    def _build_two_toggles(self):
        def process_content(content: str, is_alt: bool = False, is_top: bool = False) -> list[QLabel]:
            label_parts = re.split(r"(<span.*?>.*?</span>)", content)
            label_parts = [part for part in label_parts if part]
            widgets: list[QLabel] = []
            for part in label_parts:
                part = part.strip()
                if not part:
                    continue
                is_icon = "<span" in part and "</span>" in part
                if is_icon:
                    class_name = re.search(r'class=(["\'])([^"\']+?)\1', part)
                    class_result = class_name.group(2) if class_name else "icon"
                    icon = re.sub(r"<span.*?>|</span>", "", part).strip()
                    label = PiecesToggleIcon(icon, self, is_top)
                    label.setProperty("class", class_result)
                else:
                    label = QLabel(part)
                    label.setProperty("class", "label alt" if is_alt else "label")
                
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                widgets.append(label)
                if is_alt:
                    label.hide()
                else:
                    label.show()
            return widgets

        # Top toggle (Time Source)
        top_frame = QFrame()
        top_layout = QHBoxLayout(top_frame)
        top_layout.setSpacing(0)
        top_layout.setContentsMargins(0, 0, -4, 0)
        top_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._time_widgets = process_content(self.config.label, False, is_top=True)
        self._time_widgets_alt = process_content(self.config.label_alt, True, is_top=True)
        for w in self._time_widgets + self._time_widgets_alt:
            top_layout.addWidget(w)
        self._widget_container_layout.addWidget(top_frame, alignment=Qt.AlignmentFlag.AlignTop)

        # Bottom toggle (Pieces Diagram)
        bottom_frame = QFrame()
        bottom_layout = QHBoxLayout(bottom_frame)
        bottom_layout.setSpacing(0)
        bottom_layout.setContentsMargins(0, 0, -4, 0)
        bottom_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._pieces_widgets = process_content(self.config.label, False, is_top=False)
        self._pieces_widgets_alt = process_content(self.config.label_alt, True, is_top=False)
        for w in self._pieces_widgets + self._pieces_widgets_alt:
            bottom_layout.addWidget(w)
        self._widget_container_layout.addWidget(bottom_frame, alignment=Qt.AlignmentFlag.AlignBottom)

    def _handle_mouse_events(self, event):
        # Ignore clicks on the capsule outside the icons so it doesn't trigger BaseWidget's callback
        event.ignore()

    def _toggle_time_source(self):
        self._time_is_on = not self._time_is_on
        self._event_service.emit_event("pieces_time_source_changed", self._time_is_on, self.screen_name)
        self._update_labels()

    def _toggle_pieces(self):
        # Route the toggle request only to the pieces widget on the same screen.
        self._event_service.emit_event("toggle_pieces_widget", self.screen_name)
        
    def _on_state_changed(self, is_on: bool, screen_name: str):
        if screen_name != self.screen_name:
            return

        self._is_on = is_on
        self._update_labels()
        
    def _update_labels(self):
        for widget in self._time_widgets:
            widget.setVisible(self._time_is_on)
        for widget in self._time_widgets_alt:
            widget.setVisible(not self._time_is_on)
            
        for widget in self._pieces_widgets:
            widget.setVisible(self._is_on)
        for widget in self._pieces_widgets_alt:
            widget.setVisible(not self._is_on)

