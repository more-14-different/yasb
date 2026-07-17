import logging
import math
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QCursor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolTip
from win32con import SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE

from core.utils.win32.bindings import SetWindowPos
from core.validation.widgets.yasb.pieces_density import PiecesDensityConfig
from core.widgets.base import BaseWidget
from core.widgets.yasb.pieces_time_source import TimeSource

TRUTH_TIME_DB_FILENAME = Path("data") / "truth_time.sqlite3"
TRUTH_TIME_MIN_SCHEMA_VERSION = 2
TRUTH_TIME_REQUIRED_COLUMNS = {
    "livestreams": {"official_start_at_utc_us", "official_end_at_utc_us"},
    "machine_sessions": {"boot_at_utc_us", "shutdown_at_utc_us", "shutdown_upper_bound_utc_us"},
}


class TruthTimeSchemaError(RuntimeError):
    pass


def selected_session_index(sessions: list[float], selected_start: float | None) -> int | None:
    if not sessions:
        return None
    if selected_start is None:
        return len(sessions) - 1
    return next(
        (index for index, start in enumerate(sessions) if abs(start - selected_start) < 0.001),
        len(sessions) - 1,
    )


def format_compact_date(value: datetime, include_year: bool = False) -> str:
    """Format a date without padding month or day with a leading zero."""
    if include_year:
        return f"{value:%y}-{value.month}-{value.day}"
    return f"{value.month}-{value.day}"


def format_compact_time(value: datetime) -> str:
    """Format hours compactly while keeping minutes at two digits."""
    return f"{value.hour}:{value.minute:02d}"


def format_compact_duration(hours: int, minutes: int) -> str:
    """Format duration hours compactly while keeping minutes at two digits."""
    return f"{hours}:{minutes:02d}"


def ruler_label_baseline(
    normal_baseline: float,
    label_rect: QRectF,
    exclusions: list[QRectF],
    font_descent: float,
) -> float:
    collision_tops = [exclusion.top() for exclusion in exclusions if exclusion.intersects(label_rect)]
    if not collision_tops:
        return normal_baseline
    collision_baseline = min(collision_tops) - font_descent - 3
    return min(normal_baseline, collision_baseline + 10)


