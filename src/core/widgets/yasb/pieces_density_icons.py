import hashlib
import re
import threading
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SURFACE_ICON_KINDS = frozenset({"desktop", "taskbar", "system_tray", "file_explorer"})


def surface_icon_variant_key(icon_hash: str, surface_kind: str) -> str:
    return f"{icon_hash}:{surface_kind}" if surface_kind in _SURFACE_ICON_KINDS else icon_hash


def _draw_surface_badge(painter: QPainter, target: int, surface_kind: str) -> None:
    colors = {
        "desktop": QColor(64, 190, 255),
        "taskbar": QColor(183, 128, 255),
        "system_tray": QColor(255, 166, 76),
        "file_explorer": QColor(255, 211, 77),
    }
    color = colors.get(surface_kind)
    if color is None:
        return
    side = max(round(target * 0.56), 7)
    left = target - side
    top = target - side
    bounds = QRectF(left + 0.5, top + 0.5, side - 1.0, side - 1.0)
    border_width = max(target * 0.045, 1.0)
    painter.setPen(QPen(QColor(20, 24, 32, 235), border_width))
    painter.setBrush(color)
    painter.drawRoundedRect(bounds, side * 0.23, side * 0.23)

    ink = QColor(20, 29, 40) if surface_kind == "file_explorer" else QColor(255, 255, 255)
    glyph_pen = QPen(ink, max(target * 0.052, 1.0))
    glyph_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(glyph_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    x = float(left)
    y = float(top)
    s = float(side)
    if surface_kind == "desktop":
        painter.drawRoundedRect(QRectF(x + s * 0.22, y + s * 0.22, s * 0.56, s * 0.39), 1.0, 1.0)
        painter.drawLine(QPointF(x + s * 0.5, y + s * 0.62), QPointF(x + s * 0.5, y + s * 0.76))
        painter.drawLine(QPointF(x + s * 0.35, y + s * 0.77), QPointF(x + s * 0.65, y + s * 0.77))
    elif surface_kind == "taskbar":
        painter.drawLine(QPointF(x + s * 0.2, y + s * 0.7), QPointF(x + s * 0.8, y + s * 0.7))
        for offset in (0.28, 0.5, 0.72):
            painter.drawPoint(QPointF(x + s * offset, y + s * 0.43))
    elif surface_kind == "system_tray":
        painter.drawLine(QPointF(x + s * 0.28, y + s * 0.58), QPointF(x + s * 0.5, y + s * 0.36))
        painter.drawLine(QPointF(x + s * 0.5, y + s * 0.36), QPointF(x + s * 0.72, y + s * 0.58))
        for offset in (0.32, 0.5, 0.68):
            painter.drawPoint(QPointF(x + s * offset, y + s * 0.72))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawRoundedRect(QRectF(x + s * 0.19, y + s * 0.37, s * 0.62, s * 0.4), 1.0, 1.0)
        painter.drawRoundedRect(QRectF(x + s * 0.24, y + s * 0.25, s * 0.28, s * 0.24), 1.0, 1.0)


def prepare_surface_icon(payload: bytes, surface_kind: str, target: int) -> QImage | None:
    source = QImage.fromData(payload, "PNG")
    if source.isNull():
        return None
    target = max(int(target), 1)
    scaled = source.scaled(
        target,
        target,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    image = QImage(target, target, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.drawImage(
        QPointF((target - scaled.width()) / 2, (target - scaled.height()) / 2),
        scaled,
    )
    _draw_surface_badge(painter, target, surface_kind)
    painter.end()
    return image


def surface_icon_png(payload: bytes, surface_kind: str, target: int = 32) -> bytes | None:
    if surface_kind not in _SURFACE_ICON_KINDS:
        return payload
    image = prepare_surface_icon(payload, surface_kind, target)
    if image is None:
        return None
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        return None
    buffer.close()
    return bytes(encoded)


def icon_asset_path(graph_database: str, icon_sha256: str) -> Path | None:
    if not _SHA256.fullmatch(icon_sha256):
        return None
    root = Path(graph_database).resolve().parent
    candidate = (root / "assets" / "app-icons" / "sha256" / icon_sha256[:2] / f"{icon_sha256}.png").resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


class IconByteCache:
    """Thread-safe, hash-verifying source cache used only outside paint."""

    def __init__(self, maximum_entries: int = 256):
        self._maximum_entries = maximum_entries
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def load(self, graph_database: str, hashes: set[str]) -> dict[str, bytes]:
        loaded: dict[str, bytes] = {}
        with self._lock:
            for icon_hash in hashes:
                cached = self._items.get(icon_hash)
                if cached is not None:
                    self._items.move_to_end(icon_hash)
                    loaded[icon_hash] = cached
                    continue
                path = icon_asset_path(graph_database, icon_hash)
                if path is None or not path.is_file():
                    continue
                try:
                    payload = path.read_bytes()
                except OSError:
                    continue
                if hashlib.sha256(payload).hexdigest() != icon_hash or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                    continue
                self._items[icon_hash] = payload
                loaded[icon_hash] = payload
                while len(self._items) > self._maximum_entries:
                    self._items.popitem(last=False)
        return loaded


class PixmapLru:
    """GUI-thread scaled pixmaps keyed by immutable content, size, and DPR."""

    def __init__(self, maximum_entries: int = 256):
        self._maximum_entries = maximum_entries
        self._items: OrderedDict[tuple[str, int, float], QPixmap] = OrderedDict()

    @staticmethod
    def _key(icon_hash: str, logical_size: float, dpr: float) -> tuple[str, int, float]:
        return icon_hash, max(round(logical_size), 1), round(dpr, 3)

    def prepare(self, icon_hash: str, payload: bytes, logical_size: float, dpr: float) -> None:
        key = self._key(icon_hash, logical_size, dpr)
        if key in self._items:
            self._items.move_to_end(key)
            return
        image = QImage.fromData(payload, "PNG")
        if image.isNull():
            return
        target = max(round(key[1] * dpr), 1)
        image = image.scaled(
            target,
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(dpr)
        self._items[key] = pixmap
        while len(self._items) > self._maximum_entries:
            self._items.popitem(last=False)

    def get(self, icon_hash: str, logical_size: float, dpr: float) -> QPixmap | None:
        key = self._key(icon_hash, logical_size, dpr)
        pixmap = self._items.get(key)
        if pixmap is not None:
            self._items.move_to_end(key)
        return pixmap


class ImageLru:
    """Thread-safe worker-side decoded/scaled images keyed by content, size, and DPR."""

    def __init__(self, maximum_entries: int = 256):
        self._maximum_entries = maximum_entries
        self._items: OrderedDict[tuple[str, int, float, str], QImage] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_prepare(
        self,
        icon_hash: str,
        payload: bytes,
        logical_size: float,
        dpr: float,
        surface_kind: str = "application",
    ) -> QImage | None:
        base_key = PixmapLru._key(icon_hash, logical_size, dpr)
        key = (*base_key, surface_kind)
        with self._lock:
            image = self._items.get(key)
            if image is not None:
                self._items.move_to_end(key)
                return image
            target = max(round(base_key[1] * dpr), 1)
            image = prepare_surface_icon(payload, surface_kind, target)
            if image is None:
                return None
            image.setDevicePixelRatio(dpr)
            self._items[key] = image
            while len(self._items) > self._maximum_entries:
                self._items.popitem(last=False)
            return image
