import logging
import time
from PyQt5.QtWidgets import (QApplication, QWidget, QSlider, QVBoxLayout, QHBoxLayout, QSystemTrayIcon, QMenu,
                             QAction, QLabel, QMessageBox, QCheckBox, QToolTip, QStackedWidget, QPushButton,
                             QTimeEdit, QAbstractSpinBox)
from PyQt5.QtGui import QIcon, QCursor
from PyQt5.QtCore import Qt, QEvent, QTimer, QSettings, QTime, QLocale

from flexlux import VERSION
from flexlux.brightness import get_backend, get_key_interceptor
from flexlux.gamma import get_gamma_controller, warmth_rgb, warmth_kelvin
from flexlux.overlay import OverlayWindow
from flexlux.platform_ui import get_ui_config
from flexlux.utils import resource_path
from flexlux import autostart

log = logging.getLogger("FlexLux")

_SLIDER_HEIGHT = 40
_SLIDER_HANDLE_STYLE = """
    QSlider::handle:horizontal {
        width: 24px;
        background-color: white;
        border-radius: 12px;
        margin: -8px 0;
    }
"""


def _slider_style(groove_background):
    return f"""
    .QSlider {{
        max-height: {_SLIDER_HEIGHT}px;
    }}
    QSlider::groove:horizontal {{
        height: 6px;
        {groove_background}
    }}
    {_SLIDER_HANDLE_STYLE}
    """