def resolve_truth_time_db_path(configured_path: str) -> str:
    """Resolve an explicit path or discover a nearby event-logger database."""
    configured_path = configured_path.strip()
    if configured_path and configured_path.casefold() != "auto":
        return os.path.abspath(os.path.expandvars(os.path.expanduser(configured_path)))

    candidates: list[Path] = []
    environment_path = os.environ.get("EVENT_LOGGER_DB_PATH", "").strip()
    if environment_path:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(environment_path)))

    anchors = (Path(sys.executable).resolve().parent, Path(__file__).resolve().parent, Path.cwd().resolve())
    for anchor in anchors:
        for parent in (anchor, *anchor.parents):
            if parent.name.casefold() == "event-logger":
                candidates.append(parent / TRUTH_TIME_DB_FILENAME)
            candidates.append(parent / "event-logger" / TRUTH_TIME_DB_FILENAME)

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    fallback = Path.cwd() / TRUTH_TIME_DB_FILENAME
    if local_app_data:
        fallback = Path(local_app_data) / "event-logger" / TRUTH_TIME_DB_FILENAME
        candidates.append(fallback)

    unique_candidates = list(dict.fromkeys(path.resolve() for path in candidates))
    for candidate in unique_candidates:
        if candidate.is_file():
            return str(candidate)

    return str(fallback.resolve())


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
    """Reads canonical livestream and machine intervals from event-logger."""

    _MIN_VALID_TIMESTAMP = 1_000_000_000  # ~2001-09-09, rejects epoch-zero

    def __init__(self, database_path: str):
        self.database_path = resolve_truth_time_db_path(database_path)
        self.last_error = ""
        self._schema_validated = False
        logging.info("Pieces truth-time database: %s", self.database_path)

    def _connect(self) -> sqlite3.Connection:
        uri_path = self.database_path.replace("\\", "/")
        return sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=2)

    def _validate_schema(self, conn: sqlite3.Connection) -> None:
        if self._schema_validated:
            return

        version = int(conn.execute("pragma user_version").fetchone()[0])
        if version < TRUTH_TIME_MIN_SCHEMA_VERSION:
            raise TruthTimeSchemaError(
                f"event-logger schema v{version} is unsupported; v{TRUTH_TIME_MIN_SCHEMA_VERSION}+ is required"
            )

        for table, required_columns in TRUTH_TIME_REQUIRED_COLUMNS.items():
            actual_columns = {row[1] for row in conn.execute(f"pragma table_info({table})")}
            missing_columns = required_columns - actual_columns
            if missing_columns:
                missing = ", ".join(sorted(missing_columns))
                raise TruthTimeSchemaError(f"event-logger table {table} is missing columns: {missing}")

        self._schema_validated = True

    def _set_error(self, message: str) -> None:
        if message != self.last_error:
            logging.error("Failed to read event-logger sessions: %s", message)
        self.last_error = message

    def _query_intervals(self, sql: str) -> list[tuple[float, float | None]]:
        if not os.path.exists(self.database_path):
            self._set_error(f"event-logger database not found: {self.database_path}")
            return []
        conn = None
        try:
            conn = self._connect()
            self._validate_schema(conn)
            rows = conn.execute(sql).fetchall()
            self.last_error = ""
            return [
                (start_us / 1_000_000, end_us / 1_000_000 if end_us else None)
                for start_us, end_us in rows
                if start_us and start_us / 1_000_000 > self._MIN_VALID_TIMESTAMP
            ]
        except (sqlite3.Error, TruthTimeSchemaError) as error:
            self._schema_validated = False
            self._set_error(str(error))
            return []
        finally:
            if conn is not None:
                conn.close()

    def livestream_intervals(self) -> list[tuple[float, float | None]]:
        return self._query_intervals(
            "select official_start_at_utc_us, official_end_at_utc_us "
            "from livestreams order by official_start_at_utc_us"
        )

    def machine_intervals(self) -> list[tuple[float, float | None]]:
        return self._query_intervals(
            "select boot_at_utc_us, "
            "coalesce(shutdown_at_utc_us, shutdown_upper_bound_utc_us) "
            "from machine_sessions order by boot_at_utc_us"
        )

    def intervals(self, source: TimeSource) -> list[tuple[float, float | None]]:
        if source is TimeSource.YOUTUBE_LIVESTREAM:
            return self.livestream_intervals()
        if source is TimeSource.MACHINE_SESSION:
            return self.machine_intervals()
        raise ValueError(f"Unsupported Pieces time source: {source}")

    def get_sessions(self, source: TimeSource) -> list[float]:
        return [start for start, _ in self.intervals(source)]

    def get_session_end(self, source: TimeSource, start_time: float) -> float | None:
        return next(
            (end for start, end in self.intervals(source) if abs(start - start_time) < 0.001),
            None,
        )


