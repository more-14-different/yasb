from PyQt6.QtCore import pyqtSignal
from core.validation.widgets.yasb.pieces_toggle import PiecesToggleConfig
from core.widgets.base import BaseWidget

class PiecesToggleWidget(BaseWidget):
    validation_schema = PiecesToggleConfig
    
    _state_changed_signal = pyqtSignal(bool)
    
    def __init__(self, config: PiecesToggleConfig):
        super().__init__(class_name=f"pieces-toggle-widget {config.class_name}")
        self.config = config
        self._is_on = True
        
        self._init_container()
        self.build_widget_label(
            self.config.label, self.config.label_alt
        )
        
        self.register_callback("toggle_pieces", self._toggle_pieces)
        
        self.callback_left = self.config.callbacks.on_left
        self.callback_right = self.config.callbacks.on_right
        self.callback_middle = self.config.callbacks.on_middle
        
        self._event_service.register_event("pieces_widget_state_changed", self._state_changed_signal)
        self._state_changed_signal.connect(self._on_state_changed)
        
        self._update_label()
        
    def _toggle_pieces(self):
        # Emit event to tell PiecesDensityWidget to toggle
        self._event_service.emit_event("toggle_pieces_widget")
        
    def _on_state_changed(self, is_on: bool):
        self._is_on = is_on
        self._update_label()
        
    def _update_label(self):
        for widget in self._widgets:
            widget.setVisible(self._is_on)
        for widget in self._widgets_alt:
            widget.setVisible(not self._is_on)
