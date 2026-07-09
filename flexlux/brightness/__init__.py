import logging
import platform
from typing import Protocol, Optional


log = logging.getLogger("FlexLux")


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


def get_key_interceptor():
    """Return a brightness-key interceptor for this platform, or None.

    On Darwin a constructed (but not yet started) MacBrightnessKeyInterceptor
    is returned. On every other platform None is returned. Construction is
    cheap; callers own start()/stop() lifecycle. Returns None if construction
    fails so callers can degrade gracefully without touching platform APIs.
    """
    if platform.system() != "Darwin":
        return None
    try:
        from flexlux.brightness.mac_keys import MacBrightnessKeyInterceptor
        return MacBrightnessKeyInterceptor()
    except Exception as e:
        log.warning("Could not create brightness key interceptor: %s", e)
        return None
