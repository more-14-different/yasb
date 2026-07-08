import logging
import time
import math
import os
import sqlite3
import json
from datetime import datetime

from PyQt6.QtCore import QTimer, Qt, QPointF, QRectF, QThread, pyqtSignal, QEvent, QPoint
from PyQt6.QtGui import QPainter, QPainterPath, QLinearGradient, QColor, QBrush, QCursor, QPen
from PyQt6.QtWidgets import QFrame, QToolTip, QPushButton, QHBoxLayout, QLabel
from win32con import SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE

from core.utils.win32.bindings import SetWindowPos

from core.widgets.base import BaseWidget
from core.validation.widgets.yasb.pieces_density import PiecesDensityConfig

# Try importing the required packages
try:
    import obsws_python as obs
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False
    logging.warning(
        "obsws-python not installed. OBS stream tracking will fail.")


def parse_color(color_str: str) -> QColor:
    """Safely parse a color string, including rgba(...) into a QColor."""
    color_str = color_str.strip()
    if color_str.startswith("rgba(") and color_str.endswith(")"):
        parts = color_str[5:-1].split(',')
        if len(parts) == 4:
            try:
                r = int(parts[0].strip())
                g = int(parts[1].strip())
                b = int(parts[2].strip())
                a = float(parts[3].strip())
                return QColor(r, g, b, int(a * 255))
            except ValueError:
                pass
    return QColor(color_str)


class SessionManager:
    """Manages the history of session start times, separated by source type."""

    _MIN_VALID_TIMESTAMP = 1_000_000_000  # ~2001-09-09, rejects epoch-zero

    def __init__(self):
        self.state_file = os.path.expanduser(
            "~/.config/yasb/obs_sessions.json")
        self._data: dict[str, list[float]] = {"obs": [], "boot": []}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and "obs" in raw:
                    self._data = {"obs": raw.get("obs", []), "boot": raw.get("boot", [])}
                elif isinstance(raw, list):
                    # Auto-migrate old flat-array format: discard invalid entries,
                    # keep all valid ones under "obs" (legacy default)
                    valid = [t for t in raw if isinstance(t, (int, float)) and t > self._MIN_VALID_TIMESTAMP]
                    self._data = {"obs": valid, "boot": []}
                    self._save()
                    logging.info(f"Migrated {len(valid)} sessions from legacy format")
        except Exception as e:
            logging.error(f"Failed to load sessions: {e}")
            self._data = {"obs": [], "boot": []}
        self._cleanup()

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(self._data, f)
        except Exception as e:
            logging.error(f"Failed to save sessions: {e}")

    def _cleanup(self):
        now = time.time()
        changed = False
        for key in ("obs", "boot"):
            filtered = [s for s in self._data[key] if now - s <= 129600]
            if len(filtered) != len(self._data[key]):
                self._data[key] = filtered
                changed = True
        if changed:
            self._save()

    def get_sessions(self, use_obs: bool) -> list[float]:
        """Return the session list for the given time source."""
        return self._data["obs" if use_obs else "boot"]

    def record_start_time(self, start_time: float, use_obs: bool):
        if start_time <= self._MIN_VALID_TIMESTAMP:
            return  # Reject epoch-zero and other nonsensical timestamps
        sessions = self._data["obs" if use_obs else "boot"]
        if not sessions:
            sessions.append(start_time)
            self._save()
            return
        last_time = sessions[-1]
        if abs(start_time - last_time) > 30:
            sessions.append(start_time)
            self._save()


class ControlsOverlayBase(QFrame):
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

        self.style_str = """
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
        """


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
        sessions = self.widget._session_manager.get_sessions(self.widget._use_obs_time)
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
        sessions = self.widget._session_manager.get_sessions(self.widget._use_obs_time)
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