class ControlsOverlayBase(QFrame):
    def __init__(self, widget: PiecesDensityWidget, parent=None):
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
                background-color: rgba(58, 55, 72, 190);
                color: #fffaff;
                border: 1px solid rgba(255, 255, 255, 55);
                border-radius: 4px;
                padding: 1px 4px;
                margin: 0px;
                font-family: Consolas, monospace;
            }
            QPushButton:hover {
                border-color: rgba(255, 255, 255, 130);
            }
            QPushButton#LeftPreviousButton {
                background-color: rgba(111, 88, 150, 205);
                color: #fbf3ff;
            }
            QPushButton#LeftPreviousButton:hover {
                background-color: rgba(143, 113, 187, 230);
            }
            QPushButton#LeftNextButton {
                background-color: rgba(153, 83, 116, 205);
                color: #fff2f8;
            }
            QPushButton#LeftNextButton:hover {
                background-color: rgba(190, 105, 144, 230);
            }
            QPushButton#RightPreviousButton {
                background-color: rgba(52, 124, 103, 205);
                color: #effff9;
            }
            QPushButton#RightPreviousButton:hover {
                background-color: rgba(68, 158, 131, 230);
            }
            QPushButton#RightNextButton {
                background-color: rgba(60, 109, 157, 205);
                color: #f0f8ff;
            }
            QPushButton#RightNextButton:hover {
                background-color: rgba(76, 139, 198, 230);
            }
            QPushButton:disabled {
                background-color: rgba(10, 10, 10, 50);
                color: rgba(255, 255, 255, 30);
                border: 1px solid rgba(255, 255, 255, 10);
            }
            QLabel {
                color: #fffaff;
                font-size: 10px;
                background-color: rgba(58, 55, 72, 190);
                border-radius: 4px;
                padding: 1px 4px;
                margin: 0px;
                font-family: Consolas, monospace;
            }
            QLabel#DateLabel {
                background-color: rgba(145, 98, 61, 205);
                color: #fff4df;
            }
            QLabel#StartTimeLabel {
                background-color: rgba(79, 99, 147, 205);
                color: #f3f5ff;
            }
            QLabel#DaysLabel {
                background-color: rgba(151, 84, 105, 205);
                color: #fff1f5;
            }
            QLabel#DurationLabel {
                background-color: rgba(56, 119, 112, 205);
                color: #effffa;
            }
        """


class ControlsOverlayLeft(ControlsOverlayBase):
    def __init__(self, widget: PiecesDensityWidget, parent=None):
        super().__init__(widget, parent)
        self.btn_prev = QPushButton("<")
        self.btn_next = QPushButton(">")
        self.lbl_date = QLabel()
        self.lbl_date.setObjectName("DateLabel")
        self.lbl_time = QLabel("--:--")
        self.btn_prev.setObjectName("LeftPreviousButton")
        self.btn_next.setObjectName("LeftNextButton")
        self.lbl_time.setObjectName("StartTimeLabel")

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
        sessions = self.widget._session_manager.get_sessions(self.widget._time_source)
        if not sessions:
            self.lbl_date.hide()
            self.lbl_time.setText("No Session")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return

        real_idx = self.widget._selected_session_index(sessions)
        if real_idx is None:
            return
        self.btn_prev.setEnabled(real_idx > 0)
        self.btn_next.setEnabled(real_idx < len(sessions) - 1)

        ts = sessions[real_idx]
        dt = datetime.fromtimestamp(ts)
        now = datetime.now()

        if now.year != dt.year:
            date_str = format_compact_date(dt, include_year=True)
        elif now.month != dt.month or now.day != dt.day:
            date_str = format_compact_date(dt)
        else:
            date_str = ""

        if date_str:
            self.lbl_date.setText(date_str)
            self.lbl_date.show()
        else:
            self.lbl_date.hide()

        self.lbl_time.setText(format_compact_time(dt))
        self.adjustSize()


class ControlsOverlayRight(ControlsOverlayBase):
    def __init__(self, widget: PiecesDensityWidget, parent=None):
        super().__init__(widget, parent)
        self.btn_prev = QPushButton("<")
        self.btn_next = QPushButton(">")
        self.lbl_days = QLabel()
        self.lbl_duration = QLabel("--:--")
        self.btn_prev.setObjectName("RightPreviousButton")
        self.btn_next.setObjectName("RightNextButton")
        self.lbl_days.setObjectName("DaysLabel")
        self.lbl_duration.setObjectName("DurationLabel")

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
        sessions = self.widget._session_manager.get_sessions(self.widget._time_source)
        if not sessions:
            self.lbl_days.hide()
            self.lbl_duration.setText("--:--")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return

        real_idx = self.widget._selected_session_index(sessions)
        if real_idx is None:
            return
        self.btn_prev.setEnabled(real_idx > 0)
        self.btn_next.setEnabled(real_idx < len(sessions) - 1)

        days = int(duration_sec // 86400)
        rem = duration_sec % 86400
        hours = int(rem // 3600)
        minutes = int((rem % 3600) // 60)

        if days > 0:
            self.lbl_days.setText(f"+{days}d")
            self.lbl_days.show()
        else:
            self.lbl_days.hide()

        self.lbl_duration.setText(format_compact_duration(hours, minutes))
        self.adjustSize()


class DensityOverlay(QFrame):
    """The actual floating overlay window that draws the density heatmap."""

    def __init__(self, widget: PiecesDensityWidget, config: PiecesDensityConfig):
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

    def _control_exclusion_rects(self) -> list[QRectF]:
        overlay_geometry = self.geometry()
        exclusions = []
        for control in (self.widget._controls_left, self.widget._controls_right):
            geometry = control.geometry()
            exclusion = QRectF(
                geometry.x() - overlay_geometry.x(),
                geometry.y() - overlay_geometry.y(),
                geometry.width(),
                geometry.height(),
            )
            exclusion.adjust(-4, -4, 4, 4)
            exclusions.append(exclusion)
        return exclusions

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
            font_metrics = painter.fontMetrics()
            exclusions = self._control_exclusion_rects()

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
                    label = dt.strftime("%H:00")
                    label_width = font_metrics.horizontalAdvance(label)
                    label_x = min(max(x + 2, 2), max(w - label_width - 2, 2))
                    normal_baseline = h - 12
                    label_rect = QRectF(
                        label_x,
                        normal_baseline - font_metrics.ascent(),
                        label_width,
                        font_metrics.height(),
                    )
                    label_baseline = ruler_label_baseline(
                        normal_baseline,
                        label_rect,
                        exclusions,
                        font_metrics.descent(),
                    )
                    painter.setPen(
                        QColor(255, 255, 255, 255 if is_hovered else 200))
                    painter.drawText(QPointF(label_x, label_baseline), label)
                elif is_minor_tick:
                    pen_minor = QPen(
                        QColor(255, 255, 255, 200 if is_hovered else 60))
                    pen_minor.setWidthF(1.5 if is_hovered else 1.0)
                    painter.setPen(pen_minor)

                    x = i * step_x
                    painter.drawLine(QPointF(x, h), QPointF(x, h - 5))


class FetchWorker(QThread):
    # buckets, has_interval, start_time, duration, error, source
    data_fetched = pyqtSignal(list, bool, float, float, str, str)

    def __init__(self, config: PiecesDensityConfig, time_source: TimeSource, known_start_time: float = 0.0, last_streaming_time: float = 0.0, session_override: float = 0.0, force_session: bool = False, session_end_bound: float = 0.0, interval_error: str = ""):
        super().__init__()
        self.session_end_bound = session_end_bound
        self.config = config
        self.time_source = time_source
        self.known_start_time = known_start_time
        self.last_streaming_time = last_streaming_time
        self.session_override = session_override
        self.force_session = force_session
        self.interval_error = interval_error
        self._is_running = True

    def cancel(self):
        self._is_running = False

    def run(self):
        try:
            # event-logger is the only provider of interval boundaries.
            if self.interval_error:
                self.data_fetched.emit(
                    [], True, 0.0, 0.0, self.interval_error, self.time_source.value)
                return

            if self.session_override <= 0:
                self.data_fetched.emit(
                    [], False, 0.0, 0.0,
                    f"No canonical interval in {self.config.truth_time_db_path}",
                    self.time_source.value)
                return

            stream_start_time = self.session_override
            interval_end = self.session_end_bound or time.time()
            total_duration_sec = max(interval_end - stream_start_time, 0.0)

            # Honour cancellation request before the SQLite query
            if not self._is_running:
                return

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
                    [], True, 0.0, 0.0, f"Pieces DB missing at: {db_path}", self.time_source.value)
                return

            # Connect in read-only mode
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            try:
                c = conn.cursor()
                c.execute(
                    "SELECT created_at FROM vectors WHERE created_at >= ? AND created_at < ?",
                    (stream_start_time, interval_end),
                )
                rows = c.fetchall()
                if not self._is_running:
                    return
                for row in rows:
                    if not self._is_running:
                        return
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

            # Keep leading zero-activity buckets so the heatmap remains aligned with the
            # canonical interval selected from event-logger.

            if not self._is_running:
                return

            self.data_fetched.emit(
                buckets, True, stream_start_time, total_duration_sec, "", self.time_source.value)

        except Exception as e:
            logging.error("Error fetching Pieces data: %s", e)
            self.data_fetched.emit(
                [], True, 0.0, 0.0, f"Error: {e}", self.time_source.value)


class PiecesDensityWidget(BaseWidget):
    """
    A widget that anchors to the yasb bar, but spawns a full-width overlay
    beneath the bar for the Pieces Workstream density heatmap.
    """
    validation_schema = PiecesDensityConfig

    _toggle_req_signal = pyqtSignal(str)
    _time_source_changed_signal = pyqtSignal(str, str)

    def __init__(self, config: PiecesDensityConfig):
        super().__init__("pieces-density-widget")
        self.config = config

        self._is_active = True
        self._time_source = TimeSource.YOUTUBE_LIVESTREAM
        self._overlay = DensityOverlay(self, self.config)
        self._session_manager = SessionManager(config.truth_time_db_path)
        self._selected_session_start = None
        self._controls_left = ControlsOverlayLeft(self)
        self._controls_right = ControlsOverlayRight(self)
        self._worker = None
        self._refresh_pending = False
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
        sessions = self._session_manager.get_sessions(self._time_source)
        if sessions:
            real_idx = self._selected_session_index(sessions)
            if real_idx is not None:
                override_time = sessions[real_idx]
                end_bound = self._session_manager.get_session_end(
                    self._time_source, override_time) or 0.0

        force_session = override_time > 0
        interval_error = self._session_manager.last_error

        self._worker = FetchWorker(self.config, self._time_source, self._stream_start_time,
                                   self._last_streaming_time, override_time, force_session, end_bound,
                                   interval_error)
        self._worker.data_fetched.connect(self._on_data_fetched)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _request_refresh(self):
        try:
            if self._worker and self._worker.isRunning():
                self._worker.cancel()
                self._refresh_pending = True
                return
        except RuntimeError:
            self._worker = None
        self._fetch_data()

    def _on_worker_finished(self):
        self._worker = None
        if self._refresh_pending and self._is_active:
            self._refresh_pending = False
            QTimer.singleShot(0, self._fetch_data)

    def _selected_session_index(self, sessions: list[float]) -> int | None:
        index = selected_session_index(sessions, self._selected_session_start)
        if (
            index is not None
            and self._selected_session_start is not None
            and abs(sessions[index] - self._selected_session_start) >= 0.001
        ):
            self._selected_session_start = None
        return index

    def _on_data_fetched(self, buckets: list[int], is_streaming: bool, start_time: float, total_duration_sec: float, error_msg: str, source_value: str):
        if not getattr(self, "_is_active", True):
            return
        # Discard stale result: time source was toggled while the worker was running.
        # The next timer tick will re-fetch with the correct source.
        if TimeSource.parse(source_value) is not self._time_source:
            return

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
        sessions = self._session_manager.get_sessions(self._time_source)
        if not sessions:
            return
        index = self._selected_session_index(sessions)
        if index is not None and index > 0:
            self._selected_session_start = sessions[index - 1]
            self._controls_left.update_buttons()
            self._controls_right.update_buttons(0.0)
            self._request_refresh()

    def _next_session(self):
        sessions = self._session_manager.get_sessions(self._time_source)
        index = self._selected_session_index(sessions)
        if index is not None and index < len(sessions) - 1:
            next_index = index + 1
            self._selected_session_start = None if next_index == len(sessions) - 1 else sessions[next_index]
            self._controls_left.update_buttons()
            self._controls_right.update_buttons(0.0)
            self._request_refresh()

    def _on_time_source_changed(self, source_value: str, screen_name: str):
        if screen_name != self.screen_name:
            return
        self._time_source = TimeSource.parse(source_value)
        self._stream_start_time = 0.0  # Reset known start time when switching modes
        self._selected_session_start = None
        self._request_refresh()

    def _toggle_pieces_state(self, screen_name: str):
        if screen_name != self.screen_name:
            return

        self._is_active = not self._is_active
        if self._is_active:
            self._timer.start(self.config.poll_interval_sec * 1000)
            self._request_refresh()
        else:
            self._timer.stop()
            self._refresh_pending = False
            if self._worker and self._worker.isRunning():
                self._worker.cancel()
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
