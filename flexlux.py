# Python 3.9+ required (3.10+ recommended)
# python3.11 -m venv venv
# .\venv\Scripts\Activate (Windows) or source venv/bin/activate (Linux/macOS)
# pip install PyQt5 pyinstaller Pillow screen_brightness_control
# macOS external monitor brightness (optional): brew install m1ddc
# Build commands:
# Windows: pyinstaller --onefile --windowed --icon=assets/icon.png --add-data="assets/icon.png;assets/" flexlux.py
# Linux/macOS: pyinstaller --onefile --windowed --icon=assets/icon.png --add-data="assets/icon.png:assets/" flexlux.py
# Or use the spec file: pyinstaller flexlux.spec

VERSION = "1.3.0"

import sys
import os
import platform
import logging
from logging.handlers import RotatingFileHandler
from PyQt5.QtWidgets import QApplication, QWidget, QSlider, QVBoxLayout, QHBoxLayout, QSystemTrayIcon, QMenu, QAction, QLabel, QMessageBox, QCheckBox
from PyQt5.QtGui import QIcon, QColor, QPainter, QCursor
from PyQt5.QtCore import Qt, QRect, QEvent, QTimer, QSettings
if platform.system() == "Darwin":
    import ctypes
    import subprocess
    import shutil
else:
    import screen_brightness_control as sbc


def _setup_logging():
    system = platform.system()
    if system == "Windows":
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "FlexLux")
    elif system == "Darwin":
        log_dir = os.path.expanduser("~/Library/Logs")
    else:
        log_dir = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "FlexLux")
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("FlexLux")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(os.path.join(log_dir, "FlexLux.log"), maxBytes=1_000_000, backupCount=1)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


log = _setup_logging()


if platform.system() == "Darwin":
    class _CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    class _CGSize(ctypes.Structure):
        _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

    class _CGRect(ctypes.Structure):
        _fields_ = [("origin", _CGPoint), ("size", _CGSize)]

    class MacBrightnessControl:
        """macOS brightness control via DisplayServices (built-in) and m1ddc (external)."""

        def __init__(self):
            self._cg = ctypes.cdll.LoadLibrary(
                '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
            self._cg.CGGetActiveDisplayList.argtypes = [
                ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32)]
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

            result = self._cg.CGGetActiveDisplayList(
                max_displays, display_ids, ctypes.byref(display_count))
            if result != 0:
                return

            external_idx = 1
            for i in range(display_count.value):
                display_id = display_ids[i]
                is_builtin = bool(self._cg.CGDisplayIsBuiltin(display_id))
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


class OverlayWindow(QWidget):
    def __init__(self, geometry=None):
        super().__init__()
        self.alpha = 0
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        
        # Platform-specific window flags
        if platform.system() == "Windows":
            self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        elif platform.system() == "Darwin":  # macOS — Qt.Tool omitted so overlays persist when the app deactivates
            self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        else:  # Linux
            self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.X11BypassWindowManagerHint)
        
        if geometry is None:
            desktop = QApplication.desktop()
            total_rect = QRect()
            for i in range(desktop.screenCount()):
                total_rect = total_rect.united(desktop.screenGeometry(i))
            geometry = total_rect
        
        self.setGeometry(geometry)
        self.show()

    def setTransparency(self, alpha):
        self.alpha = alpha
        self.repaint()

    def paintEvent(self, _):
        painter = QPainter(self)
        color = QColor(0, 0, 0, self.alpha)
        painter.fillRect(self.rect(), color)

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

