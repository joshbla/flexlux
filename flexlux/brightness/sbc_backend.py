import logging
from typing import Optional

import screen_brightness_control as sbc

log = logging.getLogger("FlexLux")

# Keys that sbc.Display.from_dict reads off the info dict. Some backends (e.g.
# the Linux DDCUtil method) omit a few of these when the monitor does not report
# them, so we normalise every info dict to guarantee all keys are present before
# constructing a Display. Values may be None; from_dict only requires the keys to
# exist (it routes brightness calls via the method class + index, not the name).
_INFO_KEYS = (
    'index', 'method', 'edid', 'manufacturer', 'manufacturer_id',
    'model', 'name', 'serial', 'uid', '_methods',
)


class SbcBrightnessBackend:
    """Windows/Linux brightness control via screen_brightness_control.

    The BrightnessBackend Protocol promises that list_monitors() returns a list
    of plain str monitor names, and that the same names are passed back into
    set_brightness / get_brightness / has_hardware_control / get_display_bounds.
    sbc.list_monitors() returns the ``name`` field of each detected display, but
    that field can be None on Linux (when EDID parsing yields no name) and is not
    guaranteed unique across identical monitor models. We therefore enumerate via
    sbc.list_monitors_info(allow_duplicates=True) so every physical display is
    surfaced, derive a stable non-empty str name per display (disambiguating
    duplicates with " 2", " 3", ...), and keep a name -> sbc.Display mapping so
    set/get address the exact physical display through its (method, index) pair,
    which is the safest identifier for duplicates.
    """

    min_brightness = 0

    def __init__(self):
        # name -> sbc.Display for the exact physical display. Populated by
        # list_monitors(); None means "not yet enumerated (or enumeration failed)".
        self._displays: dict = {}
        self._names: Optional[list] = None

    def list_monitors(self):
        if self._names is None:
            self._names, self._displays = self._enumerate()
        return self._names

    @staticmethod
    def _derive_name(info, position) -> str:
        """Build a stable, non-empty display name from an sbc info dict.

        No or-style fallbacks: each candidate is checked explicitly. The first
        usable str identifier wins, in order of human-readability.
        """
        name = info.get('name')
        if isinstance(name, str) and name:
            return name
        serial = info.get('serial')
        if isinstance(serial, str) and serial:
            return serial
        manufacturer = info.get('manufacturer')
        model = info.get('model')
        if isinstance(manufacturer, str) and isinstance(model, str) and manufacturer and model:
            return f"{manufacturer} {model}"
        edid = info.get('edid')
        if isinstance(edid, str) and edid:
            return edid
        return f"Display {position}"

    @staticmethod
    def _normalize_info(info) -> dict:
        return {k: info.get(k) for k in _INFO_KEYS}

    def _enumerate(self):
        # allow_duplicates=True so identical monitor models are not collapsed into
        # a single entry; we disambiguate their names ourselves below.
        info_list = sbc.list_monitors_info(allow_duplicates=True)

        names: list = []
        displays: dict = {}
        seen_count: dict = {}
        for i, info in enumerate(info_list):
            base = self._derive_name(info, i + 1)
            if base in seen_count:
                seen_count[base] += 1
                unique = f"{base} {seen_count[base]}"
            else:
                seen_count[base] = 1
                unique = base
            names.append(unique)
            displays[unique] = sbc.Display.from_dict(self._normalize_info(info))
        return names, displays

    def has_hardware_control(self, display_name) -> bool:
        # If list_monitors() has not run yet (or returned nothing) there is no
        # hardware control available for the caller's name; report that honestly
        # rather than triggering enumeration here, where an exception would not be
        # caught by the caller.
        return display_name in self._displays

    def get_display_bounds(self, display_name):
        # sbc exposes brightness methods only; it does not report a monitor's
        # position/geometry. Returning None lets the app fall back to its
        # positional monitor->screen mapping.
        return None

    def set_brightness(self, value, display=None) -> None:
        display_obj = self._displays.get(display) if isinstance(display, str) else None
        if display_obj is None:
            log.warning("set_brightness: no sbc display for %r", display)
            return
        display_obj.set_brightness(value=value)

    def get_brightness(self, display=None) -> Optional[int]:
        display_obj = self._displays.get(display) if isinstance(display, str) else None
        if display_obj is None:
            return None
        return display_obj.get_brightness()

    def cleanup(self) -> None:
        pass
