import logging
from PyQt5.QtWidgets import QApplication, QWidget, QSlider, QVBoxLayout, QHBoxLayout, QSystemTrayIcon, QMenu, QAction, QLabel, QMessageBox, QCheckBox
from PyQt5.QtGui import QIcon, QCursor
from PyQt5.QtCore import Qt, QEvent, QTimer, QSettings

from flexlux import VERSION
from flexlux.brightness import get_backend
from flexlux.overlay import OverlayWindow
from flexlux.platform_ui import get_ui_config
from flexlux.utils import resource_path
from flexlux import autostart

log = logging.getLogger("FlexLux")


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
        self._backend = None
        try:
            self._backend = get_backend()
            self.monitor_names = self._backend.list_monitors()
        except Exception as e:
            log.warning("Brightness backend init failed: %s", e)
            self._backend = None
            self.monitor_names = []

        self.min_brightness = self._backend.min_brightness if self._backend else 0

        if self._backend and self.monitor_names:
            self._hw_capable = [self._backend.has_hardware_control(n) for n in self.monitor_names]
        else:
            self._hw_capable = []

        if not self.monitor_names:
            self.monitor_names = ["Display"]
            self._hw_capable = [False]

    def _create_overlays(self):
        desktop = QApplication.desktop()
        screen_count = desktop.screenCount()
        self._overlay_for_monitor = []

        if len(self.monitor_names) > 1 and screen_count > 1:
            screen_to_overlay = {}
            self.overlays = []
            for mon_idx, name in enumerate(self.monitor_names):
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
            self._overlay_for_monitor = [0] * len(self.monitor_names)

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
        handle_w = self._ui.slider_handle_width
        handle_r = self._ui.slider_handle_radius
        handle_m = self._ui.slider_handle_margin
        groove_h = self._ui.slider_groove_height
        max_h = self._ui.slider_max_height

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

        zfs = self._ui.zone_label_font_size
        zone_label_style = f"color: #555555; font-size: {zfs}px; margin: 0; padding: 0;"
        zone_label_disabled_style = f"color: #222222; font-size: {zfs}px; margin: 0; padding: 0;"
        mid_label_style = f"color: #333333; font-size: {zfs}px; margin: 0; padding: 0;"

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
            lbl_mid.setFixedWidth(self._ui.mid_label_width)
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
        self.quit_action = QAction("Quit", self)
        self.quit_action.triggered.connect(QApplication.instance().quit)
        self.tray_menu.addAction(self.autostart_action)
        self.tray_menu.addAction(self.about_action)
        self.tray_menu.addAction(self.reset_action)
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

            self._recalc_overlay(self._overlay_for_monitor[i])

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
            self._recalc_overlay(self._overlay_for_monitor[monitor_idx])
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
        self.adjust_window_size()

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
        elif reason == QSystemTrayIcon.Context and self._ui.use_manual_tray_menu:
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

        if self._ui.panel_y_from_top:
            y = screen_geometry.y() + self._ui.panel_top_offset
        else:
            y = screen_geometry.height() - window_height - self._ui.panel_bottom_offset

        x = max(screen_geometry.x(), min(x, screen_geometry.x() + screen_geometry.width() - window_width))
        y = max(screen_geometry.y(), min(y, screen_geometry.y() + screen_geometry.height() - window_height))

        self.move(x, y)

    def check_focus_and_hide(self):
        if not self.isActiveWindow() and not any(w.hasFocus() for w in self.findChildren(QWidget)):
            self.hide()
            self.hide_timer.stop()
            self._set_poll_speed(fast=False)

    def eventFilter(self, obj, event):
        if not self._ui.use_event_filter:
            return super().eventFilter(obj, event)

        if event.type() == QEvent.WindowDeactivate:
            self.hide_timer.start(100)
        elif event.type() == QEvent.WindowActivate:
            self.hide_timer.stop()
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        if hasattr(self, '_poll_timer'):
            self._poll_timer.stop()
        if hasattr(self, '_backend') and self._backend:
            self._backend.cleanup()
        for overlay in self.overlays:
            overlay.close()
        event.accept()
