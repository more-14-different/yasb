import math
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.widgets.yasb.pieces_density_activity import (  # noqa: E402
    ActivityLayoutPolicy,
    build_activity_components,
    build_activity_layout,
    resolve_app_color,
    robust_signal_scale,
)
from core.widgets.yasb.pieces_density_source import (  # noqa: E402
    AppMinuteRecord,
    GlobalMinuteRecord,
)


def app_minute(
    bucket: int,
    app_id: str = "app:code",
    *,
    direct: int = 0,
    non_direct: int = 0,
    foreground: int = 0,
    icon: bool = True,
    color: int | None = None,
) -> AppMinuteRecord:
    return AppMinuteRecord(
        bucket=bucket,
        canonical_app_id=app_id,
        display_name=app_id,
        icon_sha256=(app_id.encode().hex() + "0" * 64)[:64] if icon else None,
        engaged_score_milli=(direct * 2000 + non_direct * 100),
        engaged_milliseconds=min((direct + non_direct) * 1000, 60_000),
        foreground_milliseconds=foreground,
        direct_event_count=direct,
        interaction_level="direct" if direct else "passive",
        attribution_confidence_milli=950,
        direct_input_count=direct,
        key_count=direct,
        non_direct_activity_count=non_direct,
        effective_frame_count=non_direct,
        projection_kind="exact",
        representative_color_rgb=color,
        color_algorithm_version=1 if color is not None else None,
        contract_version=2,
    )


def global_minute(bucket: int, direct: int, non_direct: int) -> GlobalMinuteRecord:
    return GlobalMinuteRecord(bucket, direct, non_direct, direct, non_direct, 0, 0, ())


class ActivityComponentTests(unittest.TestCase):
    def test_one_direct_event_always_produces_a_component_and_pin(self):
        snapshot = build_activity_layout(
            [app_minute(7, direct=1)],
            [global_minute(7, 1, 0)],
            60,
            600,
            80,
        )

        self.assertEqual(snapshot.metrics.component_count, 1)
        self.assertTrue(snapshot.components[0].component.has_direct_pin)
        self.assertIsNotNone(snapshot.components[0].direct_pin_x)

    def test_continuous_activity_is_one_component_and_meaningful_gap_splits(self):
        records = [
            app_minute(0, direct=1),
            app_minute(1, non_direct=1),
            app_minute(2, direct=1),
            app_minute(6, direct=1),
        ]

        components, _ = build_activity_components(records, 10)

        self.assertEqual([(item.start_bucket, item.end_bucket) for item in components], [(0, 3), (6, 7)])

    def test_focus_only_minute_can_bridge_but_cannot_create_or_enlarge_signal(self):
        bridged = [
            app_minute(0, direct=2),
            app_minute(1, foreground=60_000),
            app_minute(2, non_direct=2),
        ]
        focus_only = [app_minute(4, foreground=60_000)]

        components, _ = build_activity_components([*bridged, *focus_only], 8)

        self.assertEqual(len(components), 1)
        self.assertEqual(components[0].bridge_buckets, (1,))
        self.assertEqual(components[0].total_direct, 2)
        self.assertEqual(components[0].total_non_direct, 2)

    def test_incremental_rebuild_reuses_components_before_changed_suffix(self):
        original = [app_minute(1, direct=1), app_minute(8, app_id="app:edge", direct=1)]
        first = build_activity_layout(original, [], 12, 240, 60)
        updated = [*original, app_minute(10, app_id="app:terminal", direct=1)]

        second = build_activity_layout(
            updated,
            [],
            12,
            240,
            60,
            previous=first,
            changed_from_bucket=10,
        )

        self.assertEqual(second.metrics.reused_component_count, 1)
        self.assertEqual(second.metrics.component_count, 3)


class ActivityScalingAndPackingTests(unittest.TestCase):
    def test_app_and_global_robust_scales_are_separate_and_deterministic(self):
        records = [app_minute(0, direct=1), app_minute(1, direct=2)]
        globals_ = [global_minute(0, 100, 200), global_minute(1, 120, 240)]

        first = build_activity_layout(records, globals_, 2, 200, 60)
        second = build_activity_layout(records, globals_, 2, 200, 60)

        self.assertEqual(first.app_direct_scale, second.app_direct_scale)
        self.assertLess(first.app_direct_scale.upper_log, first.global_direct_scale.upper_log)
        self.assertEqual(robust_signal_scale([0, 1, 2, 100]), robust_signal_scale([100, 2, 1, 0]))

    def test_non_overlapping_components_reuse_tracks_and_overlaps_do_not(self):
        records = [
            app_minute(0, "app:a", direct=1),
            app_minute(1, "app:a", direct=1),
            app_minute(4, "app:b", direct=1),
            app_minute(0, "app:c", direct=1),
            app_minute(1, "app:c", direct=1),
        ]

        snapshot = build_activity_layout(records, [], 8, 800, 80)
        tracks = {item.component.app_id: item.track for item in snapshot.components}

        self.assertEqual(tracks["app:a"], tracks["app:b"])
        self.assertNotEqual(tracks["app:a"], tracks["app:c"])

    def test_envelopes_are_bounded_by_drawable_pixel_columns(self):
        records = [app_minute(bucket, non_direct=(bucket % 5) + 1) for bucket in range(1_440)]
        globals_ = [global_minute(bucket, bucket % 3, (bucket % 7) + 1) for bucket in range(1_440)]

        snapshot = build_activity_layout(records, globals_, 1_440, 320, 80)

        self.assertLessEqual(len(snapshot.components[0].envelope), 320)
        self.assertLessEqual(len(snapshot.global_envelope), 320)
        self.assertLessEqual(snapshot.metrics.path_vertex_count, 320 * 6)

    def test_dense_overlap_is_track_bounded_without_pairwise_collision_growth(self):
        records = [app_minute(4, f"app:{index:03d}", direct=1) for index in range(200)]
        policy = ActivityLayoutPolicy(max_tracks=5, min_icon_size=14, max_icon_size=14)

        snapshot = build_activity_layout(records, [], 10, 500, 70, policy=policy)

        self.assertEqual(snapshot.metrics.visible_component_count, 5)
        self.assertEqual(snapshot.metrics.hidden_component_count, 195)
        self.assertLess(snapshot.metrics.collision_candidate_count, 100)


