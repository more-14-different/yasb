import hashlib
import re
import threading
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
