import base64
import logging
import math
import os
import sqlite3
import sys
import time
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Protocol, cast, override

from PyQt6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, pyqtBoundSignal, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QHideEvent,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
    QShowEvent,
)
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolTip, QWidget
from win32con import SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE

from core.utils.win32.bindings import SetWindowPos
from core.validation.widgets.yasb.pieces_density import PiecesDensityConfig
from core.widgets.base import BaseWidget
from core.widgets.yasb.pieces_density_activity import (
    ActivityLayoutPolicy,
    ActivityLayoutSnapshot,
    build_activity_layout,
)
from core.widgets.yasb.pieces_density_icons import IconByteCache, ImageLru, PixmapLru
from core.widgets.yasb.pieces_density_source import (
    ActivityTimelineData,
    AppTimelineCache,
    DensityBucketCache,
    integrate_density,
    resolve_density_source,
)
from core.widgets.yasb.pieces_density_timeline import (
    StageLayout,
    TimelineStage,
    build_timeline_stages,
    layout_timeline_stages,
)
from core.widgets.yasb.pieces_time_source import TimeSource

TRUTH_TIME_DB_FILENAME = Path("data") / "truth_time.sqlite3"
TRUTH_TIME_MIN_SCHEMA_VERSION = 2
TRUTH_TIME_REQUIRED_COLUMNS = {
    "livestreams": {"official_start_at_utc_us", "official_end_at_utc_us"},
    "machine_sessions": {"boot_at_utc_us", "shutdown_at_utc_us", "shutdown_upper_bound_utc_us"},
}
_DENSITY_BUCKET_CACHE = DensityBucketCache()
_APP_TIMELINE_CACHE = AppTimelineCache()
_ICON_BYTE_CACHE = IconByteCache()
_ICON_IMAGE_CACHE = ImageLru()
TIMELINE_MAJOR_TICK_HEIGHT = 10.0
TIMELINE_ICON_RULER_GAP = 2.0
HOVER_CURVE_RADIUS_BUCKETS = 5
APP_HOVER_WHITE_BLEND = 0.55


@dataclass(frozen=True)
class DensityTimelineSnapshot:
    buckets: list[int]
    has_interval: bool
    start_time: float
    duration: float
    error: str
    source_value: str
    stages: list[TimelineStage]
    icon_bytes: dict[str, bytes]
    activity_data: ActivityTimelineData | None = None
    activity_layout: ActivityLayoutSnapshot | None = None
    activity_image: QImage | None = None
    timings_ms: tuple[tuple[str, float], ...] = ()


class _AnimatedBar(Protocol):
    animation_tick: pyqtBoundSignal
    animation_finished: pyqtBoundSignal
    opacity_tick: pyqtBoundSignal


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


def density_tooltip_html(event_count: int, value: datetime) -> str:
    """Build the four semantic units shown for a hovered density bucket."""
    units = (
        ("#18382B", "#B7F5C8", f"<b>{event_count}</b>"),
        ("#3B2924", "#F2B8A0", "Events&nbsp;∈"),
        ("#1E3148", "#BCD7FF", f"<b>{format_compact_time(value)}</b>"),
        ("#30263E", "#DDC7FF", "±5m"),
    )
    cells = "".join(
        f'<td style="white-space: nowrap;" bgcolor="{background}"><font color="{foreground}">{text}</font></td>'
        for background, foreground, text in units
    )
    return f'<table cellspacing="3" cellpadding="4"><tr>{cells}</tr></table>'


def stage_tooltip_html(stage: TimelineStage | None) -> str:
    if stage is None:
        return ""
    rows = []
    for app in stage.apps[:5]:
        seconds = app.engaged_milliseconds / 1000
        rows.append(
            "<tr>"
            f'<td style="white-space: nowrap;"><b>{escape(app.display_name)}</b></td>'
            f'<td style="white-space: nowrap;">{app.direct_event_count} inputs</td>'
            f'<td style="white-space: nowrap;">{seconds:.1f}s engaged</td>'
            f'<td style="white-space: nowrap;">{app.confidence_milli / 10:.0f}%</td>'
            "</tr>"
        )
    return f'<table cellspacing="3" cellpadding="3">{"".join(rows)}</table>' if rows else ""


def activity_tooltip_html(
    layout: ActivityLayoutSnapshot | None,
    data: ActivityTimelineData | None,
    bucket: int,
    icon_data_urls: dict[str, str] | None = None,
) -> str:
    if layout is None or data is None:
        return ""
    global_record = next((record for record in data.global_records if record.bucket == bucket), None)
    app_records = {record.canonical_app_id: record for record in data.app_records if record.bucket == bucket}
    components = [
        geometry
        for geometry in layout.components
        if geometry.component.start_bucket <= bucket < geometry.component.end_bucket
    ]
    components.sort(
        key=lambda geometry: (
            -(
                app_records[geometry.component.app_id].direct_input_count
                if geometry.component.app_id in app_records
                else 0
            ),
            -(
                app_records[geometry.component.app_id].non_direct_activity_count
                if geometry.component.app_id in app_records
                else 0
            ),
            geometry.component.app_id,
        )
    )
    direct_total = (
        global_record.direct_input_count
        if global_record is not None
        else sum(record.direct_input_count for record in app_records.values())
    )
    activity_total = (
        global_record.non_direct_activity_count
        if global_record is not None
        else sum(record.non_direct_activity_count for record in app_records.values())
    )

    def count_cells_with_share(value: int, total: int) -> str:
        percentage = value / total * 100 if total > 0 else 0
        return (
            f'<td align="right" style="white-space: nowrap;">{value}</td>'
            f'<td align="left" style="white-space: nowrap;">({percentage:.0f}%)</td>'
        )

    def icon_cell(icon_sha256: str | None) -> str:
        source = (icon_data_urls or {}).get(icon_sha256 or "")
        if not source:
            return '<td width="16"></td>'
        return (
            '<td width="16" align="center" valign="middle">'
            f'<img src="{source}" width="14" height="14" style="vertical-align: middle;">'
            "</td>"
        )

    input_header_count = str(global_record.direct_input_count) if global_record is not None else ""
    activity_header_count = str(global_record.non_direct_activity_count) if global_record is not None else ""
    header = (
        "<tr>"
        '<td width="16"></td>'
        '<th align="left" style="white-space: nowrap;"><font color="#a0a0a0">app</font></th>'
        f'<th align="right" style="white-space: nowrap;"><font color="#ffffff">{input_header_count}</font></th>'
        '<th align="left" style="white-space: nowrap;"><font color="#a0a0a0">input</font></th>'
        f'<th align="right" style="white-space: nowrap;"><font color="#ffffff">{activity_header_count}</font></th>'
        '<th align="left" style="white-space: nowrap;"><font color="#a0a0a0">activity</font></th>'
        '<th align="left" style="white-space: nowrap;"><font color="#a0a0a0">status</font></th>'
        '<th align="left" style="white-space: nowrap;"><font color="#a0a0a0">attribution</font></th>'
        "</tr>"
    )
    rows: list[str] = []
    for geometry in components[:8]:
        component = geometry.component
        record = app_records.get(component.app_id)
        direct = record.direct_input_count if record else 0
        non_direct = record.non_direct_activity_count if record else 0
        if bucket in component.bridge_buckets:
            state = "focus bridge"
        elif direct:
            state = "direct"
        elif non_direct:
            state = "observable"
        else:
            state = "interval"
        rows.append(
            "<tr>"
            f"{icon_cell(component.icon_sha256)}"
            f'<td style="white-space: nowrap;"><b>{escape(component.display_name)}</b></td>'
            f"{count_cells_with_share(direct, direct_total)}"
            f"{count_cells_with_share(non_direct, activity_total)}"
            f'<td style="white-space: nowrap;">{state}</td>'
            f'<td style="white-space: nowrap;">{component.confidence_milli / 10:.0f}%</td>'
            "</tr>"
        )
    if not rows and global_record is None:
        return ""
    return f'<table cellspacing="3" cellpadding="3">{header}{"".join(rows)}</table>'


