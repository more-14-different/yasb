import math
import time
from typing import cast
from unittest.mock import patch

from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication

from core.validation.widgets.yasb.pieces_density import PiecesDensityConfig
from core.widgets.yasb.pieces_density import (
    DensityOverlay,
    PiecesDensityWidget,
    activity_tooltip_html,
    render_activity_image,
)
from core.widgets.yasb.pieces_density_activity import build_activity_layout
from core.widgets.yasb.pieces_density_source import (
    ActivityTimelineData,
    GlobalMinuteRecord,
    UnattributedReasonCount,
)
from tests.test_pieces_density_activity import app_minute, global_minute


class _WidgetStub:
    control_overlays = ()

    def request_refresh(self):
        pass


def _fixture(width: int = 3_840):
    records = [
        app_minute(
            bucket,
            f"app:{app}",
            direct=1 if bucket % (11 + app) == 0 else 0,
            non_direct=1 if bucket % (3 + app) == 0 else 0,
            icon=False,
        )
        for app in range(6)
        for bucket in range(1_440)
    ]
    globals_ = [global_minute(bucket, bucket % 4, (bucket % 9) + 1) for bucket in range(1_440)]
    return records, globals_, build_activity_layout(records, globals_, 1_440, width, 50)


def test_activity_tooltip_exposes_app_signals_global_residual_and_reason():
    records = (app_minute(3, direct=2, non_direct=5, color=0x204060),)
    reason = UnattributedReasonCount("non_direct_activity", "missing_app_identity", 2)
    globals_ = (GlobalMinuteRecord(3, 3, 7, 2, 5, 1, 2, (reason,)),)
    data = ActivityTimelineData(records, globals_, 2)
    layout = build_activity_layout(records, globals_, 10, 500, 50)

    tooltip = activity_tooltip_html(layout, data, 3)

    assert "app:code" in tooltip
    assert "2 input" in tooltip
    assert "5 activity" in tooltip
    assert "3 residual" in tooltip
    assert "missing_app_identity=2" in tooltip


def test_worker_raster_contains_global_direct_and_non_direct_channels():
    records = [app_minute(1, direct=3, non_direct=4, icon=False)]
    globals_ = [global_minute(1, 3, 4)]
    layout = build_activity_layout(records, globals_, 4, 400, 50)

    image = render_activity_image(layout, [0, 4, 0, 0], {}, PiecesDensityConfig(), 85, 1.0)

    assert not image.isNull()
    assert image.pixelColor(100, 82).alpha() > 0
    assert any(image.pixelColor(100, y).alpha() > 0 for y in range(10, 80))


def test_worker_raster_restores_multicolor_smooth_density_surface():
    layout = build_activity_layout([], [], 4, 400, 50)
    image = render_activity_image(layout, [0, 8, 0, 0], {}, PiecesDensityConfig(), 85, 1.0)

    hot = image.pixelColor(133, 52)
    middle = image.pixelColor(133, 66)
    cool = image.pixelColor(133, 81)
    assert hot.red() > hot.blue()
    assert middle.red() > middle.blue()
    assert cool.blue() > cool.red()


def test_cold_layout_plus_worker_raster_p95_stays_within_budget():
    records, globals_, _ = _fixture()
    config = PiecesDensityConfig()
    samples = []
    for _ in range(20):
        started = time.perf_counter()
        layout = build_activity_layout(records, globals_, 1_440, 3_840, 50)
        render_activity_image(layout, [index % 11 for index in range(1_440)], {}, config, 85, 1.0)
        samples.append((time.perf_counter() - started) * 1000)

    p95 = sorted(samples)[math.ceil(len(samples) * 0.95) - 1]
    assert p95 <= 150.0, f"cold layout plus raster p95 was {p95:.2f} ms"


def test_cached_overlay_swap_and_paint_stay_within_budgets(qapp: QApplication):
    _, _, layout = _fixture()
    config = PiecesDensityConfig()
    raster = render_activity_image(layout, [1] * 1_440, {}, config, 85, 1.0)
    overlay = DensityOverlay(cast(PiecesDensityWidget, _WidgetStub()), config)
    overlay.resize(3_840, 85)
    overlay.is_streaming = True
    overlay.stream_start_time = 0
    overlay.buckets = [1] * 1_440

    started = time.perf_counter()
    overlay.set_timeline([], {}, None, layout, raster)
    swap_ms = (time.perf_counter() - started) * 1000

    target = QImage(3_840, 85, QImage.Format.Format_ARGB32_Premultiplied)
    samples = []
    with patch(
        "core.widgets.yasb.pieces_density.render_activity_image",
        side_effect=AssertionError("paint rebuilt activity geometry"),
    ):
        for _ in range(30):
            target.fill(0)
            painter = QPainter(target)
            started = time.perf_counter()
            overlay.render(painter)
            painter.end()
            samples.append((time.perf_counter() - started) * 1000)

    p95 = sorted(samples)[math.ceil(len(samples) * 0.95) - 1]
    assert swap_ms <= 4.0, f"GUI snapshot swap was {swap_ms:.2f} ms"
    assert p95 <= 4.0, f"cached 3840 px paint p95 was {p95:.2f} ms"
    assert overlay._activity_cache_build_ms <= 4.0
