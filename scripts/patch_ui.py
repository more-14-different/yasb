import re

with open('src/core/widgets/yasb/pieces_density.py', 'r', encoding='utf-8') as f:
    code = f.read()

# --- 1. FetchWorker Logic ---
code = code.replace(
    "data_fetched = pyqtSignal(list, bool, float, str, bool)",
    "data_fetched = pyqtSignal(list, bool, float, float, str, bool)"
)

code = code.replace(
    "def __init__(self, config: PiecesDensityConfig, use_obs_time: bool, known_start_time: float = 0.0, last_streaming_time: float = 0.0, session_override: float = 0.0, force_session: bool = False):",
    "def __init__(self, config: PiecesDensityConfig, use_obs_time: bool, known_start_time: float = 0.0, last_streaming_time: float = 0.0, session_override: float = 0.0, force_session: bool = False, session_end_bound: float = 0.0):\n        self.session_end_bound = session_end_bound"
)
code = code.replace(
    "super().__init__()",
    "super().__init__()\n        self.session_end_bound = 0.0"
)

fetch_logic_old = """            if self.force_session and self.session_override > 0:
                is_streaming = True
                stream_start_time = self.session_override
                total_duration_sec = time.time() - stream_start_time
                err = \"\""""
fetch_logic_new = """            if self.force_session and self.session_override > 0:
                is_streaming = False
                stream_start_time = self.session_override
                try:
                    import sqlite3
                    conn = sqlite3.connect(self.config.db_path)
                    cursor = conn.cursor()
                    cursor.execute("SELECT MAX(timestamp) FROM events WHERE timestamp >= ? AND timestamp < ?", (stream_start_time, self.session_end_bound if self.session_end_bound > 0 else time.time()))
                    row = cursor.fetchone()
                    if row and row[0]:
                        total_duration_sec = max(0, row[0] - stream_start_time)
                    else:
                        total_duration_sec = 0
                    conn.close()
                except Exception as e:
                    total_duration_sec = 0
                err = \"\""""
code = code.replace(fetch_logic_old, fetch_logic_new)

code = code.replace(
    "self.data_fetched.emit([], is_streaming, 0.0, err, self.use_obs_time)",
    "self.data_fetched.emit([], is_streaming, 0.0, 0.0, err, self.use_obs_time)"
)
code = code.replace(
    "self.data_fetched.emit(buckets, True, stream_start_time, \"\", self.use_obs_time)",
    "self.data_fetched.emit(buckets, True, stream_start_time, total_duration_sec, \"\", self.use_obs_time)"
)

# --- 2. ControlsOverlay to ControlsOverlayLeft & Right ---
controls_class_pattern = re.compile(r'class ControlsOverlay\(QFrame\):.*?class DensityOverlay', re.DOTALL)

