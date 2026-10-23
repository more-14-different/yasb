from typing import Literal

from pydantic import Field

from core.validation.widgets.base_model import (
    CallbacksConfig,
    CustomBaseModel,
)


class PiecesDensityCallbacksConfig(CallbacksConfig):
    on_left: str = "toggle_pieces_density"


class PiecesDensityConfig(CustomBaseModel):
    class_name: str = ""
    # Data Polling
    poll_interval_sec: int = 10

    # Canonical interval provider
    # "auto" checks EVENT_LOGGER_DB_PATH, nearby repositories, then local app data.
    truth_time_db_path: str = "auto"

    # Raw event density provider. Auto prefers event-logger's Screenpipe graph
    # minute facts and keeps Pieces as an explicitly diagnosed fallback.
    density_source: Literal["auto", "screenpipe_graph", "pieces"] = "auto"
    screenpipe_graph_db_path: str = "auto"
    pieces_db_path: str = "auto"

    # Appearance
    widget_height: int = 100  # Can match the screenshot

    # Gradient colors for heatmap (cold -> hot)
    color_low: str = "rgba(0, 200, 255, 0.2)"
    color_mid: str = "rgba(255, 150, 0, 0.5)"
    color_high: str = "rgba(255, 50, 0, 0.8)"

    # Foreground application timeline
    show_app_timeline: bool = True
    max_stage_icons: int = Field(default=5, ge=1, le=5)
    icon_min_size: int = Field(default=12, ge=8, le=32)
    icon_max_size: int = Field(default=24, ge=8, le=48)
    icon_band_height: int = Field(default=50, ge=20, le=100)
    icon_min_gap: int = Field(default=3, ge=0, le=20)
    stack_similarity_ratio: float = Field(default=0.75, ge=0, le=1)
    stack_offset_ratio: float = Field(default=0.28, ge=0, le=1)
    show_passive_foreground: bool = True

    # Interactivity
    show_tooltip: bool = True

    callbacks: PiecesDensityCallbacksConfig = PiecesDensityCallbacksConfig()