class DensityOverlay(QFrame):
    """The actual floating overlay window that draws the density heatmap."""

    def __init__(self, widget: "PiecesDensityWidget", config: PiecesDensityConfig):
        super().__init__()
        self.session_end_bound = 0.0
        self.widget = widget
        self.config = config

        # Set up window flags for a floating overlay that sits below the bar but above desktop
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Internal state
        self.stream_start_time = 0
        self.buckets: list[int] = []
        self.is_streaming = False
        self.error_msg = ""
        self.hover_idx = None

    def paintEvent(self, event):
        if not self.is_streaming:
            return

        w = self.width()
        h = self.height()

        if w <= 0 or h <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.error_msg:
            # Draw text at 3/4 of the height
            painter.setPen(QColor(255, 255, 255, 200))
            font = painter.font()
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(QRectF(0, h * 0.75, w, 20),
                             Qt.AlignmentFlag.AlignCenter, self.error_msg)
            return

        n = len(self.buckets)
        if self.hover_idx is not None and self.hover_idx >= n:
            self.hover_idx = None

        step_x = w / (n - 1) if n > 1 else w

        # Draw Hover Highlight (±5 mins)
        if self.hover_idx is not None:
            x_min = max(0, self.hover_idx - 5) * step_x
            x_max = min(n - 1, self.hover_idx + 5) * step_x
            # Faint background for the highlighted section
            painter.fillRect(QRectF(x_min, 0, x_max - x_min, h),
                             QColor(255, 255, 255, 20))

        # Create gradient
        gradient = QLinearGradient(0, h, 0, 0)

        gradient.setColorAt(0.0, parse_color(self.config.color_low))
        gradient.setColorAt(0.5, parse_color(self.config.color_mid))
        gradient.setColorAt(1.0, parse_color(self.config.color_high))

        path = QPainterPath()
        path.moveTo(0, h)

        max_val = max(self.buckets) if self.buckets else 0

        # Normalize: Highest point reaches full height
        y_scale = h / max_val if max_val > 0 else 0

        # Draw smooth curve
        points = []
        if n > 1 and max_val > 0:
            points = [QPointF(i * step_x, h - (self.buckets[i] * y_scale))
                      for i in range(n)]

            path.lineTo(points[0])
            for i in range(n - 1):
                p1 = points[i]
                p2 = points[i + 1]
                # Bezier control points for smooth wave
                cp1 = QPointF((p1.x() + p2.x()) / 2, p1.y())
                cp2 = QPointF((p1.x() + p2.x()) / 2, p2.y())
                path.cubicTo(cp1, cp2, p2)
        else:
            val_y = h - 5
            path.lineTo(0, val_y)
            path.lineTo(w, val_y)

        path.lineTo(w, h)
        path.closeSubpath()

        painter.fillPath(path, QBrush(gradient))

        # Hover stroke highlight (solid bright curve over the hovered ±5min section)
        if self.hover_idx is not None and n > 1 and max_val > 0 and len(points) == n:
            hl_path = QPainterPath()
            start_i = max(0, self.hover_idx - 5)
            end_i = min(n - 1, self.hover_idx + 5)
            hl_path.moveTo(points[start_i])
            for i in range(start_i, end_i):
                p1 = points[i]
                p2 = points[i + 1]
                cp1 = QPointF((p1.x() + p2.x()) / 2, p1.y())
                cp2 = QPointF((p1.x() + p2.x()) / 2, p2.y())
                hl_path.cubicTo(cp1, cp2, p2)

            pen = QPen(QColor(255, 255, 255, 220))
            pen.setWidthF(1.5)
            painter.strokePath(hl_path, pen)

        # Draw Ruler
        if n > 1:
            font = painter.font()
            font.setPointSize(8)
            painter.setFont(font)

            for i in range(n):
                ts = self.stream_start_time + i * 60
                dt = datetime.fromtimestamp(ts)

                is_hovered = False
                if self.hover_idx is not None and (self.hover_idx - 5 <= i <= self.hover_idx + 5):
                    is_hovered = True

                # Determine which minor ticks to show based on duration
                if n > 720:
                    is_minor_tick = (dt.minute == 30)
                else:
                    is_minor_tick = (dt.minute % 10 == 0)

                # Major tick on the hour (e.g. 14:00)
                if dt.minute == 0:
                    pen_major = QPen(
                        QColor(255, 255, 255, 255 if is_hovered else 150))
                    pen_major.setWidthF(1.5 if is_hovered else 1.5)
                    painter.setPen(pen_major)

                    x = i * step_x
                    painter.drawLine(QPointF(x, h), QPointF(x, h - 10))
                    # draw text slightly above
                    painter.setPen(
                        QColor(255, 255, 255, 255 if is_hovered else 200))
                    painter.drawText(QPointF(x + 2, h - 12),
                                     dt.strftime("%H:00"))
                elif is_minor_tick:
                    pen_minor = QPen(
                        QColor(255, 255, 255, 200 if is_hovered else 60))
                    pen_minor.setWidthF(1.5 if is_hovered else 1.0)
                    painter.setPen(pen_minor)

                    x = i * step_x
                    painter.drawLine(QPointF(x, h), QPointF(x, h - 5))