new_classes = """class ControlsOverlayBase(QFrame):
    def __init__(self, widget: "PiecesDensityWidget", parent=None):
        super().__init__(parent)
        self.widget = widget
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(2)
        
        self.style_str = \"\"\"
            QPushButton {
                background-color: rgba(20, 20, 20, 100);
                color: rgba(255, 255, 255, 200);
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 4px;
                padding: 1px 4px;
                margin: 0px;
                font-family: Consolas, monospace;
            }
            QPushButton:hover {
                background-color: rgba(60, 60, 60, 150);
            }
            QPushButton:disabled {
                background-color: rgba(10, 10, 10, 50);
                color: rgba(255, 255, 255, 30);
                border: 1px solid rgba(255, 255, 255, 10);
            }
            QLabel {
                color: rgba(255, 255, 255, 200);
                font-size: 10px;
                background-color: rgba(20, 20, 20, 100);
                border-radius: 4px;
                padding: 1px 4px;
                margin: 0px;
                font-family: Consolas, monospace;
            }
            QLabel#DateLabel {
                background-color: rgba(60, 40, 20, 100);
            }
        \"\"\"

class ControlsOverlayLeft(ControlsOverlayBase):
    def __init__(self, widget: "PiecesDensityWidget", parent=None):
        super().__init__(widget, parent)
        self.btn_prev = QPushButton("<")
        self.btn_next = QPushButton(">")
        self.lbl_date = QLabel()
        self.lbl_date.setObjectName("DateLabel")
        self.lbl_time = QLabel("--:--")
        
        self.btn_prev.setStyleSheet(self.style_str)
        self.btn_next.setStyleSheet(self.style_str)
        self.lbl_date.setStyleSheet(self.style_str)
        self.lbl_time.setStyleSheet(self.style_str)
        
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.layout.addWidget(self.btn_prev)
        self.layout.addWidget(self.lbl_date)
        self.layout.addWidget(self.lbl_time)
        self.layout.addWidget(self.btn_next)
        
        self.btn_prev.clicked.connect(self.widget._prev_session)
        self.btn_next.clicked.connect(self.widget._next_session)
        self.update_buttons()

    def update_buttons(self):
        sessions = self.widget._session_manager.sessions
        idx = self.widget._session_offset
        if not sessions:
            self.lbl_date.hide()
            self.lbl_time.setText("No Session")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return
            
        real_idx = len(sessions) - 1 + idx
        self.btn_prev.setEnabled(real_idx > 0)
        self.btn_next.setEnabled(idx < 0)
        
        ts = sessions[real_idx]
        dt = datetime.fromtimestamp(ts)
        now = datetime.now()
        
        if now.year != dt.year:
            date_str = dt.strftime("%y-%m-%d")
        elif now.month != dt.month or now.day != dt.day:
            date_str = dt.strftime("%m-%d")
        else:
            date_str = ""
            
        if date_str:
            self.lbl_date.setText(date_str)
            self.lbl_date.show()
        else:
            self.lbl_date.hide()
            
        self.lbl_time.setText(dt.strftime("%H:%M"))
        self.adjustSize()

class ControlsOverlayRight(ControlsOverlayBase):
    def __init__(self, widget: "PiecesDensityWidget", parent=None):
        super().__init__(widget, parent)
        self.btn_prev = QPushButton("<")
        self.btn_next = QPushButton(">")
        self.lbl_days = QLabel()
        self.lbl_days.setObjectName("DateLabel")
        self.lbl_duration = QLabel("--:--")
        
        self.btn_prev.setStyleSheet(self.style_str)
        self.btn_next.setStyleSheet(self.style_str)
        self.lbl_days.setStyleSheet(self.style_str)
        self.lbl_duration.setStyleSheet(self.style_str)
        
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.layout.addWidget(self.btn_prev)
        self.layout.addWidget(self.lbl_days)
        self.layout.addWidget(self.lbl_duration)
        self.layout.addWidget(self.btn_next)
        
        self.btn_prev.clicked.connect(self.widget._prev_session)
        self.btn_next.clicked.connect(self.widget._next_session)
        self.update_buttons(0.0)

    def update_buttons(self, duration_sec: float):
        sessions = self.widget._session_manager.sessions
        idx = self.widget._session_offset
        if not sessions:
            self.lbl_days.hide()
            self.lbl_duration.setText("--:--")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return
            
        real_idx = len(sessions) - 1 + idx
        self.btn_prev.setEnabled(real_idx > 0)
        self.btn_next.setEnabled(idx < 0)
        
        days = int(duration_sec // 86400)
        rem = duration_sec % 86400
        hours = int(rem // 3600)
        minutes = int((rem % 3600) // 60)
        
        if days > 0:
            self.lbl_days.setText(f"+{days}d")
            self.lbl_days.show()
        else:
            self.lbl_days.hide()
            
        self.lbl_duration.setText(f"{hours:02d}:{minutes:02d}")
        self.adjustSize()

class DensityOverlay"""

code = controls_class_pattern.sub(new_classes, code)

# Replacements
code = code.replace("self._controls = ControlsOverlay(self)", "self._controls_left = ControlsOverlayLeft(self)\\n        self._controls_right = ControlsOverlayRight(self)")

# _place_overlay_below_bar
old_place = """        try:
            overlay_hwnd = int(self._overlay.winId())
            controls_hwnd = int(self._controls.winId())
            bar_hwnd = int(bar_window.winId())
        except RuntimeError:
            return

        # Place ControlsOverlay below the yasb bar
        SetWindowPos(controls_hwnd, bar_hwnd, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
        # Place DensityOverlay below the ControlsOverlay (so ControlsOverlay is clickable and sits above DensityOverlay)
        SetWindowPos(overlay_hwnd, controls_hwnd, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)"""
