from enum import StrEnum


class TimeSource(StrEnum):
    YOUTUBE_LIVESTREAM = "youtube_livestream"
    MACHINE_SESSION = "machine_session"

    @classmethod
    def parse(cls, value: str) -> TimeSource:
        return cls(value)
