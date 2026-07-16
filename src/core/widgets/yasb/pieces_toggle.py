from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from core.validation.widgets.yasb.pieces_toggle import PiecesToggleConfig
from core.widgets.base import BaseWidget
from core.widgets.yasb.pieces_time_source import TimeSource


class _ClickableLayerLabel(QLabel):
    """Icon label with a nearly transparent painted layer for hit testing."""

    def __init__(self, text: str, mouse_handler: Callable[[QMouseEvent], None]):
        super().__init__(text)
        self._mouse_handler = mouse_handler
        self.setMouseTracking(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        painter.end()
        super().paintEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if self.rect().contains(event.pos()):
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.rect().contains(event.pos()):
            self._mouse_handler(event)
            event.accept()
            return

        super().mouseReleaseEvent(event)


class PiecesToggleWidget(BaseWidget):
    validation_schema = PiecesToggleConfig
    
    _state_changed_signal = pyqtSignal(bool, str)
    
    def __init__(self, config: PiecesToggleConfig):
        super().__init__(class_name=f"pieces-toggle-widget {config.class_name}")
        self.config = config
        self._is_on = True
        self._time_source = TimeSource.YOUTUBE_LIVESTREAM
        
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
        
        # The icon labels handle mouse release events themselves. This avoids the
        # outer capsule-shaped container being clickable outside the icon layer.
        self.callback_left = self.config.callbacks.on_left
        self.callback_right = self.config.callbacks.on_right
        self.callback_middle = self.config.callbacks.on_middle
        self.mouseReleaseEvent = self._ignore_container_mouse_release
        
        self._event_service.register_event("pieces_widget_state_changed", self._state_changed_signal)
        self._state_changed_signal.connect(self._on_state_changed)
        
        self._update_labels()
        
    def _build_two_toggles(self):
        from core.utils.utilities import parse_label_template
        def process_content(
            content: str,
            mouse_handler: Callable[[QMouseEvent], None],
            is_alt: bool = False,
        ) -> list[QLabel]:
            parsed_parts = parse_label_template(content)
            widgets: list[QLabel] = []
            for parsed in parsed_parts:
                is_icon = parsed["is_icon"]
                text = parsed["text"]
                class_name = parsed["class_name"]

                if is_icon:
                    label = _ClickableLayerLabel(text, mouse_handler)
                    label.setProperty("class", class_name)
                else:
                    label = _ClickableLayerLabel(text, mouse_handler)
                    label.setProperty("class", "label alt" if is_alt else "label")
                
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                widgets.append(label)
                if is_alt:
                    label.hide()
                else:
                    label.show()
            return widgets

        def create_toggle_row(mouse_handler: Callable[[QMouseEvent], None], alignment: Qt.AlignmentFlag) -> tuple[list[QLabel], list[QLabel]]:
            frame = QFrame()
            layout = QHBoxLayout(frame)
            layout.setSpacing(0)
            layout.setContentsMargins(0, 0, -4, 0)
            layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            
            widgets = process_content(self.config.label, mouse_handler)
            widgets_alt = process_content(self.config.label_alt, mouse_handler, True)
            
            for w in widgets + widgets_alt:
                layout.addWidget(w)
            self._widget_container_layout.addWidget(frame, alignment=alignment)
            return widgets, widgets_alt

        # Top toggle (Time Source)
        self._time_widgets, self._time_widgets_alt = create_toggle_row(
            self._handle_time_mouse_event, Qt.AlignmentFlag.AlignTop
        )

        # Bottom toggle (Pieces Diagram)
        self._pieces_widgets, self._pieces_widgets_alt = create_toggle_row(
            self._handle_pieces_mouse_event, Qt.AlignmentFlag.AlignBottom
        )

    def _ignore_container_mouse_release(self, event: QMouseEvent):
        event.ignore()

    def _handle_time_mouse_event(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_time_source()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self._run_callback(self.callback_middle)
        elif event.button() == Qt.MouseButton.RightButton:
            self._run_callback(self.callback_right)

    def _handle_pieces_mouse_event(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._run_callback(self.callback_left)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self._run_callback(self.callback_middle)
        elif event.button() == Qt.MouseButton.RightButton:
            self._run_callback(self.callback_right)

    def _toggle_time_source(self):
        self._time_source = (
            TimeSource.MACHINE_SESSION
            if self._time_source is TimeSource.YOUTUBE_LIVESTREAM
            else TimeSource.YOUTUBE_LIVESTREAM
        )
        self._event_service.emit_event(
            "pieces_time_source_changed",
            self._time_source.value,
            self.screen_name,
        )
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
        uses_livestream = self._time_source is TimeSource.YOUTUBE_LIVESTREAM
        for widget in self._time_widgets:
            widget.setVisible(uses_livestream)
        for widget in self._time_widgets_alt:
            widget.setVisible(not uses_livestream)
            
        for widget in self._pieces_widgets:
            widget.setVisible(self._is_on)
        for widget in self._pieces_widgets_alt:
            widget.setVisible(not self._is_on)
