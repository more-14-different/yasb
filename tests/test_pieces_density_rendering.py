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
    mix_color_with_white,
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


def test_activity_tooltip_moves_global_totals_to_header_and_keeps_app_shares():
    records = (app_minute(3, direct=2, non_direct=5, color=0x204060),)
    reasons = (
        UnattributedReasonCount("non_direct_activity", "missing_app_identity", 2),
        UnattributedReasonCount("non_direct_activity", "already_accounted_for", 0),
    )
    globals_ = (GlobalMinuteRecord(3, 3, 7, 2, 5, 1, 2, reasons),)
    data = ActivityTimelineData(records, globals_, 2)
    layout = build_activity_layout(records, globals_, 10, 500, 50)
    icon_hash = records[0].icon_sha256
    assert icon_hash is not None

    tooltip = activity_tooltip_html(layout, data, 3, {icon_hash: "data:image/png;base64,AAAA"})

    assert "app" in tooltip
    assert "3 input" in tooltip
    assert "7 activity" in tooltip
    assert "status" in tooltip
    assert "attribution" in tooltip
    assert tooltip.count('<th align="left" style="white-space: nowrap;">') == 3
    assert tooltip.count('<th align="right" style="white-space: nowrap;">') == 2
    assert "app:code" in tooltip
    assert '<img src="data:image/png;base64,AAAA"' in tooltip
    assert "●" not in tooltip
    assert "◆" not in tooltip
    assert "2 (67%)" in tooltip
    assert "5 (71%)" in tooltip
    assert "(100%)" not in tooltip
    assert "<b>Global</b>" not in tooltip
    assert "residual" not in tooltip
    assert "missing app identity" not in tooltip
    assert "fully attributed" not in tooltip


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


def test_activity_hover_draws_ten_minute_global_and_app_curve_outlines(qapp: QApplication):
    base_rgb = 0x204060
    records = [app_minute(bucket, non_direct=5, icon=False, color=base_rgb) for bucket in range(21)]
    globals_ = [global_minute(bucket, 0, 5) for bucket in range(21)]
    layout = build_activity_layout(records, globals_, 21, 210, 50)
    overlay = DensityOverlay(cast(PiecesDensityWidget, _WidgetStub()), PiecesDensityConfig())
    overlay.resize(210, 85)
    overlay.is_streaming = True
    overlay.buckets = [5] * 21
    overlay.set_timeline([], {}, ActivityTimelineData(tuple(records), tuple(globals_), 2), layout)

    overlay.set_hover_bucket(10)

    assert overlay._activity_hover_layer is not None
    image = overlay._activity_hover_layer.toImage()
    highlighted_x = [x for x in range(image.width()) if image.pixelColor(x, 30).alpha() > 0]
    assert 104 <= highlighted_x[-1] - highlighted_x[0] <= 106
    assert image.pixelColor(40, 30).alpha() == 0
    assert image.pixelColor(170, 30).alpha() == 0

    global_curve = image.pixelColor(105, 50)
    assert min(global_curve.red(), global_curve.green(), global_curve.blue()) >= 245

    app_curve = image.pixelColor(100, 8)
    expected = mix_color_with_white(base_rgb)
    assert abs(app_curve.red() - expected.red()) <= 4
    assert abs(app_curve.green() - expected.green()) <= 4
    assert abs(app_curve.blue() - expected.blue()) <= 4
    assert app_curve.blue() > app_curve.green() > app_curve.red()


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