class FetchWorker(QThread):
    # buckets, is_streaming, start_time, error_msg, was_obs_time
    data_fetched = pyqtSignal(list, bool, float, float, str, bool)

    def __init__(self, config: PiecesDensityConfig, use_obs_time: bool, known_start_time: float = 0.0, last_streaming_time: float = 0.0, session_override: float = 0.0, force_session: bool = False, session_end_bound: float = 0.0):
        self.session_end_bound = session_end_bound
        super().__init__()
        self.session_end_bound = 0.0
        self.config = config
        self.use_obs_time = use_obs_time
        self.known_start_time = known_start_time
        self.last_streaming_time = last_streaming_time
        self.session_override = session_override
        self.force_session = force_session
        self._is_running = True

    def _get_obs_time(self) -> tuple[bool, float, float, str]:
        """Returns (is_streaming, start_time, duration, error_msg)"""
        obs_client = None
        try:
            obs_client = obs.ReqClient(
                host=self.config.obs_host,
                port=self.config.obs_port,
                password=self.config.obs_password,
                timeout=5
            )
            status = obs_client.get_stream_status()
        except Exception as e:
            # When OBS is not connected, we keep is_streaming=True with an error message
            # so the widget stays visible but shows the error.
            return True, 0.0, 0.0, f"Waiting for OBS Connection ({str(e)})"
        finally:
            if obs_client is not None:
                try:
                    obs_client.disconnect()
                except Exception:
                    pass

        is_streaming = getattr(status, 'output_active', False) or getattr(
            status, 'output_reconnecting', False)
        if not is_streaming:
            return False, 0.0, 0.0, "Waiting for OBS Stream to start..."

        duration_str = getattr(status, 'output_timecode', "")
        total_duration_sec = 0
        if duration_str:
            parts = duration_str.split(':')
            if len(parts) >= 3:
                try:
                    total_duration_sec = int(
                        parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2].split('.')[0])
                except ValueError:
                    pass

        if total_duration_sec > 0:
            stream_start_time = time.time() - total_duration_sec

            # Prevent 1-2 second jitter caused by OBS integer timecode vs time.time() float
            if self.known_start_time > 0 and abs(stream_start_time - self.known_start_time) <= 2.0:
                stream_start_time = self.known_start_time
        else:
            stream_start_time = time.time()

        total_duration_sec = time.time() - stream_start_time
        return True, stream_start_time, total_duration_sec, ""

    def _get_boot_time(self) -> tuple[bool, float, float, str]:
        """Returns (is_streaming, start_time, duration, error_msg)"""
        try:
            import psutil
            stream_start_time = psutil.boot_time()
            return True, stream_start_time, time.time() - stream_start_time, ""
        except Exception as e:
            return True, 0.0, 0.0, f"Waiting for Boot Time ({str(e)})"

    def run(self):
        if not HAS_DEPS:
            return

        try:
            # 1. Determine Stream Start Time & Duration based on selected source
            if self.force_session and self.session_override > 0:
                is_streaming = True  # Treat forced session as currently live
                stream_start_time = self.session_override
                total_duration_sec = time.time() - stream_start_time
                err = ""
            else:
                if self.use_obs_time:
                    is_streaming, stream_start_time, total_duration_sec, err = self._get_obs_time()
                else:
                    is_streaming, stream_start_time, total_duration_sec, err = self._get_boot_time()

                if is_streaming and self.session_override > 0:
                    # Snapping to known session to prevent OBS drop/reconnect resets
                    if abs(stream_start_time - self.session_override) < 900:
                        stream_start_time = self.session_override
                        total_duration_sec = time.time() - stream_start_time

            if err:
                self.data_fetched.emit(
                    [], is_streaming, 0.0, 0.0, err, self.use_obs_time)
                return

            if not is_streaming:
                self.data_fetched.emit([], False, 0.0, "", self.use_obs_time)
                return

            # Honour cancellation request before the SQLite query
            if not self._is_running:
                return

            # Safeguard: cap maximum duration to 24 hours (86400 seconds)
            if total_duration_sec > 86400:
                total_duration_sec = 86400
                stream_start_time = time.time() - 86400

            # 2. Raw Bucket sampling (1 min intervals)
            bucket_interval = 60
            num_buckets = max(
                math.ceil(total_duration_sec / bucket_interval), 1)
            raw_buckets = [0] * num_buckets

            # 3. Query the local Pieces OS sqlite file
            localappdata = os.environ.get("LOCALAPPDATA", "")
            db_path = os.path.join(
                localappdata,
                "Mesh Intelligent Technologies, Inc",
                "Pieces OS",
                "com.pieces.os",
                "production",
                "Pieces",
                "vector_db",
                "workstreamEvents.sqlite"
            )

            if not os.path.exists(db_path):
                self.data_fetched.emit(
                    [], True, 0.0, f"Pieces DB missing at: {db_path}", self.use_obs_time)
                return

            # Connect in read-only mode
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            try:
                c = conn.cursor()
                c.execute("SELECT created_at FROM vectors WHERE created_at >= ?", (int(
                    stream_start_time),))
                for row in c.fetchall():
                    idx = int((row[0] - stream_start_time) / bucket_interval)
                    if 0 <= idx < num_buckets:
                        raw_buckets[idx] += 1
            finally:
                conn.close()

            # 4. Apply 10-min sliding window (±5 mins) integration
            buckets = [
                sum(raw_buckets[max(0, i - 5):min(num_buckets, i + 6)])
                for i in range(num_buckets)
            ]

            # 5. [REMOVED] Do not trim leading zero-activity buckets.
            # The UI must strictly align with the OBS duration so that the heatmap's time axis
            # maps perfectly to the video playback time from the viewer's perspective.

            self.data_fetched.emit(
                buckets, True, stream_start_time, total_duration_sec, "", self.use_obs_time)

        except Exception as e:
            logging.error(f"Error fetching Pieces data: {e}")
            self.data_fetched.emit(
                [], True, 0.0, f"Error: {str(e)}", self.use_obs_time)


