from pydantic import Field
from core.validation.widgets.base_model import CustomBaseModel, CallbacksConfig

class PiecesToggleCallbacksConfig(CallbacksConfig):
    on_left: str = "toggle_pieces"

class PiecesToggleConfig(CustomBaseModel):
    class_name: str = ""
    label: str = "\uf205"  # FontAwesome toggle-on
    label_alt: str = "\uf204"  # FontAwesome toggle-off
    callbacks: PiecesToggleCallbacksConfig = PiecesToggleCallbacksConfig()