_HW_GROOVE = "background-color: darkgray;"
_OVERLAY_ONLY_GROOVE = ("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                        "stop:0 darkgray, stop:0.499 darkgray, stop:0.5 #333333, stop:1 #333333);")
_WARMTH_GROOVE = "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 darkgray, stop:1 #d9822b);"
_CHECKBOX_STYLE = "QCheckBox { color: #888888; font-size: 10px; }"
_NAV_BUTTON_STYLE = """
    QPushButton {
        color: #888888; background: transparent; border: none;
        font-size: 12px; padding: 2px 4px;
    }
    QPushButton:hover { color: #dddddd; }
"""
_TIME_EDIT_STYLE = """
    QTimeEdit {
        color: #cccccc; background-color: #1c1c1c;
        border: 1px solid #333333; border-radius: 3px;
        padding: 2px 4px; font-size: 11px;
    }
    QTimeEdit:disabled { color: #444444; border-color: #222222; }
"""
_SMALL_LABEL_STYLE = "color: #666666; font-size: 10px;"


class FlexLuxApp(QWidget):
    def __init__(self):
        super().__init__()
        self._ui = get_ui_config()
        self.max_brightness = 100
        self.settings = QSettings("FlexLux", "FlexLux")
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._save_settings)
        self._syncing = False
        self._key_interceptor = None
        self._accessibility_notified = False
        self._last_hide_time = 0.0
        self._gamma_unlock_offered = False
        self._warmth_mix = 0.0
        self._fade_from = 0.0
        self._fade_to = 0.0
        self._fade_started = 0.0
        self._fade_timer = QTimer()
        self._fade_timer.setInterval(self._WARMTH_FADE_STEP_MS)
        self._fade_timer.timeout.connect(self._fade_tick)
        # Whether the clock was inside the schedule window at the last check;
        # None forces the next check to apply the schedule.
        self._schedule_was_in_window = None
        self._gamma = get_gamma_controller()
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
        self._gamma_timer = QTimer()
        self._gamma_timer.setInterval(self._GAMMA_REAPPLY_INTERVAL_MS)
        self._gamma_timer.timeout.connect(self._reapply_gamma)
        if self._gamma is not None:
            self._gamma_timer.start()
        self._schedule_timer = QTimer()
        self._schedule_timer.setInterval(self._SCHEDULE_CHECK_INTERVAL_MS)
        self._schedule_timer.timeout.connect(lambda: self._check_schedule(fade=True))
        self._schedule_timer.start()

        interceptor = get_key_interceptor()
        if interceptor is not None:
            interceptor.brightness_key_pressed.connect(self._on_brightness_key)
            if interceptor.start():
                self._key_interceptor = interceptor
                self._update_key_intercept_state()
            elif interceptor.failure_reason == "permission":
                self._notify_accessibility_permission()

        QApplication.instance().aboutToQuit.connect(self._shutdown)

    def _detect_monitors(self):
        self._backend = None
        try:
            self._backend = get_backend()
            self.monitor_names = self._backend.list_monitors()
        except Exception as e:
            log.warning("Brightness backend init failed: %s", e)
            if self._backend:
                self._backend.cleanup()
            self._backend = None
            self.monitor_names = []

        self.min_brightness = self._backend.min_brightness if self._backend else 0

        if self._backend and self.monitor_names:
            self._hw_capable = [self._backend.has_hardware_control(n) for n in self.monitor_names]
            self._gamma_keys = [self._gamma_key_for(n) for n in self.monitor_names]
        else:
            self._hw_capable = []
            self._gamma_keys = []

        if not self.monitor_names:
            self.monitor_names = ["Display"]
            self._hw_capable = [False]
            self._gamma_keys = [None]

    def _gamma_key_for(self, name):
        """Gamma controller key for a monitor dimmed through gamma, else None."""
        key = self._backend.gamma_dimming_key(name)
        if key is None:
            return None
        if self._gamma is None or key not in self._gamma.keys():
            log.warning("%s needs gamma dimming but gamma control is unavailable; "
                        "it will share the overlay", name)
            return None
        return key

    def _create_overlays(self):
        desktop = QApplication.desktop()
        screen_count = desktop.screenCount()
        self._overlay_for_monitor = []

        if len(self.monitor_names) > 1 and screen_count > 1:
            screen_to_overlay = {}
            self.overlays = []
            for mon_idx, name in enumerate(self.monitor_names):
                if self._gamma_keys[mon_idx] is not None:
                    self._overlay_for_monitor.append(None)
                    continue
                qt_screen = mon_idx
                if self._backend:
                    bounds = self._backend.get_display_bounds(name)
                    if bounds:
                        for s in range(screen_count):
                            geo = desktop.screenGeometry(s)
                            if bounds[0] == geo.x() and bounds[1] == geo.y():
                                qt_screen = s
                                break
                qt_screen = min(qt_screen, screen_count - 1)
                if qt_screen not in screen_to_overlay:
                    screen_to_overlay[qt_screen] = len(self.overlays)
                    self.overlays.append(OverlayWindow(desktop.screenGeometry(qt_screen)))
                self._overlay_for_monitor.append(screen_to_overlay[qt_screen])
        else:
            self.overlays = [OverlayWindow()]
            self._overlay_for_monitor = [0 if k is None else None for k in self._gamma_keys]

    def _restore_settings(self):
        for i, name in enumerate(self.monitor_names):
            key = f"brightness/{name}"
            saved = self.settings.value(key, None)
            if saved is None:
                continue
            try:
                value = int(saved)
            except (ValueError, TypeError):
                log.warning("Ignoring corrupt saved brightness value for %s: %r",
                            name, saved)
                continue
            upper = 200 if self._hw_capable[i] else 100
            value = max(0, min(upper, value))
            self.sliders[i].setValue(value)
        if self.warmth_slider is not None:
            self._set_warmth_controls(
                self.settings.value("warmth/enabled", False, type=bool),
                self.settings.value("warmth/level", self._WARMTH_DEFAULT_LEVEL, type=int))
            self._set_schedule_controls(
                self.settings.value("warmth/schedule_enabled", False, type=bool),
                self.settings.value("warmth/schedule_start", self._SCHEDULE_DEFAULT_START, type=int),
                self.settings.value("warmth/schedule_end", self._SCHEDULE_DEFAULT_END, type=int))
            self._check_schedule(fade=False)

    def _save_settings(self):
        for i, name in enumerate(self.monitor_names):
            self.settings.setValue(f"brightness/{name}", self.sliders[i].value())
        if self.link_checkbox:
            self.settings.setValue("link_monitors", self.link_checkbox.isChecked())
        if self.warmth_slider is not None:
            self.settings.setValue("warmth/enabled", self.warmth_checkbox.isChecked())
            self.settings.setValue("warmth/level", self.warmth_slider.value())
            self.settings.setValue("warmth/schedule_enabled", self.schedule_checkbox.isChecked())
            self.settings.setValue("warmth/schedule_start", self._minutes(self.schedule_start_edit.time()))
            self.settings.setValue("warmth/schedule_end", self._minutes(self.schedule_end_edit.time()))
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
        if self.warmth_slider is not None:
            new_height += _SLIDER_HEIGHT
        self.resize(new_width, new_height)

    def _build_sliders(self, layout):
        # Page 0 holds the everyday controls; page 1 holds warmth settings.
        self._pages = QStackedWidget(self)
        main_page = QWidget(self._pages)
        self._pages.addWidget(main_page)
        layout.addWidget(self._pages)
        layout = QVBoxLayout(main_page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)

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
            slider.setAccessibleName(f"Brightness for {name}")
            slider.setStyleSheet(_slider_style(_HW_GROOVE if hw else _OVERLAY_ONLY_GROOVE))
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

        self.warmth_checkbox = None
        self.warmth_slider = None
        self.warmth_more_button = None
        self.schedule_checkbox = None
        self.schedule_start_edit = None
        self.schedule_end_edit = None
        if self._gamma is not None:
            row = QHBoxLayout()
            row.setContentsMargins(0, 4, 0, 0)
            row.setSpacing(6)
            self.warmth_checkbox = QCheckBox("Warmth")
            self.warmth_checkbox.setStyleSheet(_CHECKBOX_STYLE)
            self.warmth_slider = QSlider(Qt.Horizontal, self)
            self.warmth_slider.setAccessibleName("Screen warmth")
            self.warmth_slider.setStyleSheet(_slider_style(_WARMTH_GROOVE))
            self.warmth_more_button = QPushButton()
            self.warmth_more_button.setStyleSheet(_NAV_BUTTON_STYLE)
            self.warmth_more_button.setCursor(Qt.PointingHandCursor)
            self.warmth_more_button.setAccessibleName("Warmth schedule")
            self.warmth_more_button.clicked.connect(lambda: self._pages.setCurrentIndex(1))
            row.addWidget(self.warmth_checkbox)
            row.addWidget(self.warmth_slider, 1)
            row.addWidget(self.warmth_more_button)
            layout.addLayout(row)
            self._pages.addWidget(self._build_warmth_page())
            self._wire_warmth_controls()

    def _build_warmth_page(self):
        page = QWidget(self._pages)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(2)
        back_button = QPushButton("‹")
        back_button.setStyleSheet(_NAV_BUTTON_STYLE)
        back_button.setCursor(Qt.PointingHandCursor)
        back_button.setAccessibleName("Back")
        back_button.clicked.connect(lambda: self._pages.setCurrentIndex(0))
        title = QLabel("Warmth")
        title.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        header.addWidget(back_button)
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        self.schedule_checkbox = QCheckBox("Schedule")
        self.schedule_checkbox.setStyleSheet(_CHECKBOX_STYLE)
        layout.addWidget(self.schedule_checkbox)

        times = QHBoxLayout()
        times.setSpacing(6)
        self.schedule_start_edit = self._make_time_edit("Warmth starts at")
        self.schedule_end_edit = self._make_time_edit("Warmth ends at")
        from_label = QLabel("From")
        from_label.setStyleSheet(_SMALL_LABEL_STYLE)
        to_label = QLabel("to")
        to_label.setStyleSheet(_SMALL_LABEL_STYLE)
        times.addWidget(from_label)
        times.addWidget(self.schedule_start_edit, 1)
        times.addWidget(to_label)
        times.addWidget(self.schedule_end_edit, 1)
        layout.addLayout(times)
        layout.addStretch(1)
        return page

    @staticmethod
    def _make_time_edit(accessible_name):
        edit = QTimeEdit()
        edit.setDisplayFormat(QLocale.system().timeFormat(QLocale.ShortFormat))
        edit.setButtonSymbols(QAbstractSpinBox.NoButtons)
        edit.setAlignment(Qt.AlignCenter)
        edit.setStyleSheet(_TIME_EDIT_STYLE)
        edit.setAccessibleName(accessible_name)
        return edit

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

        # Flush and stop the old backend's writer thread so its queued writes
        # cannot land after the new backend's.
        if self._backend:
            self._backend.cleanup()
        # The display set changed: put back the old displays' ramps and
        # capture fresh ones for the new set.
        if self._gamma is not None:
            self._gamma.restore()
        self._gamma = get_gamma_controller()
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
        self._gamma_timer.stop()
        if self._gamma is not None:
            self._gamma_timer.start()

        # Reconcile key interception against the new monitor set. We do not
        # construct the interceptor here if it was never created at startup —
        # only refresh its intercept state if it already exists.
        if self._key_interceptor is not None:
            self._update_key_intercept_state()

    def initUI(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._build_sliders(layout)

        self.setStyleSheet("background-color: #111111;")
        self.setLayout(layout)

        self.adjust_window_size()

        self.setWindowFlags(self._ui.app_window_flags)
        
        self.setWindowTitle('FlexLux')
        self.setWindowIcon(QIcon(resource_path('assets/icon.png')))

        # System tray setup
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(resource_path('assets/icon.png')))
        
        self.tray_menu = QMenu()
        self.autostart_action = QAction("Launch at Startup", self)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(autostart.is_enabled())
        self.autostart_action.triggered.connect(self._toggle_autostart)
        self.about_action = QAction("About", self)
        self.about_action.triggered.connect(self._show_about)
        self.reset_action = QAction("Reset Settings", self)
        self.reset_action.triggered.connect(self._reset_settings)
        self.gamma_unlock_action = QAction("Allow Stronger Warmth...", self)
        self.gamma_unlock_action.triggered.connect(self._unlock_gamma_range)
        self.gamma_unlock_action.setVisible(False)
        self.quit_action = QAction("Quit", self)
        self.quit_action.triggered.connect(QApplication.instance().quit)
        self.tray_menu.addAction(self.autostart_action)
        self.tray_menu.addAction(self.about_action)
        self.tray_menu.addAction(self.reset_action)
        self.tray_menu.addAction(self.gamma_unlock_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.quit_action)
        if not self._ui.use_manual_tray_menu:
            self.tray_icon.setContextMenu(self.tray_menu)

        self.tray_icon.show()
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        if self._ui.use_event_filter:
            QApplication.instance().installEventFilter(self)

    def _set_hardware_brightness(self, value, display):
        """Set hardware brightness (0-100) using the platform-appropriate backend."""
        if self._backend:
            self._backend.set_brightness(value, display=display)

    def _get_hardware_brightness(self, display):
        """Read current hardware brightness (0-100), or None if unavailable."""
        if not self._backend:
            return None
        try:
            return self._backend.get_brightness(display=display)
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

            self._apply_artificial_dimming(i)

            self._save_timer.start()

        self._update_key_intercept_state()

    def _notify_accessibility_permission(self):
        """One-time non-blocking tray notification prompting for Accessibility.

        Shown at most once per app run, only on macOS, only when the
        interceptor failed specifically due to a missing Accessibility grant.
        """
        if self._accessibility_notified:
            return
        self._accessibility_notified = True
        self.tray_icon.showMessage(
            "FlexLux: Accessibility permission needed",
            "Grant FlexLux Accessibility under System Settings > Privacy & "
            "Security > Accessibility to enable brightness-key support.",
            QSystemTrayIcon.Warning, 5000)

    def _update_key_intercept_state(self):
        """Update which brightness key directions the interceptor should suppress.

        Keys are intercepted if ANY hardware-capable monitor's state requires
        it: down is intercepted whenever any such slider is at or below the
        overlay/hardware boundary (software handles dimming), and up is
        intercepted whenever any such slider is in the overlay zone (so the OS
        does not fight the overlay by raising hardware brightness).
        """
        if not self._key_interceptor:
            return
        intercept_down = False
        intercept_up = False
        for i, name in enumerate(self.monitor_names):
            if not self._hw_capable[i]:
                continue
            value = self.sliders[i].value()
            if value < 100:
                intercept_up = True
            if value <= 100:
                intercept_down = True
        self._key_interceptor.set_state(intercept_down, intercept_up)

    def _on_brightness_key(self, direction, intercepted):
        """Handle a brightness key press detected by the event tap."""
        if intercepted:
            linked = self.link_checkbox is not None and self.link_checkbox.isChecked()
            for i, name in enumerate(self.monitor_names):
                if not self._hw_capable[i]:
                    continue
                value = self.sliders[i].value()
                if direction == "down":
                    new_value = max(self._KEY_MIN_VALUE, value - self._KEY_STEP)
                else:
                    new_value = min(100, value + self._KEY_STEP)
                # When monitors are linked, _snap_and_update propagates the
                # value to every other slider, so driving one is enough and
                # avoids the propagation loop double-applying the step.
                self.sliders[i].setValue(new_value)
                if linked:
                    return
        else:
            # The OS changed hardware brightness itself. Wait briefly for the
            # new value to settle on the hardware before polling it back so we
            # read the post-key state rather than a mid-transition one.
            QTimer.singleShot(200, self._poll_brightness)

    _SNAP_THRESHOLD = 5
    _POLL_INTERVAL_MS = 5000
    _POLL_INTERVAL_FAST_MS = 500
    _POLL_THRESHOLD = 3
    _KEY_STEP = 6
    _KEY_MIN_VALUE = 0
    _GAMMA_REAPPLY_INTERVAL_MS = 2000
    _WARMTH_DEFAULT_LEVEL = 40
    _WARMTH_FADE_SECONDS = 180
    _WARMTH_FADE_STEP_MS = 500
    _SCHEDULE_CHECK_INTERVAL_MS = 30000
    _SCHEDULE_DEFAULT_START = 21 * 60
    _SCHEDULE_DEFAULT_END = 7 * 60

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

    @staticmethod
    def _overlay_alpha_for_value(value):
        """Compute overlay alpha (0-229) for a slider value."""
        if value >= 100:
            return 0
        darkness_percent = (100 - value) / 100
        max_darkness = 0.9
        return int(darkness_percent * max_darkness * 255)

    def _recalc_overlay(self, overlay_idx):
        """Set an overlay's transparency to the darkest value across all monitors sharing it."""
        max_alpha = 0
        for i, ov_idx in enumerate(self._overlay_for_monitor):
            if ov_idx == overlay_idx:
                max_alpha = max(max_alpha, self._overlay_alpha_for_value(self.sliders[i].value()))
        self.overlays[overlay_idx].setTransparency(max_alpha)

    @staticmethod
    def _gamma_level_for_value(value):
        """Compute gamma output level (0.0-1.0) for a slider value.

        Unlike the overlay, gamma reaches full black at 0: a gamma-dimmed
        display is mirrored, so the panel stays visible on the other display.
        """
        if value >= 100:
            return 1.0
        return value / 100

    def _apply_artificial_dimming(self, monitor_idx):
        key = self._gamma_keys[monitor_idx]
        if key is not None:
            self._gamma.set_dimming(key, self._gamma_level_for_value(self.sliders[monitor_idx].value()))
        else:
            self._recalc_overlay(self._overlay_for_monitor[monitor_idx])

    def _reapply_gamma(self):
        self._gamma.reapply()

    def _wire_warmth_controls(self):
        self.warmth_slider.setRange(0, 100)
        self.warmth_slider.setValue(self._WARMTH_DEFAULT_LEVEL)
        self._update_warmth_tooltip()
        self.warmth_checkbox.toggled.connect(self._on_warmth_toggled)
        self.warmth_slider.valueChanged[int].connect(self._on_warmth_level_changed)
        self.schedule_start_edit.setTime(self._time_from_minutes(self._SCHEDULE_DEFAULT_START))
        self.schedule_end_edit.setTime(self._time_from_minutes(self._SCHEDULE_DEFAULT_END))
        self._update_schedule_controls()
        self.schedule_checkbox.toggled.connect(self._on_schedule_settings_changed)
        self.schedule_start_edit.timeChanged.connect(self._on_schedule_settings_changed)
        self.schedule_end_edit.timeChanged.connect(self._on_schedule_settings_changed)

    def _set_warmth_controls(self, enabled, level):
        """Set warmth widgets without their change handlers, then apply once."""
        self.warmth_checkbox.blockSignals(True)
        self.warmth_slider.blockSignals(True)
        self.warmth_checkbox.setChecked(enabled)
        self.warmth_slider.setValue(max(0, min(100, level)))
        self.warmth_checkbox.blockSignals(False)
        self.warmth_slider.blockSignals(False)
        self._update_warmth_tooltip()
        self._fade_timer.stop()
        self._warmth_mix = 1.0 if enabled else 0.0
        self._apply_warmth()

    def _update_warmth_tooltip(self):
        kelvin = warmth_kelvin(self.warmth_slider.value() / 100)
        self.warmth_slider.setToolTip(f"Warmth  ·  {kelvin}K")

    def _on_warmth_toggled(self, checked):
        self._fade_timer.stop()
        self._warmth_mix = 1.0 if checked else 0.0
        self._apply_warmth()
        self._save_timer.start()

    def _on_warmth_level_changed(self, _value):
        self._update_warmth_tooltip()
        if self.warmth_slider.isSliderDown():
            QToolTip.showText(QCursor.pos(), self.warmth_slider.toolTip(), self.warmth_slider)
        # Moving the slider means the user wants warmth on.
        if not self.warmth_checkbox.isChecked():
            self.warmth_checkbox.setChecked(True)
            return
        self._apply_warmth()
        self._save_timer.start()

    def _apply_warmth(self):
        # The mix is 0 or 1 except while a scheduled change fades in or out.
        strength = self.warmth_slider.value() / 100 * self._warmth_mix
        if not self._gamma.set_warmth(warmth_rgb(strength)):
            self._offer_gamma_unlock()

    def _set_warmth_on(self, on, fade):
        """Turn warmth on or off for the schedule, optionally fading."""
        self.warmth_checkbox.blockSignals(True)
        self.warmth_checkbox.setChecked(on)
        self.warmth_checkbox.blockSignals(False)
        target = 1.0 if on else 0.0
        if fade:
            self._fade_from = self._warmth_mix
            self._fade_to = target
            self._fade_started = time.monotonic()
            self._fade_timer.start()
        else:
            self._fade_timer.stop()
            self._warmth_mix = target
            self._apply_warmth()
        self._save_timer.start()

    def _fade_tick(self):
        if self.warmth_slider is None:
            self._fade_timer.stop()
            return
        progress = min(1.0, (time.monotonic() - self._fade_started) / self._WARMTH_FADE_SECONDS)
        self._warmth_mix = self._fade_from + (self._fade_to - self._fade_from) * progress
        self._apply_warmth()
        if progress >= 1.0:
            self._fade_timer.stop()

    @staticmethod
    def _minutes(qtime):
        return qtime.hour() * 60 + qtime.minute()

    @staticmethod
    def _time_from_minutes(minutes):
        return QTime(minutes // 60 % 24, minutes % 60)

    def _set_schedule_controls(self, enabled, start, end):
        """Set schedule widgets without their change handlers."""
        widgets = (self.schedule_checkbox, self.schedule_start_edit, self.schedule_end_edit)
        for widget in widgets:
            widget.blockSignals(True)
        self.schedule_checkbox.setChecked(enabled)
        self.schedule_start_edit.setTime(self._time_from_minutes(start))
        self.schedule_end_edit.setTime(self._time_from_minutes(end))
        for widget in widgets:
            widget.blockSignals(False)
        self._update_schedule_controls()

    def _update_schedule_controls(self):
        scheduled = self.schedule_checkbox.isChecked()
        self.schedule_start_edit.setEnabled(scheduled)
        self.schedule_end_edit.setEnabled(scheduled)
        if scheduled:
            locale = QLocale.system()
            start = locale.toString(self.schedule_start_edit.time(), QLocale.ShortFormat)
            end = locale.toString(self.schedule_end_edit.time(), QLocale.ShortFormat)
            self.warmth_more_button.setText("Auto ›")
            self.warmth_more_button.setToolTip(f"Warmth schedule  ·  {start}–{end}")
        else:
            self.warmth_more_button.setText("›")
            self.warmth_more_button.setToolTip("Warmth schedule")

    def _on_schedule_settings_changed(self, *_):
        self._update_schedule_controls()
        # A changed schedule takes over immediately, replacing any manual override.
        self._schedule_was_in_window = None
        self._check_schedule(fade=False)
        self._save_timer.start()

    def _check_schedule(self, fade):
        """Switch warmth when the clock crosses into or out of the schedule.

        Warmth changes only at those crossings, so a manual toggle holds until
        the next scheduled start or end.
        """
        if self.warmth_slider is None or not self.schedule_checkbox.isChecked():
            return
        now = self._minutes(QTime.currentTime())
        start = self._minutes(self.schedule_start_edit.time())
        end = self._minutes(self.schedule_end_edit.time())
        if start <= end:
            in_window = start <= now < end
        else:
            in_window = now >= start or now < end
        if in_window == self._schedule_was_in_window:
            return
        self._schedule_was_in_window = in_window
        if in_window != self.warmth_checkbox.isChecked():
            self._set_warmth_on(in_window, fade)

    def _offer_gamma_unlock(self):
        """Once per run, point the user at lifting the OS gamma limit."""
        if self._gamma_unlock_offered or not self._gamma.can_unlock_full_range():
            return
        self._gamma_unlock_offered = True
        self.gamma_unlock_action.setVisible(True)
        self.tray_icon.showMessage(
            "FlexLux: Warmth limited by Windows",
            "Windows limits how warm the screen can get. Choose \"Allow Stronger Warmth\" "
            "from the tray menu to lift the limit (needs administrator approval).",
            QSystemTrayIcon.Warning, 8000)

    def _unlock_gamma_range(self):
        if self._gamma is None or not self._gamma.unlock_full_range():
            return
        self.gamma_unlock_action.setVisible(False)
        self.tray_icon.showMessage(
            "FlexLux: Stronger warmth allowed",
            "Sign out of Windows and back in for the full warmth range to take effect.",
            QSystemTrayIcon.Information, 8000)

    def _on_slider_changed(self, monitor_idx, value):
        self._save_timer.start()
        if hasattr(self, '_poll_timer'):
            self._poll_timer.start()
        monitor_name = self.monitor_names[monitor_idx]
        hw = self._hw_capable[monitor_idx]
        try:
            if value == 100:
                if hw:
                    self._set_hardware_brightness(self.min_brightness, monitor_name)
            elif value > 100:
                if hw:
                    brightness_percent = (value - 100) / 100
                    new_brightness = int(self.min_brightness + (self.max_brightness - self.min_brightness) * brightness_percent)
                    self._set_hardware_brightness(new_brightness, monitor_name)
            else:
                if hw:
                    self._set_hardware_brightness(self.min_brightness, monitor_name)
            self._apply_artificial_dimming(monitor_idx)
            self._update_key_intercept_state()
        except Exception as e:
            log.warning("Could not change brightness for %s: %s", monitor_name, e)

    def _toggle_autostart(self):
        enabled = self.autostart_action.isChecked()
        try:
            autostart.set_enabled(enabled)
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

    def _reset_settings(self):
        reply = QMessageBox.question(
            self, "Reset Settings",
            "This will reset all saved settings to defaults and restart FlexLux. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        self.settings.clear()
        self.settings.sync()
        for i, slider in enumerate(self.sliders):
            slider.setValue(100)
        if self.link_checkbox:
            self.link_checkbox.setChecked(True)
        if self.warmth_slider is not None:
            self._set_warmth_controls(False, self._WARMTH_DEFAULT_LEVEL)
            self._set_schedule_controls(False, self._SCHEDULE_DEFAULT_START, self._SCHEDULE_DEFAULT_END)
            self._schedule_was_in_window = None
        self._pages.setCurrentIndex(0)
        self.adjust_window_size()

    def toggle_window(self):
        if self.isVisible():
            self.hide()
            return
        # If the panel was hidden a moment ago by this same tray click (e.g.
        # the popup auto-closed on mouse-press and the tray Trigger arrives on
        # mouse-release), the user meant "close" — don't immediately reopen.
        if time.monotonic() - self._last_hide_time < 0.3:
            return
        self.show()
        self.activateWindow()
        self.raise_()
        self.updatePosition()
        self._set_poll_speed(fast=True)

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_window()
        elif reason == QSystemTrayIcon.Context and self._ui.use_manual_tray_menu:
            if self.tray_menu.isVisible():
                self.tray_menu.hide()
            else:
                self.tray_menu.popup(QCursor.pos())

    def updatePosition(self):
        cursor_pos = QCursor.pos()
        if not self._ui.panel_y_from_top:
            # Work-area coordinates also handle non-primary screens and taskbars
            # placed on any edge, without covering the taskbar itself.
            available = QApplication.desktop().availableGeometry(cursor_pos)
            x = cursor_pos.x() - self.width() // 2
            x = max(available.left() + 12, min(x, available.right() - self.width() - 11))
            y = available.bottom() - self.height() - 11
            self.move(x, max(available.top() + 12, y))
            return
        screen_geometry = QApplication.desktop().screenGeometry(cursor_pos)

        window_width = self.width()
        window_height = self.height()

        x = cursor_pos.x() - window_width // 2
        y = screen_geometry.y() + self._ui.panel_top_offset

        x = max(screen_geometry.x(), min(x, screen_geometry.x() + screen_geometry.width() - window_width))
        y = max(screen_geometry.y(), min(y, screen_geometry.y() + screen_geometry.height() - window_height))

        self.move(x, y)

    def hideEvent(self, event):
        # Runs for every hide path (tray toggle, popup auto-close on outside
        # click, focus-loss hide), keeping poll speed and the reopen guard
        # consistent no matter how the panel was dismissed.
        self._last_hide_time = time.monotonic()
        self._set_poll_speed(fast=False)
        self._pages.setCurrentIndex(0)
        super().hideEvent(event)

    def check_focus_and_hide(self):
        if not self.isActiveWindow() and not any(w.hasFocus() for w in self.findChildren(QWidget)):
            self.hide()
            self.hide_timer.stop()

    def eventFilter(self, obj, event):
        if not self._ui.use_event_filter:
            return super().eventFilter(obj, event)

        if event.type() == QEvent.WindowDeactivate:
            self.hide_timer.start(100)
        elif event.type() == QEvent.WindowActivate:
            self.hide_timer.stop()
        return super().eventFilter(obj, event)

    def _shutdown(self):
        # Connected to QApplication.aboutToQuit, so this runs only on real
        # exits (tray-menu Quit, logout). It must NOT live in closeEvent: the
        # macOS panel is a Qt.Popup, and Qt dismisses popups on outside clicks
        # by closing them, so closeEvent fires on every dismissal — running
        # teardown there killed the overlays, DDC writer, and key interceptor
        # mid-session.
        if self._key_interceptor is not None:
            self._key_interceptor.stop()
        if hasattr(self, '_poll_timer'):
            self._poll_timer.stop()
        if hasattr(self, '_gamma_timer'):
            self._gamma_timer.stop()
        if hasattr(self, '_schedule_timer'):
            self._schedule_timer.stop()
        self._fade_timer.stop()
        if self._gamma is not None:
            self._gamma.restore()
        if hasattr(self, '_backend') and self._backend:
            self._backend.cleanup()
        for overlay in self.overlays:
            overlay.close()
