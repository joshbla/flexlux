import logging
import threading
from collections import Counter
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


class _BrightnessWorker:
    """Runs sbc brightness reads and writes on one background thread.

    On Windows every VCP call re-enumerates the monitors and retries up to 50
    times, which takes seconds when DDC/CI is blocked (KVM switches, hubs).
    Calling sbc on the UI thread therefore froze the panel during slider drags
    and polls. Writes keep only the LATEST pending value per display; reads
    refresh a cache that get_brightness() serves without blocking. A single
    thread serializes all access to each display's DDC/CI bus.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._writes = {}       # name -> (sbc.Display, value)
        self._reads = {}        # name -> sbc.Display
        self._readings = {}     # name -> last brightness read after the latest write
        self._write_gen = Counter()
        self._stop_requested = False
        self._thread = threading.Thread(
            target=self._run, name="flexlux-sbc-worker", daemon=True)
        self._thread.start()

    def submit_write(self, name, display, value):
        with self._cond:
            self._writes[name] = (display, value)
            self._write_gen[name] += 1
            # Any cached reading predates this write and would make the
            # poller think the brightness was changed externally.
            self._readings.pop(name, None)
            self._cond.notify()

    def latest_reading(self, name, display):
        """Return the most recent reading (or None) and queue a fresh read."""
        with self._cond:
            self._reads[name] = display
            self._cond.notify()
            return self._readings.get(name)

    def stop(self):
        """Flush remaining writes, drop pending reads, then stop the thread."""
        with self._cond:
            self._stop_requested = True
            self._reads.clear()
            self._cond.notify()
        self._thread.join(timeout=5)

    def _run(self):
        while True:
            with self._cond:
                while not self._writes and not self._reads and not self._stop_requested:
                    self._cond.wait()
                if self._writes:
                    name, (display, value) = self._writes.popitem()
                    is_write = True
                elif self._stop_requested:
                    return
                else:
                    name, display = self._reads.popitem()
                    gen = self._write_gen[name]
                    is_write = False
            if is_write:
                try:
                    display.set_brightness(value=value)
                except Exception as e:
                    log.warning("sbc brightness set failed for %s: %s", name, e)
                continue
            try:
                value = display.get_brightness()
            except Exception as e:
                log.debug("sbc brightness get failed for %s: %s", name, e)
                continue
            with self._cond:
                # Discard a reading that raced a newer write to the same display.
                if self._write_gen[name] == gen and name not in self._writes:
                    self._readings[name] = value


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
        self._worker = _BrightnessWorker()

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

    def gamma_dimming_key(self, display_name):
        # Artificial dimming is always done with overlays on Windows/Linux.
        return None

    def set_brightness(self, value, display=None) -> None:
        """Queue a brightness write. Writes are async and coalesced."""
        display_obj = self._displays.get(display) if isinstance(display, str) else None
        if display_obj is None:
            log.warning("set_brightness: no sbc display for %r", display)
            return
        self._worker.submit_write(display, display_obj, value)

    def get_brightness(self, display=None) -> Optional[int]:
        """Return the latest background reading, or None if none is ready yet."""
        display_obj = self._displays.get(display) if isinstance(display, str) else None
        if display_obj is None:
            return None
        return self._worker.latest_reading(display, display_obj)

    def cleanup(self) -> None:
        self._worker.stop()
