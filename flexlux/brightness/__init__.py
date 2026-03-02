import platform
from typing import Protocol, Optional


class BrightnessBackend(Protocol):
    min_brightness: int

    def list_monitors(self) -> list[str]: ...
    def has_hardware_control(self, display: str) -> bool: ...
    def get_brightness(self, display: str) -> Optional[int]: ...
    def set_brightness(self, value: int, display: str) -> None: ...
    def get_display_bounds(self, display: str) -> Optional[tuple]: ...
    def cleanup(self) -> None: ...


def get_backend() -> BrightnessBackend:
    if platform.system() == "Darwin":
        from flexlux.brightness.mac import MacBrightnessBackend
        return MacBrightnessBackend()
    else:
        from flexlux.brightness.sbc_backend import SbcBrightnessBackend
        return SbcBrightnessBackend()