class FlexLuxApp(QWidget):
    def __init__(self):
        super().__init__()
        self.min_brightness = 1 if platform.system() == "Darwin" else 0
        self.max_brightness = 100
        self.settings = QSettings("FlexLux", "FlexLux")
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._save_settings)
        self._syncing = False
        self._detect_monitors()
        self._create_overlays()
        self.initUI()
        self._restore_settings()
        self.hide_timer = QTimer()
        self.hide_timer.timeout.connect(self.check_focus_and_hide)
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._refresh_monitors)
        QApplication.desktop().screenCountChanged.connect(lambda _: self._refresh_timer.start())
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(self._POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_brightness)
        if any(self._hw_capable):
            self._poll_timer.start()

    def _detect_monitors(self):
        self._mac_brightness = None
        if platform.system() == "Darwin":
            try:
                self._mac_brightness = MacBrightnessControl()
                self.monitor_names = self._mac_brightness.list_monitors()
            except Exception as e:
                log.warning("macOS brightness init failed: %s", e)
                self._mac_brightness = None
                self.monitor_names = []
        else:
            try:
                self.monitor_names = sbc.list_monitors()
            except Exception:
                self.monitor_names = []
        self._has_hardware_monitors = len(self.monitor_names) > 0

        if platform.system() == "Darwin" and self._mac_brightness:
            self._hw_capable = [self._mac_brightness.has_hardware_control(n) for n in self.monitor_names]
        elif self._has_hardware_monitors:
            self._hw_capable = [True] * len(self.monitor_names)
        else:
            self._hw_capable = []

        if not self.monitor_names:
            self.monitor_names = ["Display"]
            self._hw_capable = [False]

    def _create_overlays(self):
        desktop = QApplication.desktop()
        screen_count = desktop.screenCount()
        if len(self.monitor_names) > 1 and screen_count > 1:
            self.overlays = []
            for mon_idx, name in enumerate(self.monitor_names):
                qt_screen = mon_idx
                if platform.system() == "Darwin" and self._mac_brightness:
                    bounds = self._mac_brightness.get_display_bounds(name)
                    if bounds:
                        for s in range(screen_count):
                            geo = desktop.screenGeometry(s)
                            if bounds[0] == geo.x() and bounds[1] == geo.y():
                                qt_screen = s
                                break
                qt_screen = min(qt_screen, screen_count - 1)
                self.overlays.append(OverlayWindow(desktop.screenGeometry(qt_screen)))
        else:
            self.overlays = [OverlayWindow()]

    def _restore_settings(self):
        for i, name in enumerate(self.monitor_names):
            key = f"brightness/{name}"
            saved = self.settings.value(key, None)
            if saved is not None:
                value = int(saved)
                upper = 200 if self._hw_capable[i] else 100
                value = max(0, min(upper, value))
                self.sliders[i].setValue(value)

    def _save_settings(self):
        for i, name in enumerate(self.monitor_names):
            self.settings.setValue(f"brightness/{name}", self.sliders[i].value())
        if self.link_checkbox:
            self.settings.setValue("link_monitors", self.link_checkbox.isChecked())
        self.settings.sync()

    def adjust_window_size(self):
        screen_width = QApplication.desktop().screenGeometry().width()
        new_width = int(0.15 * screen_width)
        single_height = int(new_width / 2.8)
        n = len(self.monitor_names)
        if n > 1:
            new_height = single_height * n + 16 * (n - 1)
        else:
            new_height = single_height
        self.resize(new_width, new_height)

    def _build_sliders(self, layout):
        if platform.system() == "Darwin":
            handle_w, handle_r, handle_m, groove_h, max_h = 24, 12, -8, 6, 40
        else:
            handle_w, handle_r, handle_m, groove_h, max_h = 50, 25, -20, 10, 70

        def _slider_style(hw_capable):
            if hw_capable:
                groove_bg = "background-color: darkgray;"
            else:
                groove_bg = ("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                             "stop:0 darkgray, stop:0.499 darkgray, "
                             "stop:0.5 #333333, stop:1 #333333);")
            return f"""
            .QSlider {{
                max-height: {max_h}px;
            }}
            QSlider::groove:horizontal {{
                height: {groove_h}px;
                {groove_bg}
            }}
            QSlider::handle:horizontal {{
                width: {handle_w}px;
                background-color: white;
                border-radius: {handle_r}px;
                margin: {handle_m}px 0;
            }}
            """

        zone_label_style = "color: #555555; font-size: 9px; margin: 0; padding: 0;"
        zone_label_disabled_style = "color: #222222; font-size: 9px; margin: 0; padding: 0;"
        mid_label_style = "color: #333333; font-size: 9px; margin: 0; padding: 0;"

        self.sliders = []
        multi = len(self.monitor_names) > 1

        for i, name in enumerate(self.monitor_names):
            hw = self._hw_capable[i]

            if multi:
                label = QLabel(name)
                label.setStyleSheet("color: #888888; font-size: 10px; margin: 0; padding: 0;")
                layout.addWidget(label)

            slider = QSlider(Qt.Horizontal, self)
            slider.setRange(0, 200)
            slider.setValue(100)
            slider.setStyleSheet(_slider_style(hw))
            slider.valueChanged[int].connect(lambda value, idx=i: self._snap_and_update(idx, value))
            layout.addWidget(slider)

            zone_row = QHBoxLayout()
            zone_row.setContentsMargins(0, 0, 0, 0)
            zone_row.setSpacing(0)
            lbl_artificial = QLabel("Artificial")
            lbl_artificial.setAlignment(Qt.AlignCenter)
            lbl_artificial.setStyleSheet(zone_label_style)
            lbl_mid = QLabel("|")
            lbl_mid.setFixedWidth(6)
            lbl_mid.setAlignment(Qt.AlignCenter)
            lbl_mid.setStyleSheet(mid_label_style)
            lbl_natural = QLabel("Natural")
            lbl_natural.setAlignment(Qt.AlignCenter)
            lbl_natural.setStyleSheet(zone_label_style if hw else zone_label_disabled_style)
            zone_row.addWidget(lbl_artificial, 1)
            zone_row.addWidget(lbl_mid, 0)
            zone_row.addWidget(lbl_natural, 1)
            layout.addLayout(zone_row)

            self.sliders.append(slider)

        self.link_checkbox = None
        if multi:
            self.link_checkbox = QCheckBox("Link Monitors")
            self.link_checkbox.setStyleSheet("color: #888888; font-size: 10px; margin-top: 4px;")
            self.link_checkbox.setChecked(self.settings.value("link_monitors", True, type=bool))
            layout.addWidget(self.link_checkbox)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _refresh_monitors(self):
        log.info("Screen configuration changed, refreshing monitors")
        self._save_settings()

        for overlay in self.overlays:
            overlay.close()

        self._detect_monitors()
        self._create_overlays()

        layout = self.layout()
        self._clear_layout(layout)
        self._build_sliders(layout)

        self.adjust_window_size()
        self._restore_settings()

        self._poll_timer.stop()
        if any(self._hw_capable):
            self._poll_timer.start()

    def initUI(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)

        self._build_sliders(layout)

        self.setStyleSheet("background-color: #111111;")
        self.setLayout(layout)

        self.adjust_window_size()

        # Platform-specific window flags
        if platform.system() == "Darwin":  # macOS
            self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        
        self.setWindowTitle('FlexLux')
        self.setWindowIcon(QIcon(resource_path('assets/icon.png')))

        # System tray setup
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(resource_path('assets/icon.png')))
        
        self.tray_menu = QMenu()
        self.autostart_action = QAction("Launch at Startup", self)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(self._is_autostart_enabled())
        self.autostart_action.triggered.connect(self._toggle_autostart)
        self.about_action = QAction("About", self)
        self.about_action.triggered.connect(self._show_about)
        self.quit_action = QAction("Quit", self)
        self.quit_action.triggered.connect(QApplication.instance().quit)
        self.tray_menu.addAction(self.autostart_action)
        self.tray_menu.addAction(self.about_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.quit_action)
        if platform.system() != "Darwin":
            self.tray_icon.setContextMenu(self.tray_menu)

        self.tray_icon.show()
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        # Platform-specific event handling
        if platform.system() != "Darwin":  # Not macOS
            QApplication.instance().installEventFilter(self)

    def _set_hardware_brightness(self, value, display):
        """Set hardware brightness (0-100) using the platform-appropriate backend."""
        if platform.system() == "Darwin" and self._mac_brightness:
            self._mac_brightness.set_brightness(value, display=display)
        else:
            sbc.set_brightness(value, display=display)

    def _get_hardware_brightness(self, display):
        """Read current hardware brightness (0-100), or None if unavailable."""
        try:
            if platform.system() == "Darwin" and self._mac_brightness:
                return self._mac_brightness.get_brightness(display=display)
            else:
                levels = sbc.get_brightness(display=display)
                if isinstance(levels, list):
                    return levels[0] if levels else None
                return levels
        except Exception as e:
            log.debug("Could not read brightness for %s: %s", display, e)
            return None

    def _expected_hw_from_slider(self, monitor_idx):
        """Compute expected hardware brightness from current slider position."""
        value = self.sliders[monitor_idx].value()
        if value <= 100:
            return self.min_brightness
        brightness_percent = (value - 100) / 100
        return int(self.min_brightness + (self.max_brightness - self.min_brightness) * brightness_percent)

    def _slider_from_hw(self, hw_brightness):
        """Convert hardware brightness (0-100) to slider value (100-200)."""
        if hw_brightness <= self.min_brightness:
            return 100
        percent = (hw_brightness - self.min_brightness) / (self.max_brightness - self.min_brightness)
        return 100 + int(round(percent * 100))

    def _set_poll_speed(self, fast=False):
        """Switch between fast (panel open) and slow (background) polling."""
        if not hasattr(self, '_poll_timer'):
            return
        interval = self._POLL_INTERVAL_FAST_MS if fast else self._POLL_INTERVAL_MS
        self._poll_timer.setInterval(interval)
        if any(self._hw_capable):
            self._poll_timer.start()

    def _poll_brightness(self):
        """Detect external brightness changes and update sliders accordingly."""
        for i, name in enumerate(self.monitor_names):
            if not self._hw_capable[i]:
                continue
            actual = self._get_hardware_brightness(name)
            if actual is None:
                continue
            expected = self._expected_hw_from_slider(i)
            if abs(actual - expected) <= self._POLL_THRESHOLD:
                continue

            new_slider = self._slider_from_hw(actual)
            log.info("External brightness change on %s: expected hw=%d, actual=%d, slider -> %d",
                     name, expected, actual, new_slider)

            if self.link_checkbox and self.link_checkbox.isChecked():
                self.link_checkbox.setChecked(False)

            self.sliders[i].blockSignals(True)
            self.sliders[i].setValue(new_slider)
            self.sliders[i].blockSignals(False)

            overlay = self.overlays[min(i, len(self.overlays) - 1)]
            overlay.setTransparency(0)

            self._save_timer.start()

    _SNAP_THRESHOLD = 5
    _POLL_INTERVAL_MS = 5000
    _POLL_INTERVAL_FAST_MS = 500
    _POLL_THRESHOLD = 3

    def _snap_and_update(self, monitor_idx, value):
        if not self._hw_capable[monitor_idx] and value > 100:
            self.sliders[monitor_idx].setValue(100)
            return
        if value != 100 and abs(value - 100) <= self._SNAP_THRESHOLD:
            self.sliders[monitor_idx].setValue(100)
            return
        self._on_slider_changed(monitor_idx, value)
        if self.link_checkbox and self.link_checkbox.isChecked() and not self._syncing:
            self._syncing = True
            for i, s in enumerate(self.sliders):
                if i != monitor_idx:
                    clamped = min(value, 200 if self._hw_capable[i] else 100)
                    s.setValue(clamped)
            self._syncing = False

    def _on_slider_changed(self, monitor_idx, value):
        self._save_timer.start()
        if hasattr(self, '_poll_timer'):
            self._poll_timer.start()
        monitor_name = self.monitor_names[monitor_idx]
        hw = self._hw_capable[monitor_idx]
        overlay = self.overlays[min(monitor_idx, len(self.overlays) - 1)]
        try:
            if value == 100:
                if hw:
                    self._set_hardware_brightness(self.min_brightness, monitor_name)
                overlay.setTransparency(0)
            elif value > 100:
                if hw:
                    brightness_percent = (value - 100) / 100
                    new_brightness = int(self.min_brightness + (self.max_brightness - self.min_brightness) * brightness_percent)
                    self._set_hardware_brightness(new_brightness, monitor_name)
                overlay.setTransparency(0)
            else:
                if hw:
                    self._set_hardware_brightness(self.min_brightness, monitor_name)
                darkness_percent = (100 - value) / 100
                max_darkness = 0.9
                overlay.setTransparency(int(darkness_percent * max_darkness * 255))
        except Exception as e:
            log.warning("Could not change brightness for %s: %s", monitor_name, e)

    def _get_autostart_executable(self):
        if getattr(sys, 'frozen', False):
            return sys.executable
        return f'"{sys.executable}" "{os.path.abspath(__file__)}"'

    def _is_autostart_enabled(self):
        system = platform.system()
        if system == "Windows":
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "FlexLux")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            except Exception:
                return False
        elif system == "Darwin":
            return os.path.exists(
                os.path.expanduser("~/Library/LaunchAgents/com.flexlux.app.plist"))
        else:
            return os.path.exists(
                os.path.expanduser("~/.config/autostart/flexlux.desktop"))

    def _set_autostart(self, enabled):
        system = platform.system()
        exe = self._get_autostart_executable()

        if system == "Windows":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE)
            try:
                if enabled:
                    winreg.SetValueEx(key, "FlexLux", 0, winreg.REG_SZ, exe)
                else:
                    try:
                        winreg.DeleteValue(key, "FlexLux")
                    except FileNotFoundError:
                        pass
            finally:
                winreg.CloseKey(key)

        elif system == "Darwin":
            plist_path = os.path.expanduser(
                "~/Library/LaunchAgents/com.flexlux.app.plist")
            if enabled:
                if getattr(sys, 'frozen', False):
                    args = f"        <string>{exe}</string>"
                else:
                    args = (f"        <string>{sys.executable}</string>\n"
                            f"        <string>{os.path.abspath(__file__)}</string>")
                plist = (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
                    ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0">\n<dict>\n'
                    '    <key>Label</key>\n'
                    '    <string>com.flexlux.app</string>\n'
                    '    <key>ProgramArguments</key>\n'
                    '    <array>\n'
                    f'{args}\n'
                    '    </array>\n'
                    '    <key>RunAtLoad</key>\n'
                    '    <true/>\n'
                    '</dict>\n</plist>\n')
                os.makedirs(os.path.dirname(plist_path), exist_ok=True)
                with open(plist_path, 'w') as f:
                    f.write(plist)
            else:
                if os.path.exists(plist_path):
                    os.remove(plist_path)

        else:  # Linux
            desktop_path = os.path.expanduser(
                "~/.config/autostart/flexlux.desktop")
            if enabled:
                entry = (
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=FlexLux\n"
                    f"Exec={exe}\n"
                    "Hidden=false\n"
                    "NoDisplay=false\n"
                    "X-GNOME-Autostart-enabled=true\n")
                os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
                with open(desktop_path, 'w') as f:
                    f.write(entry)
            else:
                if os.path.exists(desktop_path):
                    os.remove(desktop_path)

    def _toggle_autostart(self):
        enabled = self.autostart_action.isChecked()
        try:
            self._set_autostart(enabled)
        except Exception as e:
            log.warning("Could not %s autostart: %s", "enable" if enabled else "disable", e)
            self.autostart_action.setChecked(not enabled)

    def _show_about(self):
        QMessageBox.about(
            self, "About FlexLux",
            f"<h3>FlexLux v{VERSION}</h3>"
            "<p>Adjust monitor brightness beyond hardware limits.</p>"
            '<p><a href="https://github.com/joshbla/flexlux">github.com/joshbla/flexlux</a><br>'
            '<a href="https://github.com/joshbla/flexlux/issues">Report issues here</a></p>'
        )

    def toggle_window(self):
        if self.isVisible():
            self.hide()
            self._set_poll_speed(fast=False)
        else:
            self.show()
            self.activateWindow()
            self.raise_()
            self.updatePosition()
            self._set_poll_speed(fast=True)

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_window()
        elif reason == QSystemTrayIcon.Context and platform.system() == "Darwin":
            if self.tray_menu.isVisible():
                self.tray_menu.hide()
            else:
                self.tray_menu.popup(QCursor.pos())

    def updatePosition(self):
        cursor_pos = QCursor.pos()
        screen_geometry = QApplication.desktop().screenGeometry(cursor_pos)

        window_width = self.width()
        window_height = self.height()

        x = cursor_pos.x() - window_width // 2

        if platform.system() == "Darwin":
            y = screen_geometry.y() + 30
        else:
            y = screen_geometry.height() - window_height - 50

        x = max(screen_geometry.x(), min(x, screen_geometry.x() + screen_geometry.width() - window_width))
        y = max(screen_geometry.y(), min(y, screen_geometry.y() + screen_geometry.height() - window_height))

        self.move(x, y)

    def check_focus_and_hide(self):
        if not self.isActiveWindow() and not any(w.hasFocus() for w in self.findChildren(QWidget)):
            self.hide()
            self.hide_timer.stop()
            self._set_poll_speed(fast=False)

    def eventFilter(self, obj, event):
        if platform.system() == "Darwin":  # macOS needs different handling
            return super().eventFilter(obj, event)
        
        if event.type() == QEvent.WindowDeactivate:
            # Use timer to allow for child widget focus
            self.hide_timer.start(100)
        elif event.type() == QEvent.WindowActivate:
            self.hide_timer.stop()
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        if hasattr(self, '_poll_timer'):
            self._poll_timer.stop()
        if hasattr(self, '_mac_brightness') and self._mac_brightness:
            self._mac_brightness.cleanup()
        for overlay in self.overlays:
            overlay.close()
        event.accept()

if __name__ == '__main__':
    log.info("FlexLux v%s starting on %s", VERSION, platform.system())
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Keep running in system tray
    ex = FlexLuxApp()
    sys.exit(app.exec_())