def ruler_label_x(
    tick_x: float,
    hour_width: float,
    colon_width: float,
    label_width: float,
    ruler_width: float,
    margin: float = 2,
) -> float:
    """Place a time label with the colon centered on its major tick."""
    colon_center = hour_width + colon_width / 2
    return min(
        max(tick_x - colon_center, margin),
        max(ruler_width - label_width - margin, margin),
    )


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
    return min(normal_baseline, collision_baseline + 7)


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
        parts = color_str[5:-1].split(",")
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


def color_with_alpha(rgb: int, alpha: int) -> QColor:
    color = QColor.fromRgb(rgb)
    color.setAlpha(alpha)
    return color


def mix_color_with_white(rgb: int, white_weight: float = APP_HOVER_WHITE_BLEND, alpha: int = 245) -> QColor:
    """Return an app color brightened toward white without losing its identity."""
    weight = min(max(white_weight, 0.0), 1.0)
    color = QColor.fromRgb(rgb)
    return QColor(
        round(color.red() + (255 - color.red()) * weight),
        round(color.green() + (255 - color.green()) * weight),
        round(color.blue() + (255 - color.blue()) * weight),
        alpha,
    )


def density_curve_points(
    buckets: Sequence[int],
    width: int,
    height: int,
    density_top: float,
) -> list[QPointF]:
    maximum = max(buckets, default=0)
    if len(buckets) <= 1 or maximum <= 0:
        return []
    step_x = width / (len(buckets) - 1)
    y_scale = max(height - density_top, 1.0) / maximum
    return [QPointF(index * step_x, height - value * y_scale) for index, value in enumerate(buckets)]


def smooth_curve_path(points: Sequence[QPointF], start_index: int = 0, end_index: int | None = None) -> QPainterPath:
    path = QPainterPath()
    if not points:
        return path
    last_index = len(points) - 1 if end_index is None else min(end_index, len(points) - 1)
    first_index = min(max(start_index, 0), last_index)
    path.moveTo(points[first_index])
    for index in range(first_index, last_index):
        left = points[index]
        right = points[index + 1]
        midpoint = (left.x() + right.x()) / 2
        path.cubicTo(QPointF(midpoint, left.y()), QPointF(midpoint, right.y()), right)
    return path


def draw_density_heatmap(
    painter: QPainter,
    buckets: Sequence[int],
    config: PiecesDensityConfig,
    width: int,
    height: int,
    *,
    density_top: float = 0.0,
) -> list[QPointF]:
    """Paint the legacy smooth, multicolor density surface and return its curve."""
    if width <= 0 or height <= 0:
        return []
    density_top = min(max(density_top, 0.0), float(height))
    gradient = QLinearGradient(0, height, 0, density_top)
    gradient.setColorAt(0.0, parse_color(config.color_low))
    gradient.setColorAt(0.5, parse_color(config.color_mid))
    gradient.setColorAt(1.0, parse_color(config.color_high))

    path = QPainterPath()
    path.moveTo(0, height)
    points = density_curve_points(buckets, width, height, density_top)
    if points:
        path.lineTo(points[0])
        curve = smooth_curve_path(points)
        path.connectPath(curve)
    else:
        baseline = height - 5
        path.lineTo(0, baseline)
        path.lineTo(width, baseline)
    path.lineTo(width, height)
    path.closeSubpath()
    painter.fillPath(path, QBrush(gradient))
    return points


