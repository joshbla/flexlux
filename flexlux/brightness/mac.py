import ctypes
import subprocess
import shutil
import logging

log = logging.getLogger("FlexLux")


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


class MacBrightnessBackend:
    """macOS brightness control via DisplayServices (built-in) and m1ddc (external)."""

    min_brightness = 1

    def __init__(self):
        self._cg = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        self._cg.CGGetOnlineDisplayList.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32)]
        self._cg.CGDisplayMirrorsDisplay.argtypes = [ctypes.c_uint32]
        self._cg.CGDisplayMirrorsDisplay.restype = ctypes.c_uint32
        self._cg.CGDisplayIsBuiltin.argtypes = [ctypes.c_uint32]
        self._cg.CGDisplayIsBuiltin.restype = ctypes.c_int
        self._cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
        self._cg.CGDisplayBounds.restype = _CGRect
        self._cg.CGDisplayVendorNumber.argtypes = [ctypes.c_uint32]
        self._cg.CGDisplayVendorNumber.restype = ctypes.c_uint32
        self._cg.CGDisplayModelNumber.argtypes = [ctypes.c_uint32]
        self._cg.CGDisplayModelNumber.restype = ctypes.c_uint32
        self._cg.CGDisplaySerialNumber.argtypes = [ctypes.c_uint32]
        self._cg.CGDisplaySerialNumber.restype = ctypes.c_uint32

        self._ds = None
        try:
            self._ds = ctypes.cdll.LoadLibrary(
                '/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices')
            self._ds.DisplayServicesSetBrightness.argtypes = [ctypes.c_uint32, ctypes.c_float]
            self._ds.DisplayServicesSetBrightness.restype = ctypes.c_int
            self._ds.DisplayServicesGetBrightness.argtypes = [
                ctypes.c_uint32, ctypes.POINTER(ctypes.c_float)]
            self._ds.DisplayServicesGetBrightness.restype = ctypes.c_int
        except (OSError, AttributeError) as e:
            log.warning("DisplayServices not available: %s", e)

        self._m1ddc = shutil.which('m1ddc')
        self._m1ddc_info = {}
        if self._m1ddc:
            self._parse_m1ddc_display_list()
        self._displays = []
        self._detect_displays()

    def _parse_m1ddc_display_list(self):
        """Parse 'm1ddc display list detailed' to get names, UUIDs, and EDID identifiers."""
        try:
            result = subprocess.run(
                [self._m1ddc, 'display', 'list', 'detailed'],
                capture_output=True, timeout=3, text=True)
            if result.returncode != 0:
                return
            current_idx = None
            current = {}
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith('['):
                    if current_idx is not None:
                        self._m1ddc_info[current_idx] = current
                    bracket_end = line.index(']')
                    current_idx = int(line[1:bracket_end])
                    rest = line[bracket_end + 2:]
                    uuid_start = rest.rfind('(')
                    if uuid_start != -1:
                        product_name = rest[:uuid_start].strip()
                        uuid = rest[uuid_start + 1:rest.rfind(')')]
                    else:
                        product_name = rest.strip()
                        uuid = None
                    if not product_name or product_name == '(null)':
                        product_name = None
                    current = {'name': product_name, 'uuid': uuid}
                elif line.startswith('- Vendor:') and current_idx is not None:
                    parts = line.split('(')
                    if len(parts) >= 2:
                        current['vendor'] = int(parts[1].rstrip(')'), 16)
                elif line.startswith('- Model:') and current_idx is not None:
                    parts = line.split('(')
                    if len(parts) >= 2:
                        current['model'] = int(parts[1].rstrip(')'), 16)
                elif line.startswith('- Serial:') and current_idx is not None:
                    parts = line.split('(')
                    if len(parts) >= 2:
                        current['serial'] = int(parts[1].rstrip(')'), 16)
            if current_idx is not None:
                self._m1ddc_info[current_idx] = current
        except Exception as e:
            log.debug("Could not parse m1ddc display list: %s", e)

    def _find_m1ddc_match(self, cg_display_id):
        """Match a CG display to an m1ddc display by EDID vendor/model/serial."""
        cg_vendor = self._cg.CGDisplayVendorNumber(cg_display_id)
        cg_model = self._cg.CGDisplayModelNumber(cg_display_id)
        cg_serial = self._cg.CGDisplaySerialNumber(cg_display_id)
        for idx, info in self._m1ddc_info.items():
            if (info.get('vendor') == cg_vendor and
                    info.get('model') == cg_model and
                    info.get('serial') == cg_serial):
                return idx, info
        return None, {}

    def _detect_displays(self):
        max_displays = 16
        display_ids = (ctypes.c_uint32 * max_displays)()
        display_count = ctypes.c_uint32()

        result = self._cg.CGGetOnlineDisplayList(
            max_displays, display_ids, ctypes.byref(display_count))
        if result != 0:
            return

        external_idx = 1
        for i in range(display_count.value):
            display_id = display_ids[i]
            is_builtin = bool(self._cg.CGDisplayIsBuiltin(display_id))
            is_mirror = self._cg.CGDisplayMirrorsDisplay(display_id) != 0
            if is_builtin:
                name = "Built-in Display"
                method = 'displayservices' if self._ds else None
                uuid = None
            else:
                m1ddc_idx, info = self._find_m1ddc_match(display_id)
                name = info.get('name') or f"External Display {external_idx}"
                uuid = info.get('uuid')
                method = None
                if self._m1ddc and m1ddc_idx is not None:
                    try:
                        probe = subprocess.run(
                            [self._m1ddc, 'get', 'luminance', '-d', str(m1ddc_idx)],
                            capture_output=True, timeout=3, text=True)
                        if probe.returncode == 0 and probe.stdout.strip().isdigit():
                            method = 'm1ddc'
                            log.info("DDC/CI available for %s (m1ddc #%d)", name, m1ddc_idx)
                        else:
                            log.info("DDC/CI unavailable for %s (m1ddc #%d, rc=%d)", name, m1ddc_idx, probe.returncode)
                    except Exception as e:
                        log.info("DDC/CI probe failed for %s: %s", name, e)
                elif self._m1ddc:
                    log.info("No m1ddc match found for CG display %d", display_id)
                external_idx += 1
            if is_mirror:
                name += " (mirrored)"
            self._displays.append({
                'id': display_id,
                'name': name,
                'builtin': is_builtin,
                'method': method,
                'uuid': uuid,
                'm1ddc_idx': m1ddc_idx if not is_builtin else None,
            })

    def list_monitors(self):
        return [d['name'] for d in self._displays]

    def has_hardware_control(self, display_name):
        for d in self._displays:
            if d['name'] == display_name:
                return d['method'] is not None
        return False

    def get_display_bounds(self, display_name):
        """Return (x, y, w, h) in points for the named display, or None."""
        for d in self._displays:
            if d['name'] == display_name:
                r = self._cg.CGDisplayBounds(d['id'])
                return (int(r.origin.x), int(r.origin.y),
                        int(r.size.width), int(r.size.height))
        return None

    def set_brightness(self, value, display=None):
        """Set brightness. value: 0-100."""
        target = None
        if display:
            for d in self._displays:
                if d['name'] == display:
                    target = d
                    break
        if target is None and self._displays:
            target = self._displays[0]
        if target is None:
            return

        if target['method'] == 'displayservices':
            brightness = max(0.0, min(1.0, value / 100.0))
            kr = self._ds.DisplayServicesSetBrightness(
                target['id'], ctypes.c_float(brightness))
            if kr != 0:
                log.warning("DisplayServicesSetBrightness returned %d", kr)
        elif target['method'] == 'm1ddc' and target.get('m1ddc_idx') is not None:
            try:
                subprocess.run(
                    [self._m1ddc, 'set', 'luminance', str(int(value)),
                     '-d', str(target['m1ddc_idx'])],
                    capture_output=True, timeout=2)
            except Exception as e:
                log.warning("m1ddc brightness set failed: %s", e)

    def get_brightness(self, display=None):
        """Get current brightness (0-100), or None on failure."""
        target = None
        if display:
            for d in self._displays:
                if d['name'] == display:
                    target = d
                    break
        if target is None and self._displays:
            target = self._displays[0]
        if target is None:
            return None

        if target['method'] == 'displayservices':
            brightness = ctypes.c_float()
            kr = self._ds.DisplayServicesGetBrightness(
                target['id'], ctypes.byref(brightness))
            if kr == 0:
                return int(round(brightness.value * 100))
            return None
        elif target['method'] == 'm1ddc' and target.get('m1ddc_idx') is not None:
            try:
                result = subprocess.run(
                    [self._m1ddc, 'get', 'luminance', '-d', str(target['m1ddc_idx'])],
                    capture_output=True, timeout=3, text=True)
                if result.returncode == 0 and result.stdout.strip().isdigit():
                    return int(result.stdout.strip())
            except Exception as e:
                log.warning("m1ddc brightness get failed: %s", e)
        return None

    def cleanup(self):
        pass