class ActivityIconPlacementTests(unittest.TestCase):
    def test_stronger_components_receive_larger_more_opaque_icons(self):
        records = [
            app_minute(0, "app:small", direct=1),
            app_minute(5, "app:large", direct=20),
        ]

        snapshot = build_activity_layout(records, [], 10, 500, 80)
        icons = {item.component.app_id: item.icon for item in snapshot.components}

        assert icons["app:small"] is not None
        assert icons["app:large"] is not None
        self.assertGreater(icons["app:large"].size, icons["app:small"].size)
        self.assertGreater(icons["app:large"].opacity, icons["app:small"].opacity)

    def test_icon_search_moves_from_a_strong_peak_into_a_local_valley(self):
        records = [
            app_minute(0, direct=1),
            app_minute(1, direct=1),
            app_minute(2, direct=40),
            app_minute(3, direct=1),
            app_minute(4, direct=1),
        ]

        snapshot = build_activity_layout(records, [], 5, 500, 80)
        icon = snapshot.components[0].icon

        assert icon is not None
        self.assertGreaterEqual(abs(icon.x + icon.size / 2 - icon.logical_anchor_x), 40)
        self.assertIsNotNone(icon.leader_from_x)

    def test_similar_colliding_icons_stack_but_signal_envelopes_stay_separate(self):
        records = [
            app_minute(0, "app:a", direct=2),
            app_minute(1, "app:a", non_direct=2),
            app_minute(0, "app:b", direct=2),
            app_minute(1, "app:b", non_direct=2),
        ]
        policy = ActivityLayoutPolicy(
            max_tracks=2,
            min_icon_size=16,
            max_icon_size=16,
            icon_search_radius_px=8,
            icon_search_step_px=4,
        )

        snapshot = build_activity_layout(records, [], 20, 200, 30, policy=policy)

        icons = [item.icon for item in snapshot.components if item.icon]
        self.assertEqual(len(icons), 2)
        self.assertIn(1, {icon.stack_depth for icon in icons})
        self.assertEqual(len(snapshot.components), 2)
        self.assertTrue(all(item.envelope for item in snapshot.components))

    def test_dissimilar_colliding_signals_never_merge(self):
        records = [
            app_minute(0, "app:a", direct=4),
            app_minute(1, "app:a", non_direct=1),
            app_minute(0, "app:b", non_direct=1),
            app_minute(1, "app:b", direct=4),
        ]
        policy = ActivityLayoutPolicy(
            max_tracks=2,
            min_icon_size=16,
            max_icon_size=16,
            icon_search_radius_px=8,
            icon_search_step_px=4,
        )

        snapshot = build_activity_layout(records, [], 20, 200, 30, policy=policy)

        self.assertEqual(len(snapshot.components), 2)
        self.assertEqual(snapshot.metrics.icon_count, 1)
        self.assertEqual(snapshot.metrics.hidden_icon_count, 1)

    def test_persisted_color_wins_and_hash_fallback_is_stable(self):
        persisted = resolve_app_color(app_minute(0, color=0x204060))
        first = resolve_app_color(app_minute(0, "app:unknown"))
        second = resolve_app_color(app_minute(1, "app:unknown"))

        self.assertEqual(persisted.hex_rgb, "#204060")
        self.assertEqual(persisted.source, "persisted")
        self.assertEqual(first, second)
        self.assertEqual(first.source, "stable_hash")


class ActivityLayoutPerformanceTests(unittest.TestCase):
    def test_warm_incremental_layout_p95_stays_within_budget(self):
        records = [
            app_minute(
                bucket,
                f"app:{app}",
                direct=1 if bucket % (11 + app) == 0 else 0,
                non_direct=1 if bucket % (3 + app) == 0 else 0,
            )
            for app in range(6)
            for bucket in range(1_440)
        ]
        globals_ = [global_minute(bucket, bucket % 4, (bucket % 9) + 1) for bucket in range(1_440)]
        first = build_activity_layout(records, globals_, 1_440, 3_840, 96)
        samples = []
        for _ in range(50):
            started = time.perf_counter()
            snapshot = build_activity_layout(
                records,
                globals_,
                1_440,
                3_840,
                96,
                previous=first,
                changed_from_bucket=1_439,
            )
            samples.append(time.perf_counter() - started)

        p95 = sorted(samples)[math.ceil(len(samples) * 0.95) - 1]
        self.assertGreater(snapshot.metrics.reused_component_count, 0)
        self.assertLessEqual(p95, 0.025, f"warm incremental layout p95 was {p95 * 1000:.2f} ms")


if __name__ == "__main__":
    unittest.main()