def render_activity_image(
    layout: ActivityLayoutSnapshot,
    density_buckets: Sequence[int],
    icon_bytes: dict[str, bytes],
    config: PiecesDensityConfig,
    height: int,
    dpr: float,
) -> QImage:
    image = QImage(
        max(round(layout.width * dpr), 1),
        max(round(height * dpr), 1),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.setDevicePixelRatio(dpr)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    global_height = max(height - TIMELINE_MAJOR_TICK_HEIGHT, 1.0)
    draw_density_heatmap(
        painter,
        density_buckets,
        config,
        layout.width,
        height,
        density_top=min(layout.band_height, height),
    )

    if layout.global_envelope:
        first = layout.global_envelope[0]
        if config.show_unattributed_residual:
            residual = QPainterPath()
            residual.moveTo(first.pixel, height)
            for sample in layout.global_envelope:
                residual.lineTo(sample.pixel, height - sample.residual_non_direct_level * global_height)
            residual.lineTo(layout.global_envelope[-1].pixel, height)
            residual.closeSubpath()
            painter.fillPath(
                residual,
                QBrush(QColor(175, 175, 175, 95), Qt.BrushStyle.Dense4Pattern),
            )

        if config.show_direct_ribbon:
            for index, sample in enumerate(layout.global_envelope):
                if sample.direct_level <= 0:
                    continue
                next_x = (
                    layout.global_envelope[index + 1].pixel
                    if index + 1 < len(layout.global_envelope)
                    else sample.pixel + 1
                )
                painter.fillRect(
                    QRectF(sample.pixel, height - 6, max(next_x - sample.pixel, 1), 5),
                    QColor(100, 224, 255, round(45 + sample.direct_level * 190)),
                )
                if config.show_unattributed_residual and sample.residual_direct_level > 0:
                    painter.setPen(QPen(QColor(210, 210, 210, 180), 1.0, Qt.PenStyle.DashLine))
                    painter.drawLine(QPointF(sample.pixel, height - 7), QPointF(next_x, height - 7))

    track_height = layout.band_height / max(layout.policy.max_tracks, 1)
    for geometry in layout.components:
        component = geometry.component
        if geometry.track >= 0:
            baseline = geometry.track * track_height + 2.0
            painter.setPen(QPen(color_with_alpha(component.color.rgb, 210), 1.0))
            painter.drawLine(QPointF(geometry.x_start, baseline), QPointF(geometry.x_end, baseline))
            if config.show_non_direct_cloud and geometry.envelope:
                cloud = QPainterPath()
                cloud.moveTo(geometry.x_start, baseline)
                for sample in geometry.envelope:
                    cloud.lineTo(sample.pixel, baseline + sample.non_direct_level * max(track_height - 4, 1))
                cloud.lineTo(geometry.x_end, baseline)
                cloud.closeSubpath()
                painter.fillPath(cloud, QBrush(color_with_alpha(component.color.rgb, 72)))
                painter.strokePath(cloud, QPen(color_with_alpha(component.color.rgb, 145), 0.8))
            if config.show_direct_ribbon:
                for index, sample in enumerate(geometry.envelope):
                    if sample.direct_level <= 0:
                        continue
                    next_x = (
                        geometry.envelope[index + 1].pixel if index + 1 < len(geometry.envelope) else sample.pixel + 1
                    )
                    painter.fillRect(
                        QRectF(sample.pixel, baseline - 1.5, max(next_x - sample.pixel, 1), 3.0),
                        color_with_alpha(component.color.rgb, round(48 + sample.direct_level * 207)),
                    )
            if geometry.bridge_segments:
                painter.setPen(QPen(color_with_alpha(component.color.rgb, 175), 1.0, Qt.PenStyle.DotLine))
                for left, right in geometry.bridge_segments:
                    painter.drawLine(QPointF(left, baseline), QPointF(right, baseline))
        else:
            baseline = max(layout.band_height - 3, 1)
        if geometry.direct_pin_x is not None:
            painter.setPen(QPen(color_with_alpha(component.color.rgb, 235), 1.4))
            painter.drawLine(
                QPointF(geometry.direct_pin_x, max(baseline - 4, 0)),
                QPointF(geometry.direct_pin_x, min(baseline + 6, layout.band_height)),
            )

    for geometry in layout.components:
        icon = geometry.icon
        icon_hash = geometry.component.icon_sha256
        if icon is None or not icon_hash:
            continue
        if icon.leader_from_x is not None:
            baseline = geometry.track * track_height + 2.0
            painter.setPen(QPen(color_with_alpha(geometry.component.color.rgb, 150), 0.8))
            painter.drawLine(
                QPointF(icon.leader_from_x, baseline),
                QPointF(icon.x + icon.size / 2, icon.y + icon.size),
            )
        payload = icon_bytes.get(icon_hash)
        if payload is None:
            continue
        icon_image = _ICON_IMAGE_CACHE.get_or_prepare(icon_hash, payload, icon.size, dpr)
        if icon_image is None:
            continue
        painter.setOpacity(icon.opacity)
        painter.drawImage(
            QRectF(icon.x, icon.y, icon.size, icon.size),
            icon_image,
            QRectF(icon_image.rect()),
        )
        painter.setOpacity(1.0)
    painter.end()
    return image


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
            intervals = [
                (start_us / 1_000_000, end_us / 1_000_000 if end_us else None)
                for start_us, end_us in rows
                if start_us and start_us / 1_000_000 > self._MIN_VALID_TIMESTAMP
            ]
            # A stale unfinished historical interval must not stretch all the
            # way to the present. The next canonical start is its safe upper
            # bound; only the newest interval may still be genuinely live.
            return [
                (start, end if end is not None else intervals[index + 1][0])
                if index + 1 < len(intervals)
                else (start, end)
                for index, (start, end) in enumerate(intervals)
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
            "select official_start_at_utc_us, official_end_at_utc_us from livestreams order by official_start_at_utc_us"
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
    def __init__(self, widget: PiecesDensityWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.widget = widget
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.controls_layout = QHBoxLayout(self)
        self.controls_layout.setContentsMargins(0, 0, 0, 0)
        self.controls_layout.setSpacing(2)

        self.style_str = """
            QPushButton {
                background-color: rgba(164, 124, 134, 120);
                color: rgba(250, 239, 242, 225);
                border: 1px solid rgba(222, 194, 200, 75);
                border-radius: 4px;
                padding: 1px 4px;
                margin: 0px;
                font-family: Consolas, monospace;
            }
            QPushButton:hover {
                background-color: rgba(183, 140, 150, 165);
                border-color: rgba(235, 211, 216, 115);
            }
            QPushButton:disabled {
                background-color: rgba(104, 82, 89, 75);
                color: rgba(198, 178, 183, 95);
                border: 1px solid rgba(156, 126, 133, 55);
            }
            QLabel {
                color: rgba(244, 241, 239, 215);
                font-size: 10px;
                background-color: rgba(91, 89, 94, 105);
                border-radius: 4px;
                padding: 1px 4px;
                margin: 0px;
                font-family: Consolas, monospace;
            }
            QLabel#DateLabel {
                background-color: rgba(126, 110, 99, 115);
                color: rgba(249, 241, 232, 220);
            }
            QLabel#StartTimeLabel {
                background-color: rgba(92, 107, 118, 115);
                color: rgba(238, 243, 246, 220);
            }
            QLabel#DaysLabel {
                background-color: rgba(123, 102, 107, 115);
                color: rgba(248, 238, 240, 220);
            }
            QLabel#DurationLabel {
                background-color: rgba(96, 112, 105, 115);
                color: rgba(238, 245, 241, 220);
            }
        """


class ControlsOverlayLeft(ControlsOverlayBase):
    def __init__(self, widget: PiecesDensityWidget, parent: QWidget | None = None):
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

        self.controls_layout.addWidget(self.btn_prev)
        self.controls_layout.addWidget(self.lbl_date)
        self.controls_layout.addWidget(self.lbl_time)
        self.controls_layout.addWidget(self.btn_next)

        self.btn_prev.clicked.connect(self.widget.select_previous_session)
        self.btn_next.clicked.connect(self.widget.select_next_session)
        self.update_buttons()

    def update_buttons(self):
        sessions = self.widget.sessions()
        if not sessions:
            self.lbl_date.hide()
            self.lbl_time.setText("No Session")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return

        real_idx = self.widget.selected_session_index(sessions)
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
    def __init__(self, widget: PiecesDensityWidget, parent: QWidget | None = None):
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

        self.controls_layout.addWidget(self.btn_prev)
        self.controls_layout.addWidget(self.lbl_days)
        self.controls_layout.addWidget(self.lbl_duration)
        self.controls_layout.addWidget(self.btn_next)

        self.btn_prev.clicked.connect(self.widget.select_previous_session)
        self.btn_next.clicked.connect(self.widget.select_next_session)
        self.update_buttons(0.0)

    def update_buttons(self, duration_sec: float | None = None):
        sessions = self.widget.sessions()
        if not sessions:
            self.lbl_days.hide()
            self.lbl_duration.setText("--:--")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return

        real_idx = self.widget.selected_session_index(sessions)
        if real_idx is None:
            return
        self.btn_prev.setEnabled(real_idx > 0)
        self.btn_next.setEnabled(real_idx < len(sessions) - 1)

        # Navigation changes before the replacement data is available. Keep the
        # previous duration visible during that transition instead of flashing
        # a synthetic 0:00 value.
        if duration_sec is None:
            return

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
        self.stream_start_time = 0.0
        self.buckets: list[int] = []
        self.is_streaming = False
        self.error_msg = ""
        self.hover_idx: int | None = None
        self.timeline_stages: list[TimelineStage] = []
        self.timeline_layouts: list[StageLayout] = []
        self.icon_bytes: dict[str, bytes] = {}
        self.tooltip_icon_data_urls: dict[str, str] = {}
        self.activity_data: ActivityTimelineData | None = None
        self.activity_layout: ActivityLayoutSnapshot | None = None
        self.activity_image: QImage | None = None
        self._activity_static_layer: QPixmap | None = None
        self._activity_hover_layer: QPixmap | None = None
        self._activity_cache_build_ms = 0.0
        self._activity_paint_samples_ms: list[float] = []
        self._layout_refresh_size: tuple[int, int] | None = None
        self._pixmaps = PixmapLru()

    def set_timeline(
        self,
        stages: list[TimelineStage],
        icon_bytes: dict[str, bytes],
        activity_data: ActivityTimelineData | None = None,
        activity_layout: ActivityLayoutSnapshot | None = None,
        activity_image: QImage | None = None,
    ) -> None:
        self.timeline_stages = stages
        self.icon_bytes = icon_bytes
        self.tooltip_icon_data_urls = {
            icon_hash: f"data:image/png;base64,{base64.b64encode(payload).decode('ascii')}"
            for icon_hash, payload in icon_bytes.items()
        }
        self.activity_data = activity_data
        self.activity_layout = activity_layout
        self.activity_image = activity_image
        self._activity_hover_layer = None
        if activity_layout is not None:
            self.timeline_layouts = []
            self._rebuild_activity_render_cache()
        else:
            self._activity_static_layer = None
            self._rebuild_timeline_layout()

    def _rebuild_timeline_layout(self) -> None:
        icon_bottom = max(self.height() - TIMELINE_MAJOR_TICK_HEIGHT - TIMELINE_ICON_RULER_GAP, 0)
        icon_band_height = min(self.config.icon_band_height, icon_bottom)
        self.timeline_layouts = layout_timeline_stages(
            self.timeline_stages,
            self.width(),
            icon_band_height,
            band_top=icon_bottom - icon_band_height,
            min_size=self.config.icon_min_size,
            max_size=self.config.icon_max_size,
            max_icons=self.config.max_stage_icons,
            min_gap=self.config.icon_min_gap,
            stack_similarity_ratio=self.config.stack_similarity_ratio,
            stack_offset_ratio=self.config.stack_offset_ratio,
            show_passive_foreground=self.config.show_passive_foreground,
        )
        dpr = self.devicePixelRatioF()
        for layout in self.timeline_layouts:
            for placement in layout.icons:
                icon_hash = placement.app.icon_sha256
                payload = self.icon_bytes.get(icon_hash or "")
                if icon_hash and payload:
                    self._pixmaps.prepare(icon_hash, payload, placement.size, dpr)

    def stage_at(self, bucket: int) -> TimelineStage | None:
        return next(
            (stage for stage in self.timeline_stages if stage.start_bucket <= bucket < stage.end_bucket),
            None,
        )

    def activity_tooltip_at(self, bucket: int) -> str:
        return activity_tooltip_html(
            self.activity_layout,
            self.activity_data,
            bucket,
            self.tooltip_icon_data_urls,
        )

    @override
    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        super().resizeEvent(a0)
        if self.activity_layout is None:
            self._rebuild_timeline_layout()
            return
        size = (self.width(), self.height())
        if self.activity_layout.width == self.width():
            self._rebuild_activity_render_cache()
            return
        self._activity_static_layer = None
        if self._layout_refresh_size != size:
            self._layout_refresh_size = size
            QTimer.singleShot(0, self.widget.request_refresh)

    def _control_exclusion_rects(self) -> list[QRectF]:
        overlay_geometry = self.geometry()
        exclusions: list[QRectF] = []
        for control in self.widget.control_overlays:
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

    def set_hover_bucket(self, bucket: int | None) -> None:
        if self.hover_idx == bucket:
            return
        self.hover_idx = bucket
        if self.activity_layout is not None:
            self._rebuild_activity_hover_layer()
        self.update()

    def _new_render_layer(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        layer = QPixmap(max(round(self.width() * dpr), 1), max(round(self.height() * dpr), 1))
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.GlobalColor.transparent)
        return layer

    def _draw_cached_ruler(self, painter: QPainter, bucket_count: int) -> None:
        if bucket_count <= 1:
            return
        width = self.width()
        height = self.height()
        step_x = width / (bucket_count - 1)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        font_metrics = painter.fontMetrics()
        exclusions = self._control_exclusion_rects()
        for index in range(bucket_count):
            value = datetime.fromtimestamp(self.stream_start_time + index * 60)
            if bucket_count > 720:
                minor_tick = value.minute == 30
            else:
                minor_tick = value.minute % 10 == 0
            x = index * step_x
            if value.minute == 0:
                painter.setPen(QPen(QColor(255, 255, 255, 150), 1.5))
                painter.drawLine(QPointF(x, height), QPointF(x, height - TIMELINE_MAJOR_TICK_HEIGHT))
                label = format_compact_time(value)
                label_width = font_metrics.horizontalAdvance(label)
                label_x = ruler_label_x(
                    x,
                    font_metrics.horizontalAdvance(str(value.hour)),
                    font_metrics.horizontalAdvance(":"),
                    label_width,
                    width,
                )
                normal_baseline = height - 14
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
                painter.setPen(QColor(255, 255, 255, 200))
                painter.drawText(QPointF(label_x, label_baseline), label)
            elif minor_tick:
                painter.setPen(QPen(QColor(255, 255, 255, 60), 1.0))
                painter.drawLine(QPointF(x, height), QPointF(x, height - 5))

    def _rebuild_activity_render_cache(self) -> None:
        layout = self.activity_layout
        if layout is None or self.width() <= 0 or self.height() <= 0 or layout.width != self.width():
            self._activity_static_layer = None
            return
        started = time.perf_counter()
        image = self.activity_image
        if image is None:
            image = render_activity_image(
                layout,
                self.buckets,
                self.icon_bytes,
                self.config,
                self.height(),
                self.devicePixelRatioF(),
            )
        layer = QPixmap.fromImage(image)
        painter = QPainter(layer)
        self._draw_cached_ruler(painter, layout.bucket_count)
        painter.end()
        self._activity_static_layer = layer
        self._activity_cache_build_ms = (time.perf_counter() - started) * 1000
        self._layout_refresh_size = None
        self._rebuild_activity_hover_layer()

    def _rebuild_activity_hover_layer(self) -> None:
        layout = self.activity_layout
        if layout is None or self.hover_idx is None or self.width() <= 0 or self.height() <= 0:
            self._activity_hover_layer = None
            return
        layer = self._new_render_layer()
        painter = QPainter(layer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left_bucket = max(self.hover_idx - HOVER_CURVE_RADIUS_BUCKETS, 0)
        right_bucket = min(self.hover_idx + HOVER_CURVE_RADIUS_BUCKETS, layout.bucket_count - 1)
        left = left_bucket / max(layout.bucket_count - 1, 1) * self.width()
        right = right_bucket / max(layout.bucket_count - 1, 1) * self.width()
        painter.fillRect(QRectF(left, 0, max(right - left, 1), self.height()), QColor(255, 255, 255, 20))

        density_points = density_curve_points(
            self.buckets,
            self.width(),
            self.height(),
            min(layout.band_height, self.height()),
        )
        if len(density_points) == layout.bucket_count:
            painter.strokePath(
                smooth_curve_path(density_points, left_bucket, right_bucket),
                QPen(QColor(255, 255, 255, 220), 1.5),
            )

        track_height = layout.band_height / max(layout.policy.max_tracks, 1)
        cloud_height = max(track_height - 4, 1)
        painter.save()
        painter.setClipRect(QRectF(left, 0, max(right - left, 1), layout.band_height))
        for geometry in layout.components:
            if geometry.track < 0 or geometry.x_end < left or geometry.x_start > right or not geometry.envelope:
                continue
            baseline = geometry.track * track_height + 2.0
            first_sample = max(bisect_left(geometry.envelope, left, key=lambda sample: sample.pixel) - 1, 0)
            last_sample = min(
                bisect_right(geometry.envelope, right, key=lambda sample: sample.pixel) + 1,
                len(geometry.envelope),
            )
            points = [
                QPointF(sample.pixel, baseline + sample.non_direct_level * cloud_height)
                for sample in geometry.envelope[first_sample:last_sample]
            ]
            pen = QPen(mix_color_with_white(geometry.component.color.rgb), 1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            if len(points) == 1:
                painter.setPen(pen)
                painter.drawPoint(points[0])
            else:
                painter.strokePath(smooth_curve_path(points), pen)
        painter.restore()
        painter.end()
        self._activity_hover_layer = layer

    @override
    def paintEvent(self, a0: QPaintEvent | None) -> None:
        paint_started = time.perf_counter()
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
            painter.drawText(QRectF(0, h * 0.75, w, 20), Qt.AlignmentFlag.AlignCenter, self.error_msg)
            return

        if self.activity_layout is not None:
            if self._activity_static_layer is not None:
                painter.drawPixmap(QPointF(0, 0), self._activity_static_layer)
            if self._activity_hover_layer is not None:
                painter.drawPixmap(QPointF(0, 0), self._activity_hover_layer)
            self._activity_paint_samples_ms.append((time.perf_counter() - paint_started) * 1000)
            if len(self._activity_paint_samples_ms) >= 120:
                ordered = sorted(self._activity_paint_samples_ms)
                p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
                logging.debug(
                    "pieces activity paint metrics p95_ms=%.3f max_ms=%.3f samples=%d",
                    p95,
                    max(ordered),
                    len(ordered),
                )
                self._activity_paint_samples_ms.clear()
            return

        n = len(self.buckets)
        if self.hover_idx is not None and self.hover_idx >= n:
            self.hover_idx = None

        step_x = w / (n - 1) if n > 1 else w
        density_top = min(self.config.icon_band_height, h) if self.timeline_layouts else 0

        # Draw Hover Highlight (±5 mins)
        if self.hover_idx is not None:
            x_min = max(0, self.hover_idx - HOVER_CURVE_RADIUS_BUCKETS) * step_x
            x_max = min(n - 1, self.hover_idx + HOVER_CURVE_RADIUS_BUCKETS) * step_x
            # Faint background for the highlighted section
            painter.fillRect(QRectF(x_min, 0, x_max - x_min, h), QColor(255, 255, 255, 20))

        max_val = max(self.buckets) if self.buckets else 0
        points = draw_density_heatmap(
            painter,
            self.buckets,
            self.config,
            w,
            h,
            density_top=density_top,
        )

        dpr = self.devicePixelRatioF()
        for layout in self.timeline_layouts:
            for placement in layout.icons:
                icon_hash = placement.app.icon_sha256
                if not icon_hash:
                    continue
                pixmap = self._pixmaps.get(icon_hash, placement.size, dpr)
                if pixmap is None:
                    continue
                painter.setOpacity(placement.opacity)
                painter.drawPixmap(
                    QRectF(placement.x, placement.y, placement.size, placement.size),
                    pixmap,
                    QRectF(pixmap.rect()),
                )
        painter.setOpacity(1.0)

        # Hover stroke highlight (solid bright curve over the hovered ±5min section)
        if self.hover_idx is not None and n > 1 and max_val > 0 and len(points) == n:
            start_i = max(0, self.hover_idx - HOVER_CURVE_RADIUS_BUCKETS)
            end_i = min(n - 1, self.hover_idx + HOVER_CURVE_RADIUS_BUCKETS)
            hl_path = smooth_curve_path(points, start_i, end_i)

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
                if self.hover_idx is not None and (
                    self.hover_idx - HOVER_CURVE_RADIUS_BUCKETS <= i <= self.hover_idx + HOVER_CURVE_RADIUS_BUCKETS
                ):
                    is_hovered = True

                # Determine which minor ticks to show based on duration
                if n > 720:
                    is_minor_tick = dt.minute == 30
                else:
                    is_minor_tick = dt.minute % 10 == 0

                # Major tick on the hour (e.g. 14:00)
                if dt.minute == 0:
                    pen_major = QPen(QColor(255, 255, 255, 255 if is_hovered else 150))
                    pen_major.setWidthF(1.5 if is_hovered else 1.5)
                    painter.setPen(pen_major)

                    x = i * step_x
                    painter.drawLine(QPointF(x, h), QPointF(x, h - TIMELINE_MAJOR_TICK_HEIGHT))
                    label = format_compact_time(dt)
                    label_width = font_metrics.horizontalAdvance(label)
                    label_x = ruler_label_x(
                        x,
                        font_metrics.horizontalAdvance(str(dt.hour)),
                        font_metrics.horizontalAdvance(":"),
                        label_width,
                        w,
                    )
                    normal_baseline = h - 14
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
                    painter.setPen(QColor(255, 255, 255, 255 if is_hovered else 200))
                    painter.drawText(QPointF(label_x, label_baseline), label)
                elif is_minor_tick:
                    pen_minor = QPen(QColor(255, 255, 255, 200 if is_hovered else 60))
                    pen_minor.setWidthF(1.5 if is_hovered else 1.0)
                    painter.setPen(pen_minor)

                    x = i * step_x
                    painter.drawLine(QPointF(x, h), QPointF(x, h - 5))


class FetchWorker(QThread):
    data_fetched = pyqtSignal(object)

    def __init__(
        self,
        config: PiecesDensityConfig,
        time_source: TimeSource,
        known_start_time: float = 0.0,
        last_streaming_time: float = 0.0,
        session_override: float = 0.0,
        force_session: bool = False,
        session_end_bound: float = 0.0,
        interval_error: str = "",
        layout_width: int = 1,
        layout_height: int = 1,
        layout_dpr: float = 1.0,
        previous_activity_layout: ActivityLayoutSnapshot | None = None,
        previous_activity_image: QImage | None = None,
        previous_density_buckets: tuple[int, ...] = (),
    ):
        super().__init__()
        self.session_end_bound = session_end_bound
        self.config = config
        self.time_source = time_source
        self.known_start_time = known_start_time
        self.last_streaming_time = last_streaming_time
        self.session_override = session_override
        self.force_session = force_session
        self.interval_error = interval_error
        self.layout_width = max(layout_width, 1)
        self.layout_height = max(layout_height, 1)
        self.layout_dpr = max(layout_dpr, 1.0)
        self.previous_activity_layout = previous_activity_layout
        self.previous_activity_image = previous_activity_image
        self.previous_density_buckets = previous_density_buckets
        self._is_running = True

    def cancel(self):
        self._is_running = False

    def _emit_snapshot(
        self,
        buckets: list[int],
        has_interval: bool,
        start_time: float,
        duration: float,
        error: str,
        stages: list[TimelineStage] | None = None,
        icon_bytes: dict[str, bytes] | None = None,
        activity_data: ActivityTimelineData | None = None,
        activity_layout: ActivityLayoutSnapshot | None = None,
        activity_image: QImage | None = None,
        timings_ms: tuple[tuple[str, float], ...] = (),
    ) -> None:
        self.data_fetched.emit(
            DensityTimelineSnapshot(
                buckets=buckets,
                has_interval=has_interval,
                start_time=start_time,
                duration=duration,
                error=error,
                source_value=self.time_source.value,
                stages=stages or [],
                icon_bytes=icon_bytes or {},
                activity_data=activity_data,
                activity_layout=activity_layout,
                activity_image=activity_image,
                timings_ms=timings_ms,
            )
        )

    def run(self):
        try:
            # event-logger is the only provider of interval boundaries.
            if self.interval_error:
                self._emit_snapshot([], True, 0.0, 0.0, self.interval_error)
                return

            if self.session_override <= 0:
                self._emit_snapshot(
                    [],
                    False,
                    0.0,
                    0.0,
                    f"No canonical interval in {self.config.truth_time_db_path}",
                )
                return

            stream_start_time = self.session_override
            interval_end = self.session_end_bound or time.time()
            total_duration_sec = max(interval_end - stream_start_time, 0.0)

            # Honour cancellation request before the SQLite query
            if not self._is_running:
                return

            resolved_source = resolve_density_source(
                self.config.density_source,
                self.config.screenpipe_graph_db_path,
                self.config.pieces_db_path,
            )
            if not os.path.exists(resolved_source.database_path):
                self._emit_snapshot(
                    [],
                    True,
                    0.0,
                    0.0,
                    f"{resolved_source.source.value} DB missing at: {resolved_source.database_path}",
                )
                return
            timings: list[tuple[str, float]] = []
            density_started = time.perf_counter()
            raw_buckets = _DENSITY_BUCKET_CACHE.load(
                resolved_source,
                stream_start_time,
                interval_end,
                closed_interval=self.session_end_bound > 0,
                cancelled=lambda: not self._is_running,
            )
            timings.append(("density_load", (time.perf_counter() - density_started) * 1000))
            if not self._is_running:
                return
            buckets = integrate_density(raw_buckets)
            stages: list[TimelineStage] = []
            icon_bytes: dict[str, bytes] = {}
            activity_data: ActivityTimelineData | None = None
            activity_layout: ActivityLayoutSnapshot | None = None
            activity_image: QImage | None = None
            if self.config.show_app_timeline:
                try:
                    activity_started = time.perf_counter()
                    activity_data = _APP_TIMELINE_CACHE.load_activity(
                        resolved_source,
                        stream_start_time,
                        interval_end,
                        closed_interval=self.session_end_bound > 0,
                        cancelled=lambda: not self._is_running,
                    )
                    if not self._is_running:
                        return
                    timings.append(("activity_load", (time.perf_counter() - activity_started) * 1000))
                    if activity_data.contract_version >= 2:
                        policy = ActivityLayoutPolicy(
                            max_tracks=self.config.max_app_tracks,
                            min_icon_size=self.config.icon_min_size,
                            max_icon_size=self.config.icon_max_size,
                            stack_offset_ratio=self.config.stack_offset_ratio,
                            stack_similarity=self.config.stack_similarity_ratio,
                        )
                        icon_bottom = max(
                            self.layout_height - TIMELINE_MAJOR_TICK_HEIGHT - TIMELINE_ICON_RULER_GAP,
                            1,
                        )
                        band_height = min(self.config.icon_band_height, icon_bottom)
                        previous = self.previous_activity_layout
                        if (
                            activity_data.cache_hit
                            and previous is not None
                            and previous.bucket_count == len(raw_buckets)
                            and previous.width == self.layout_width
                            and previous.band_height == band_height
                            and previous.policy == policy
                        ):
                            activity_layout = previous
                            timings.append(("activity_layout", 0.0))
                        else:
                            layout_started = time.perf_counter()
                            activity_layout = build_activity_layout(
                                activity_data.app_records,
                                activity_data.global_records,
                                len(raw_buckets),
                                self.layout_width,
                                band_height,
                                policy=policy,
                                previous=previous,
                                changed_from_bucket=activity_data.changed_from_bucket,
                                cancelled=lambda: not self._is_running,
                            )
                            timings.append(("activity_layout", (time.perf_counter() - layout_started) * 1000))
                        icon_hashes = {
                            geometry.component.icon_sha256
                            for geometry in activity_layout.components
                            if geometry.component.icon_sha256
                        }
                    else:
                        stages = build_timeline_stages(list(activity_data.app_records), len(raw_buckets))
                        icon_hashes = {
                            app.icon_sha256
                            for stage in stages
                            for app in stage.apps[: self.config.max_stage_icons]
                            if app.icon_sha256
                        }
                    icon_started = time.perf_counter()
                    icon_bytes = _ICON_BYTE_CACHE.load(resolved_source.database_path, icon_hashes)
                    timings.append(("icon_load", (time.perf_counter() - icon_started) * 1000))
                    if activity_layout is not None:
                        if (
                            activity_layout is self.previous_activity_layout
                            and self.previous_activity_image is not None
                            and tuple(buckets) == self.previous_density_buckets
                        ):
                            activity_image = self.previous_activity_image
                            timings.append(("activity_raster", 0.0))
                        else:
                            raster_started = time.perf_counter()
                            activity_image = render_activity_image(
                                activity_layout,
                                buckets,
                                icon_bytes,
                                self.config,
                                self.layout_height,
                                self.layout_dpr,
                            )
                            timings.append(("activity_raster", (time.perf_counter() - raster_started) * 1000))
                except Exception as error:
                    if not self._is_running:
                        return
                    activity_data = None
                    activity_layout = None
                    activity_image = None
                    logging.warning("App timeline unavailable; keeping heatmap-only rendering: %s", error)

            # Keep leading zero-activity buckets so the heatmap remains aligned with the
            # canonical interval selected from event-logger.

            if not self._is_running:
                return

            self._emit_snapshot(
                buckets,
                True,
                stream_start_time,
                total_duration_sec,
                "",
                stages,
                icon_bytes,
                activity_data,
                activity_layout,
                activity_image,
                tuple(timings),
            )

        except Exception as e:
            if not self._is_running:
                return
            logging.error("Error fetching activity density: %s", e)
            self._emit_snapshot([], True, 0.0, 0.0, f"Error: {e}")


class PiecesDensityWidget(BaseWidget):
    """
    A widget that anchors to the yasb bar, but spawns a full-width overlay
    beneath the bar for the Pieces Workstream density heatmap.
    """

    validation_schema = PiecesDensityConfig

    _toggle_req_signal = pyqtSignal(str)
    _time_source_changed_signal = pyqtSignal(str, str)

    def __init__(self, config: PiecesDensityConfig):
        super().__init__(class_name="pieces-density-widget")
        self.config = config

        resolved_density = resolve_density_source(
            config.density_source,
            config.screenpipe_graph_db_path,
            config.pieces_db_path,
        )
        logging.info(
            "Activity density source=%s database=%s fallback_reason=%s",
            resolved_density.source.value,
            resolved_density.database_path,
            resolved_density.fallback_reason or "none",
        )

        self._is_active = True
        self._time_source = TimeSource.YOUTUBE_LIVESTREAM
        self._overlay = DensityOverlay(self, self.config)
        self._session_manager = SessionManager(config.truth_time_db_path)
        self._selected_session_start: float | None = None
        self._controls_left = ControlsOverlayLeft(self)
        self._controls_right = ControlsOverlayRight(self)
        self._worker: FetchWorker | None = None
        self._refresh_pending = False
        self._stream_start_time = 0.0
        self._last_streaming_time = 0.0
        self._activity_interval_key: tuple[str, float] | None = None
        self._connected_anim = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._fetch_data)
        self._timer.start(self.config.poll_interval_sec * 1000)
        self.register_callback("toggle_pieces_density", self._toggle_overlay)

        self._event_service.register_event("toggle_pieces_widget", self._toggle_req_signal)
        self._toggle_req_signal.connect(self._toggle_pieces_state)

        self._event_service.register_event("pieces_time_source_changed", self._time_source_changed_signal)
        self._time_source_changed_signal.connect(self._on_time_source_changed)

        # Drop to the bottom of the bar's Z-order to prevent covering other widgets
        self.lower()

        # Polling for hover without triggering Qt's window raising on mouse hover
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(50)
        self._hover_timer.timeout.connect(self._poll_hover)
        self._hover_timer.start()

    @property
    def control_overlays(self) -> tuple[ControlsOverlayLeft, ControlsOverlayRight]:
        return self._controls_left, self._controls_right

    def sessions(self) -> list[float]:
        return self._session_manager.get_sessions(self._time_source)

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

        SetWindowPos(controls_l_hwnd, bar_hwnd, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
        SetWindowPos(controls_r_hwnd, controls_l_hwnd, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
        SetWindowPos(overlay_hwnd, controls_r_hwnd, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)

    def _show_overlay_below_bar(self):
        self._update_overlay_geometry()
        self._place_overlay_below_bar(allow_hidden=True)
        self._overlay.show()
        self._place_overlay_below_bar()

    def _poll_hover(self):
        if (
            not self._overlay
            or not self._overlay.isVisible()
            or not self.config.show_tooltip
            or not self._overlay.is_streaming
        ):
            if getattr(self._overlay, "hover_idx", None) is not None:
                self._overlay.set_hover_bucket(None)
                QToolTip.hideText()
            return

        cursor_pos = QCursor.pos()
        geo = self._overlay.geometry()

        if geo.contains(cursor_pos):
            w = self._overlay.width()
            if w <= 0:
                return

            if self._overlay.error_msg:
                QToolTip.showText(cursor_pos, f"Error: {self._overlay.error_msg}")
                return

            if not self._overlay.buckets:
                QToolTip.showText(cursor_pos, "0 Events")
                return

            # Map mouse global X to time bucket
            local_x = cursor_pos.x() - geo.x()
            bucket_index = int((local_x / w) * len(self._overlay.buckets))
            bucket_index = min(max(bucket_index, 0), len(self._overlay.buckets) - 1)

            if getattr(self._overlay, "hover_idx", None) != bucket_index:
                self._overlay.set_hover_bucket(bucket_index)

            val = self._overlay.buckets[bucket_index]
            ts = self._overlay.stream_start_time + bucket_index * 60
            tooltip_text = density_tooltip_html(val, datetime.fromtimestamp(ts))
            app_tooltip = self._overlay.activity_tooltip_at(bucket_index)
            if not app_tooltip:
                app_tooltip = stage_tooltip_html(self._overlay.stage_at(bucket_index))
            if app_tooltip:
                tooltip_text = f"{tooltip_text}<br>{app_tooltip}"

            QToolTip.showText(cursor_pos, tooltip_text)
        else:
            if getattr(self._overlay, "hover_idx", None) is not None:
                self._overlay.set_hover_bucket(None)
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

        self._update_overlay_geometry()

        override_time = 0.0
        end_bound = 0.0
        sessions = self._session_manager.get_sessions(self._time_source)
        if sessions:
            real_idx = self.selected_session_index(sessions)
            if real_idx is not None:
                override_time = sessions[real_idx]
                end_bound = self._session_manager.get_session_end(self._time_source, override_time) or 0.0

        force_session = override_time > 0
        interval_error = self._session_manager.last_error
        requested_interval_key = (self._time_source.value, override_time)
        same_interval = self._activity_interval_key == requested_interval_key

        self._worker = FetchWorker(
            self.config,
            self._time_source,
            self._stream_start_time,
            self._last_streaming_time,
            override_time,
            force_session,
            end_bound,
            interval_error,
            self._overlay.width(),
            self._overlay.height(),
            self._overlay.devicePixelRatioF(),
            self._overlay.activity_layout if same_interval else None,
            self._overlay.activity_image if same_interval else None,
            tuple(self._overlay.buckets) if same_interval else (),
        )
        self._worker.data_fetched.connect(self._on_data_fetched)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def request_refresh(self) -> None:
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

    def selected_session_index(self, sessions: list[float]) -> int | None:
        index = selected_session_index(sessions, self._selected_session_start)
        if (
            index is not None
            and self._selected_session_start is not None
            and abs(sessions[index] - self._selected_session_start) >= 0.001
        ):
            self._selected_session_start = None
        return index

    def _on_data_fetched(self, snapshot: DensityTimelineSnapshot):
        if not getattr(self, "_is_active", True):
            return
        # Discard stale result: time source was toggled while the worker was running.
        # The next timer tick will re-fetch with the correct source.
        if TimeSource.parse(snapshot.source_value) is not self._time_source:
            return

        self._controls_left.update_buttons()
        self._controls_right.update_buttons(snapshot.duration)

        unchanged_visual = (
            snapshot.activity_layout is not None
            and snapshot.activity_layout is self._overlay.activity_layout
            and snapshot.activity_image is self._overlay.activity_image
            and snapshot.buckets == self._overlay.buckets
            and snapshot.error == self._overlay.error_msg
            and snapshot.start_time == self._overlay.stream_start_time
        )
        self._overlay.buckets = snapshot.buckets
        self._overlay.is_streaming = snapshot.has_interval
        self._overlay.stream_start_time = snapshot.start_time
        self._overlay.error_msg = snapshot.error
        self._activity_interval_key = (snapshot.source_value, snapshot.start_time)
        if not unchanged_visual:
            self._overlay.set_timeline(
                snapshot.stages,
                snapshot.icon_bytes,
                snapshot.activity_data,
                snapshot.activity_layout,
                snapshot.activity_image,
            )
        logging.debug(
            "pieces activity worker metrics timings_ms=%s cache_build_ms=%.3f unchanged=%s",
            dict(snapshot.timings_ms),
            self._overlay._activity_cache_build_ms,
            unchanged_visual,
        )

        if snapshot.has_interval:
            self._stream_start_time = snapshot.start_time
            self._last_streaming_time = time.time()
            if not self._overlay.isVisible():
                self._show_overlay_below_bar()
                self._controls_left.show()
                self._controls_right.show()
            else:
                self._update_overlay_geometry()
                self._place_overlay_below_bar()
            if not unchanged_visual:
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
        self._controls_right.setGeometry(x + w - cw_r - 5, y + h - ch_r - 5, cw_r, ch_r)

    def _toggle_overlay(self):
        if self._overlay.isVisible():
            self._overlay.hide()
            self._controls_left.hide()
            self._controls_right.hide()
        else:
            self._show_overlay_below_bar()
            self._controls_left.show()
            self._controls_right.show()

    def select_previous_session(self) -> None:
        sessions = self.sessions()
        if not sessions:
            return
        index = self.selected_session_index(sessions)
        if index is not None and index > 0:
            self._selected_session_start = sessions[index - 1]
            self._controls_left.update_buttons()
            self._controls_right.update_buttons()
            self.request_refresh()

    def select_next_session(self) -> None:
        sessions = self.sessions()
        index = self.selected_session_index(sessions)
        if index is not None and index < len(sessions) - 1:
            next_index = index + 1
            self._selected_session_start = None if next_index == len(sessions) - 1 else sessions[next_index]
            self._controls_left.update_buttons()
            self._controls_right.update_buttons()
            self.request_refresh()

    def _on_time_source_changed(self, source_value: str, screen_name: str):
        if screen_name != self.screen_name:
            return
        self._time_source = TimeSource.parse(source_value)
        self._stream_start_time = 0.0  # Reset known start time when switching modes
        self._selected_session_start = None
        self.request_refresh()

    def _toggle_pieces_state(self, screen_name: str):
        if screen_name != self.screen_name:
            return

        self._is_active = not self._is_active
        if self._is_active:
            self._timer.start(self.config.poll_interval_sec * 1000)
            self.request_refresh()
        else:
            self._timer.stop()
            self._refresh_pending = False
            if self._worker and self._worker.isRunning():
                self._worker.cancel()
            if self._overlay.isVisible():
                self._overlay.hide()
                self._controls_left.hide()
                self._controls_right.hide()

        self._event_service.emit_event("pieces_widget_state_changed", self._is_active, self.screen_name)

    @override
    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        self.lower()

        # Connect to bar animation signals to stay in sync
        bar_window = self.window()
        if hasattr(bar_window, "animation_tick") and not self._connected_anim:
            animated_bar = cast(_AnimatedBar, bar_window)
            animated_bar.animation_tick.connect(self._update_overlay_geometry)
            animated_bar.animation_finished.connect(self._update_overlay_geometry)
            if hasattr(bar_window, "opacity_tick"):
                animated_bar.opacity_tick.connect(self._overlay.setWindowOpacity)
                animated_bar.opacity_tick.connect(self._controls_left.setWindowOpacity)
                animated_bar.opacity_tick.connect(self._controls_right.setWindowOpacity)
            self._connected_anim = True

        # Initial geometry update and fetch
        QTimer.singleShot(100, self._fetch_data)

        # Ensure overlay stays under the bar window when the bar is shown again
        self._place_overlay_below_bar()

    @override
    def hideEvent(self, a0: QHideEvent | None) -> None:
        super().hideEvent(a0)
        if self._overlay:
            self._overlay.hide()
            self._controls_left.hide()
            self._controls_right.hide()
