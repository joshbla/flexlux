import platform
from dataclasses import dataclass

from PyQt5.QtCore import Qt


@dataclass(frozen=True)
class PlatformUIConfig:
    overlay_flags: int
    app_window_flags: int
    panel_y_from_top: bool
    panel_top_offset: int
    use_event_filter: bool
    use_manual_tray_menu: bool


_MACOS = PlatformUIConfig(
    overlay_flags=Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus,
    # Qt.Popup (not Qt.Tool): Tool windows map to NSPanel with
    # hidesOnDeactivate, so macOS refuses to show them while the app is
    # inactive (tray clicks don't activate the app on modern macOS), and Qt's
    # isVisible() desyncs from reality once Cocoa hides the panel — making
    # tray clicks appear to do nothing. Popup windows show without app
    # activation and auto-close on outside clicks with Qt state kept in sync.
    app_window_flags=Qt.Popup | Qt.FramelessWindowHint,
    panel_y_from_top=True,
    panel_top_offset=30,
    use_event_filter=False,
    use_manual_tray_menu=True,
)

_WINDOWS = PlatformUIConfig(
    overlay_flags=Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool,
    app_window_flags=Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
    panel_y_from_top=False,
    panel_top_offset=30,
    use_event_filter=True,
    use_manual_tray_menu=False,
)

_LINUX = PlatformUIConfig(
    overlay_flags=Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.X11BypassWindowManagerHint,
    app_window_flags=Qt.Tool | Qt.FramelessWindowHint,
    panel_y_from_top=False,
    panel_top_offset=30,
    use_event_filter=True,
    use_manual_tray_menu=False,
)


def get_ui_config() -> PlatformUIConfig:
    system = platform.system()
    if system == "Darwin":
        return _MACOS
    elif system == "Windows":
        return _WINDOWS
    return _LINUX