class PiecesDensityWidget(BaseWidget):
    """
    A widget that anchors to the yasb bar, but spawns a full-width overlay
    beneath the bar for the Pieces Workstream density heatmap.
    """
    validation_schema = PiecesDensityConfig

    _toggle_req_signal = pyqtSignal(str)
    _time_source_changed_signal = pyqtSignal(bool, str)

    def __init__(self, config: PiecesDensityConfig):
        super().__init__("pieces-density-widget")
        self.config = config

        if not HAS_DEPS:
            self.hide()
            return

        self._is_active = True
        self._use_obs_time = True
        self._overlay = DensityOverlay(self, self.config)
        self._session_manager = SessionManager()
        self._session_offset = 0
        self._controls_left = ControlsOverlayLeft(self)
        self._controls_right = ControlsOverlayRight(self)
        self._worker = None
        self._stream_start_time = 0.0
        self._last_streaming_time = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._fetch_data)
        self._timer.start(self.config.poll_interval_sec * 1000)
        self.register_callback("toggle_pieces_density", self._toggle_overlay)

        self._event_service.register_event(
            "toggle_pieces_widget", self._toggle_req_signal)
        self._toggle_req_signal.connect(self._toggle_pieces_state)

        self._event_service.register_event(
            "pieces_time_source_changed", self._time_source_changed_signal)
        self._time_source_changed_signal.connect(self._on_time_source_changed)

        # Drop to the bottom of the bar's Z-order to prevent covering other widgets
        self.lower()

        # Polling for hover without triggering Qt's window raising on mouse hover
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(50)
        self._hover_timer.timeout.connect(self._poll_hover)
        self._hover_timer.start()

    def _place_overlay_below_bar(self, allow_hidden: bool = False):
        if not self._overlay or not self._overlay.isVisible():
            if not allow_hidden:
                return

        bar_window = self.window()
        if not bar_window:
            return

        try:
            overlay_hwnd = int(self._overlay.winId())
            controls_l_hwnd = int(self._controls_left.winId())
            controls_r_hwnd = int(self._controls_right.winId())
            bar_hwnd = int(bar_window.winId())
        except RuntimeError:
            return

        SetWindowPos(controls_l_hwnd, bar_hwnd, 0, 0, 0, 0,
                     SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
        SetWindowPos(controls_r_hwnd, controls_l_hwnd, 0, 0, 0,
                     0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
        SetWindowPos(overlay_hwnd, controls_r_hwnd, 0, 0, 0, 0,
                     SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)

    def _show_overlay_below_bar(self):
        self._update_overlay_geometry()
        self._place_overlay_below_bar(allow_hidden=True)
        self._overlay.show()
        self._place_overlay_below_bar()

    def _poll_hover(self):
        if not self._overlay or not self._overlay.isVisible() or not self.config.show_tooltip or not self._overlay.is_streaming:
            if getattr(self._overlay, 'hover_idx', None) is not None:
                self._overlay.hover_idx = None
                self._overlay.update()
                QToolTip.hideText()
            return

        cursor_pos = QCursor.pos()
        geo = self._overlay.geometry()

        if geo.contains(cursor_pos):
            w = self._overlay.width()
            if w <= 0:
                return

            if self._overlay.error_msg:
                QToolTip.showText(
                    cursor_pos, f"Error: {self._overlay.error_msg}")
                return

            if not self._overlay.buckets:
                QToolTip.showText(cursor_pos, "0 Events")
                return

            # Map mouse global X to time bucket
            local_x = cursor_pos.x() - geo.x()
            bucket_index = int((local_x / w) * len(self._overlay.buckets))
            bucket_index = min(max(bucket_index, 0),
                               len(self._overlay.buckets) - 1)

            if getattr(self._overlay, 'hover_idx', None) != bucket_index:
                self._overlay.hover_idx = bucket_index
                self._overlay.update()

            val = self._overlay.buckets[bucket_index]
            ts = self._overlay.stream_start_time + bucket_index * 60
            dt_str = datetime.fromtimestamp(ts).strftime("%H:%M")
            tooltip_text = f"{dt_str} | Events (±5m): {val}"

            QToolTip.showText(cursor_pos, tooltip_text)
        else:
            if getattr(self._overlay, 'hover_idx', None) is not None:
                self._overlay.hover_idx = None
                self._overlay.update()
                QToolTip.hideText()

    def _fetch_data(self):
        # Don't overlap fetches.  Guard isRunning() with try/except because
        # deleteLater() destroys the underlying C++ object while Python's
        # self._worker reference may still be alive, causing:
        #   RuntimeError: wrapped C/C++ object of type FetchWorker has been deleted
        try:
            if self._worker and self._worker.isRunning():
                return
        except RuntimeError:
            # C++ object already deleted; treat as no active worker
            self._worker = None

        override_time = 0.0
        end_bound = 0.0
        sessions = self._session_manager.get_sessions(self._use_obs_time)
        if sessions:
            real_idx = len(sessions) - 1 + self._session_offset
            if 0 <= real_idx < len(sessions):
                override_time = sessions[real_idx]
                if real_idx + 1 < len(sessions):
                    end_bound = sessions[real_idx + 1]

        force_session = self._session_offset < 0

        self._worker = FetchWorker(self.config, self._use_obs_time, self._stream_start_time,
                                   self._last_streaming_time, override_time, force_session, end_bound)
        self._worker.data_fetched.connect(self._on_data_fetched)
        self._worker.finished.connect(self._worker.deleteLater)
        # Clear the Python reference once the thread finishes so the guard
        # above never sees a stale (deleted) C++ object again.
        self._worker.finished.connect(lambda: setattr(self, "_worker", None))
        self._worker.start()

    def _on_data_fetched(self, buckets: list[int], is_streaming: bool, start_time: float, total_duration_sec: float, error_msg: str, was_obs_time: bool):
        if not getattr(self, "_is_active", True):
            return
        # Discard stale result: time source was toggled while the worker was running.
        # The next timer tick will re-fetch with the correct source.
        if was_obs_time != self._use_obs_time:
            return

        if is_streaming and self._session_offset == 0:
            self._session_manager.record_start_time(start_time, was_obs_time)

        self._controls_left.update_buttons()
        self._controls_right.update_buttons(total_duration_sec)

        self._overlay.buckets = buckets
        self._overlay.is_streaming = is_streaming
        self._overlay.stream_start_time = start_time
        self._overlay.error_msg = error_msg

        if is_streaming:
            self._stream_start_time = start_time
            self._last_streaming_time = time.time()
            if not self._overlay.isVisible():
                self._show_overlay_below_bar()
                self._controls_left.show()
                self._controls_right.show()
            else:
                self._update_overlay_geometry()
                self._place_overlay_below_bar()
            self._overlay.update()
        else:
            if self._overlay.isVisible():
                self._overlay.hide()
                self._controls_left.hide()
                self._controls_right.hide()

    def _update_overlay_geometry(self):
        """Align the overlay with the yasb bar."""
        # Find the parent yasb bar window
        bar_window = self.window()
        if not bar_window:
            return

        bar_geo = bar_window.geometry()

        # Width matches the bar, height is configured, x matches bar x
        x = bar_geo.x()
        w = bar_geo.width()
        # Height reduced by 15px
        h = self.config.widget_height - 15

        # We want the bottom of our overlay to touch the bottom of the yasb bar,
        # plus an additional 31px downwards.
        y = bar_geo.y() + bar_geo.height() - h + 31

        self._overlay.setGeometry(x, y, w, h)

        self._controls_left.adjustSize()
        cw_l = self._controls_left.width()
        ch_l = self._controls_left.height()
        self._controls_left.setGeometry(x + 5, y + h - ch_l - 5, cw_l, ch_l)

        self._controls_right.adjustSize()
        cw_r = self._controls_right.width()
        ch_r = self._controls_right.height()
        self._controls_right.setGeometry(
            x + w - cw_r - 5, y + h - ch_r - 5, cw_r, ch_r)

    def _toggle_overlay(self):
        if self._overlay.isVisible():
            self._overlay.hide()
            self._controls_left.hide()
            self._controls_right.hide()
        else:
            self._show_overlay_below_bar()
            self._controls_left.show()
            self._controls_right.show()

    def _prev_session(self):
        sessions = self._session_manager.get_sessions(self._use_obs_time)
        if not sessions:
            return
        max_prev = -(len(sessions) - 1)
        if self._session_offset > max_prev:
            self._session_offset -= 1
            self._controls_left.update_buttons()
            self._controls_right.update_buttons(0.0)
            self._fetch_data()

    def _next_session(self):
        if self._session_offset < 0:
            self._session_offset += 1
            self._controls_left.update_buttons()
            self._controls_right.update_buttons(0.0)
            self._fetch_data()

    def _on_time_source_changed(self, use_obs: bool, screen_name: str):
        if screen_name != self.screen_name:
            return
        self._use_obs_time = use_obs
        self._stream_start_time = 0.0  # Reset known start time when switching modes
        self._session_offset = 0       # Reset navigation to latest session in new mode
        self._fetch_data()

    def _toggle_pieces_state(self, screen_name: str):
        if screen_name != self.screen_name:
            return

        self._is_active = not self._is_active
        if self._is_active:
            self._timer.start(self.config.poll_interval_sec * 1000)
            self._fetch_data()
        else:
            self._timer.stop()
            if self._worker and self._worker.isRunning():
                self._worker._is_running = False
            if self._overlay.isVisible():
                self._overlay.hide()
                self._controls_left.hide()
                self._controls_right.hide()

        self._event_service.emit_event(
            "pieces_widget_state_changed", self._is_active, self.screen_name)

    def showEvent(self, event):
        super().showEvent(event)
        self.lower()

        # Connect to bar animation signals to stay in sync
        bar_window = self.window()
        if hasattr(bar_window, "animation_tick") and not getattr(self, "_connected_anim", False):
            bar_window.animation_tick.connect(self._update_overlay_geometry)
            bar_window.animation_finished.connect(
                self._update_overlay_geometry)
            if hasattr(bar_window, "opacity_tick"):
                bar_window.opacity_tick.connect(self._overlay.setWindowOpacity)
                bar_window.opacity_tick.connect(
                    self._controls_left.setWindowOpacity)
                bar_window.opacity_tick.connect(
                    self._controls_right.setWindowOpacity)
            self._connected_anim = True

        # Initial geometry update and fetch
        QTimer.singleShot(100, self._fetch_data)

        # Ensure overlay stays under the bar window when the bar is shown again
        self._place_overlay_below_bar()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._overlay:
            self._overlay.hide()
            self._controls_left.hide()
            self._controls_right.hide()
