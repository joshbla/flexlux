import logging
import screen_brightness_control as sbc

log = logging.getLogger("FlexLux")


class SbcBrightnessBackend:
    """Windows/Linux brightness control via screen_brightness_control."""

    min_brightness = 0

    def list_monitors(self):
        return sbc.list_monitors()

    def has_hardware_control(self, display_name):
        return True

    def get_display_bounds(self, display_name):
        return None

    def set_brightness(self, value, display=None):
        sbc.set_brightness(value, display=display)

    def get_brightness(self, display=None):
        levels = sbc.get_brightness(display=display)
        if isinstance(levels, list):
            return levels[0] if levels else None
        return levels

    def cleanup(self):
        pass
