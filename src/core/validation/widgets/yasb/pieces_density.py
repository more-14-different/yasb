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
    truth_time_db_path: str = r"E:\MCP\Projects\event-logger\data\truth_time.sqlite3"

    # Appearance
    widget_height: int = 100 # Can match the screenshot
    
    # Gradient colors for heatmap (cold -> hot)
    color_low: str = "rgba(0, 200, 255, 0.2)"
    color_mid: str = "rgba(255, 150, 0, 0.5)"
    color_high: str = "rgba(255, 50, 0, 0.8)"

    # Interactivity
    show_tooltip: bool = True

    callbacks: PiecesDensityCallbacksConfig = PiecesDensityCallbacksConfig()
