from asyncio import AbstractEventLoop, Event
from os import makedirs, path
from time import time_ns

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from settings import DEFAULT_CONFIG_DIRECTORY


class YASBApplication(QApplication):
    """
    Subclass of QApplication to provide type-safe access to application-wide
    asyncio loop and shutdown events.
    Might also be used to store other application-wide state.
    """

    def __init__(self, args: list[str]):
        super().__init__(args)
        self.loop: AbstractEventLoop | None = None
        self.close_event: Event | None = None
        self._lhm_heartbeat_timer: QTimer | None = None

    def start_lhm_heartbeat(self, interval_ms: int = 2000) -> None:
        cache_dir = path.join(DEFAULT_CONFIG_DIRECTORY, "cache")
        makedirs(cache_dir, exist_ok=True)
        heartbeat_path = path.join(cache_dir, "lhm-temp-agent.heartbeat")

        def write_heartbeat() -> None:
            with open(heartbeat_path, "w", encoding="ascii", newline="") as heartbeat_file:
                heartbeat_file.write(str(time_ns() // 1_000_000))

        write_heartbeat()

        if self._lhm_heartbeat_timer is None:
            timer = QTimer(self)
            timer.setInterval(max(interval_ms, 250))
            timer.timeout.connect(write_heartbeat)
            self.aboutToQuit.connect(timer.stop)
            self._lhm_heartbeat_timer = timer

        if not self._lhm_heartbeat_timer.isActive():
            self._lhm_heartbeat_timer.start()