new_place = """        try:
            overlay_hwnd = int(self._overlay.winId())
            controls_l_hwnd = int(self._controls_left.winId())
            controls_r_hwnd = int(self._controls_right.winId())
            bar_hwnd = int(bar_window.winId())
        except RuntimeError:
            return

        SetWindowPos(controls_l_hwnd, bar_hwnd, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
        SetWindowPos(controls_r_hwnd, controls_l_hwnd, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
        SetWindowPos(overlay_hwnd, controls_r_hwnd, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)"""
code = code.replace(old_place, new_place)


# FetchWorker init
old_fetch_init = """        override_time = 0.0
        sessions = self._session_manager.sessions
        if sessions:
            real_idx = len(sessions) - 1 + self._session_offset
            if 0 <= real_idx < len(sessions):
                override_time = sessions[real_idx]
                
        force_session = self._session_offset < 0

        self._worker = FetchWorker(self.config, self._use_obs_time, self._stream_start_time, self._last_streaming_time, override_time, force_session)"""
new_fetch_init = """        override_time = 0.0
        end_bound = 0.0
        sessions = self._session_manager.sessions
        if sessions:
            real_idx = len(sessions) - 1 + self._session_offset
            if 0 <= real_idx < len(sessions):
                override_time = sessions[real_idx]
                if real_idx + 1 < len(sessions):
                    end_bound = sessions[real_idx + 1]
                
        force_session = self._session_offset < 0

        self._worker = FetchWorker(self.config, self._use_obs_time, self._stream_start_time, self._last_streaming_time, override_time, force_session, end_bound)"""
code = code.replace(old_fetch_init, new_fetch_init)


# data_fetched signature
code = code.replace("def _on_data_fetched(self, buckets: list[int], is_streaming: bool, start_time: float, error_msg: str, was_obs_time: bool):", "def _on_data_fetched(self, buckets: list[int], is_streaming: bool, start_time: float, total_duration_sec: float, error_msg: str, was_obs_time: bool):")


# buttons and visibility
code = code.replace("self._controls.update_buttons()", "self._controls_left.update_buttons()\\n        self._controls_right.update_buttons(0.0)")
code = code.replace("self._controls.show()", "self._controls_left.show()\\n                self._controls_right.show()")
code = code.replace("self._controls.hide()", "self._controls_left.hide()\\n                self._controls_right.hide()")
code = code.replace("self._controls_left.hide()\\n                self._controls_right.hide()\\n        else:\\n            self._show_overlay_below_bar()\\n            self._controls.show()", "self._controls_left.hide()\\n                self._controls_right.hide()\\n        else:\\n            self._show_overlay_below_bar()\\n            self._controls_left.show()\\n            self._controls_right.show()")
code = code.replace("bar_window.opacity_tick.connect(self._controls.setWindowOpacity)", "bar_window.opacity_tick.connect(self._controls_left.setWindowOpacity)\\n                bar_window.opacity_tick.connect(self._controls_right.setWindowOpacity)")

# update overlay geometry
old_geo = """        # Ensure it fits the content perfectly
        self._controls.adjustSize()
        cw = self._controls.width()
        ch = self._controls.height()
        
        # Place at bottom right corner, inside the overlay
        controls_x = x + w - cw - 5
        controls_y = y + h - ch - 5
        self._controls.setGeometry(controls_x, controls_y, cw, ch)"""
new_geo = """        self._controls_left.adjustSize()
        cw_l = self._controls_left.width()
        ch_l = self._controls_left.height()
        self._controls_left.setGeometry(x + 5, y + h - ch_l - 5, cw_l, ch_l)

        self._controls_right.adjustSize()
        cw_r = self._controls_right.width()
        ch_r = self._controls_right.height()
        self._controls_right.setGeometry(x + w - cw_r - 5, y + h - ch_r - 5, cw_r, ch_r)"""
code = code.replace(old_geo, new_geo)

code = code.replace(
    "self._controls_right.update_buttons(0.0)\\n\\n        self._overlay.buckets = buckets",
    "self._controls_left.update_buttons()\\n        self._controls_right.update_buttons(total_duration_sec)\\n\\n        self._overlay.buckets = buckets"
)

with open('src/core/widgets/yasb/pieces_density.py', 'w', encoding='utf-8') as f:
    f.write(code)
