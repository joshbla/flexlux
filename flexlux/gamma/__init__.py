"""Per-display gamma control: color warmth for every display, plus dimming
for displays that an overlay window cannot target on its own.

Each platform controller reads and writes one output's gamma ramps; the
shared GammaController combines dimming and warmth into a single write so the
two never overwrite each other.
"""

import logging
import math
import os
import platform
from typing import Optional

log = logging.getLogger("FlexLux")

_NEUTRAL_KELVIN = 6500
_WARMEST_KELVIN = 1200
_NEUTRAL_RGB = (1.0, 1.0, 1.0)


def _kelvin_to_rgb(kelvin):
    """Approximate blackbody color (Tanner Helland's fit), channels 0-255."""
    t = kelvin / 100
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592
        g = 288.1221695283 * (t - 60) ** -0.0755148492
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    return tuple(max(0.0, min(255.0, c)) for c in (r, g, b))


def warmth_rgb(strength):
    """Channel multipliers for a warmth strength (0.0 = neutral, 1.0 = warmest).

    Strength is linear in mireds, which tracks perceived warmth more evenly
    than kelvin. The result is normalized so 6500K maps to exactly neutral.
    """
    strength = max(0.0, min(1.0, strength))
    if strength == 0.0:
        return _NEUTRAL_RGB
    neutral_mired = 1e6 / _NEUTRAL_KELVIN
    warmest_mired = 1e6 / _WARMEST_KELVIN
    kelvin = 1e6 / (neutral_mired + (warmest_mired - neutral_mired) * strength)
    neutral = _kelvin_to_rgb(_NEUTRAL_KELVIN)
    warm = _kelvin_to_rgb(kelvin)
    return tuple(min(1.0, w / n) for w, n in zip(warm, neutral))


def warmth_kelvin(strength):
    """Approximate color temperature for a warmth strength, for display."""
    strength = max(0.0, min(1.0, strength))
    neutral_mired = 1e6 / _NEUTRAL_KELVIN
    warmest_mired = 1e6 / _WARMEST_KELVIN
    return int(round(1e6 / (neutral_mired + (warmest_mired - neutral_mired) * strength), -2))


class GammaController:
    """Shared ramp math. Subclasses implement _list_outputs, _read and _write.

    Ramps are three lists (r, g, b) of floats in 0.0-1.0. Each output's
    original ramp is captured once; every write is that ramp scaled by the
    output's dimming level and the global warmth multipliers.
    """

    _SEARCH_STEPS = 7

    def __init__(self):
        self._base = {}
        self._dim = {}
        self._warmth = _NEUTRAL_RGB
        self._written = {}
        for key in self._list_outputs():
            ramps = self._read(key)
            if ramps is None:
                log.warning("Could not read gamma for display %s; it will not be adjusted", key)
                continue
            self._base[key] = self._neutralize_if_tinted(key, ramps)
            self._dim[key] = 1.0

    def _list_outputs(self):
        raise NotImplementedError

    def _read(self, key):
        raise NotImplementedError

    def _write(self, key, ramps):
        raise NotImplementedError

    @staticmethod
    def _neutralize_if_tinted(key, ramps):
        """Replace a ramp that is already tinted or dimmed with a neutral one.

        Another color app, or a previous FlexLux run that exited without
        restoring (Windows and X11 keep ramps after the process ends), would
        otherwise become the baseline and compound with every adjustment.
        """
        tops = [channel[-1] for channel in ramps]
        if max(tops) - min(tops) <= 0.02 and min(tops) >= 0.9:
            return ramps
        log.warning("Gamma for display %s was already adjusted (tops %s); using a neutral ramp",
                    key, [round(t, 3) for t in tops])
        size = len(ramps[0])
        neutral = [i / (size - 1) for i in range(size)]
        return neutral, list(neutral), list(neutral)

    def keys(self):
        return list(self._base)

    def set_warmth(self, rgb):
        """Apply warmth multipliers to every display.

        Returns False when the OS refused the full adjustment on any display
        and a weaker one was applied instead (Windows' default gamma limit).
        """
        self._warmth = tuple(rgb)
        results = [self._apply(key) for key in self._base]
        return all(results)

    def set_dimming(self, key, level):
        """Scale one display's output by level (0.0 = black, 1.0 = unchanged)."""
        if key not in self._base:
            log.warning("Gamma dimming unavailable for display %s", key)
            return
        self._dim[key] = max(0.0, min(1.0, level))
        self._apply(key)

    def _scaled(self, key, scale):
        return [[v * s for v in channel] for channel, s in zip(self._base[key], scale)]

    def _apply(self, key):
        scale = tuple(self._dim[key] * w for w in self._warmth)
        if self._written.get(key) == scale:
            return True
        if scale == _NEUTRAL_RGB and key not in self._written:
            return True
        if self._write(key, self._scaled(key, scale)):
            self._written[key] = scale
            return True
        # The OS rejected the ramp as too far from neutral: apply the
        # strongest partial adjustment it accepts.
        accepted = 0.0
        accepted_scale = _NEUTRAL_RGB
        low, high = 0.0, 1.0
        for _ in range(self._SEARCH_STEPS):
            mid = (low + high) / 2
            partial = tuple(1.0 - mid * (1.0 - s) for s in scale)
            if self._write(key, self._scaled(key, partial)):
                low, accepted, accepted_scale = mid, mid, partial
            else:
                high = mid
        if accepted == 0.0:
            self._write(key, self._base[key])
        self._written[key] = accepted_scale
        log.warning("Gamma for display %s limited by the OS to %d%% of the requested adjustment",
                    key, int(accepted * 100))
        return False

    def reapply(self):
        """Rewrite ramps that the OS or another app has reset."""
        for key, scale in list(self._written.items()):
            if scale == _NEUTRAL_RGB:
                continue
            current = self._read(key)
            if current is None:
                continue
            expected = [channel[-1] * s for channel, s in zip(self._base[key], scale)]
            if any(abs(c[-1] - e) > 0.01 for c, e in zip(current, expected)):
                log.info("Gamma for display %s was reset; reapplying", key)
                self._write(key, self._scaled(key, scale))

    def restore(self):
        """Write back every original ramp this controller changed."""
        for key in list(self._written):
            self._write(key, self._base[key])
        self._written.clear()

    def can_unlock_full_range(self):
        """True when the OS limits gamma strength and FlexLux can lift the limit."""
        return False

    def unlock_full_range(self):
        raise NotImplementedError


def get_gamma_controller() -> Optional[GammaController]:
    """Return this platform's gamma controller, or None where gamma is unsupported."""
    system = platform.system()
    try:
        if system == "Darwin":
            from flexlux.gamma.mac import MacGammaController
            return MacGammaController()
        if system == "Windows":
            from flexlux.gamma.windows import WindowsGammaController
            return WindowsGammaController()
        if os.environ.get("XDG_SESSION_TYPE") == "wayland" or "WAYLAND_DISPLAY" in os.environ:
            # XWayland accepts X11 gamma calls but they never reach the screen.
            log.info("Gamma control is unavailable under Wayland")
            return None
        from flexlux.gamma.x11 import X11GammaController
        return X11GammaController()
    except Exception as e:
        log.warning("Gamma control unavailable: %s", e)
        return None
