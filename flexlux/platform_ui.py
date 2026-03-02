import platform
from dataclasses import dataclass

from PyQt5.QtCore import Qt


@dataclass(frozen=True)
class PlatformUIConfig:
    overlay_flags: int
    app_window_flags: int
    slider_handle_width: int
    slider_handle_radius: int
    slider_handle_margin: int
    slider_groove_height: int
    slider_max_height: int
    zone_label_font_size: int
    mid_label_width: int
    panel_y_from_top: bool
    panel_top_offset: int
    panel_bottom_offset: int
    use_event_filter: bool
    use_manual_tray_menu: bool


_MACOS = PlatformUIConfig(
    overlay_flags=Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus,
    app_window_flags=Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
    slider_handle_width=24,
    slider_handle_radius=12,
    slider_handle_margin=-8,
    slider_groove_height=6,
    slider_max_height=40,
    zone_label_font_size=9,
    mid_label_width=6,
    panel_y_from_top=True,
    panel_top_offset=30,
    panel_bottom_offset=50,
    use_event_filter=False,
    use_manual_tray_menu=True,
)

_WINDOWS = PlatformUIConfig(
    overlay_flags=Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool,
    app_window_flags=Qt.Tool | Qt.FramelessWindowHint,
    slider_handle_width=50,
    slider_handle_radius=25,
    slider_handle_margin=-20,
    slider_groove_height=10,
    slider_max_height=70,
    zone_label_font_size=14,
    mid_label_width=10,
    panel_y_from_top=False,
    panel_top_offset=30,
    panel_bottom_offset=50,
    use_event_filter=True,
    use_manual_tray_menu=False,
)

_LINUX = PlatformUIConfig(
    overlay_flags=Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.X11BypassWindowManagerHint,
    app_window_flags=Qt.Tool | Qt.FramelessWindowHint,
    slider_handle_width=50,
    slider_handle_radius=25,
    slider_handle_margin=-20,
    slider_groove_height=10,
    slider_max_height=70,
    zone_label_font_size=14,
    mid_label_width=10,
    panel_y_from_top=False,
    panel_top_offset=30,
    panel_bottom_offset=50,
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
