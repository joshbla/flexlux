import logging
import time
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QWidget, QSlider, QVBoxLayout, QHBoxLayout, QSystemTrayIcon, QMenu,
                             QAction, QLabel, QMessageBox, QToolTip, QStackedWidget, QPushButton,
                             QTimeEdit, QAbstractSpinBox, QGridLayout)
from PyQt5.QtGui import QIcon, QCursor, QColor, QPainter
from PyQt5.QtCore import Qt, QEvent, QTimer, QSettings, QTime, QLocale

from flexlux import VERSION
from flexlux.brightness import get_backend, get_key_interceptor
from flexlux.gamma import get_gamma_controller, warmth_rgb, warmth_kelvin
from flexlux.overlay import OverlayWindow
from flexlux.platform_ui import get_ui_config
from flexlux.schedule import (DailySchedule, DAY, WIND_DOWN, NIGHT, WAKE_UP, scheduled_warmth,
                              scheduled_brightness, step_toward, next_time_of_day, dimming_progress)
from flexlux.utils import resource_path
from flexlux.widgets import PanelCheckBox
from flexlux import autostart

log = logging.getLogger("FlexLux")

_SLIDER_HEIGHT = 28
_SLIDER_HANDLE_STYLE = """
    QSlider::handle:horizontal {
        width: 24px;
        background-color: white;
        border-radius: 11px;
        margin: -8px 0;
    }
"""


def _slider_style(groove_background):
    return f"""
    .QSlider {{
        min-height: {_SLIDER_HEIGHT}px;
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
_VALUE_LABEL_STYLE = "color: #aaaaaa; font-size: 10px;"
# Dark at the left like the Artificial half of the brightness slider it mirrors.
_NIGHT_BRIGHTNESS_GROOVE = "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #262626, stop:1 darkgray);"
_WARNING_LABEL_STYLE = "color: #c08040; font-size: 10px;"
_PHASE_NAMES = {DAY: "Day", WIND_DOWN: "Winding down", NIGHT: "Night", WAKE_UP: "Waking up"}


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
        # Applied warmth (0.0-1.0). The schedule moves it in finer steps than
        # the slider shows.
        self._warmth_strength = 0.0
        self._schedule = self._SCHEDULE_DEFAULT
        self._schedule_enabled = False
        self._schedule_paused = False
        self._paused_until = None
        # Monitor name -> brightness darkened by hand during the day, held
        # until _held_until (the next day start).
        self._held_darkening = {}
        self._held_until = None
        self._night_warmth = self._SCHEDULE_DEFAULT_NIGHT_WARMTH
        self._night_brightness = self._SCHEDULE_DEFAULT_NIGHT_BRIGHTNESS
        self._day_brightness = {}
        # True while FlexLux itself moves the controls, so the change is not
        # mistaken for the user's.
        self._driving = False
        self._schedule_moved_hw_at = 0.0
        # Large scheduled changes (startup, resume, wake from sleep) move in
        # small steps on this timer instead of jumping.
        self._slew_timer = QTimer()
        self._slew_timer.setInterval(self._SCHEDULE_SLEW_INTERVAL_MS)
        self._slew_timer.timeout.connect(self._schedule_step)
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
        self._refresh_timer.timeout.connect(lambda: self._refresh_monitors("Screen configuration changed"))
        # Swapping one screen for another (closing the laptop lid while docked,
        # waking from sleep) can keep the screen count the same, so watch the
        # screens themselves rather than how many there are.
        app = QApplication.instance()
        app.screenAdded.connect(lambda _: self._on_screens_changed())
        app.screenRemoved.connect(lambda _: self._on_screens_changed())
        self._screen_check_timer = QTimer()
        self._screen_check_timer.setInterval(self._SCREEN_CHECK_INTERVAL_MS)
        self._screen_check_timer.timeout.connect(self._check_screens)
        self._screen_check_timer.start()
        # Retries detection while a monitor's hardware control does not answer
        # yet, as when it is still waking up at login.
        self._redetect_attempts = 0
        self._redetect_timer = QTimer()
        self._redetect_timer.setSingleShot(True)
        self._redetect_timer.setInterval(self._REDETECT_RETRY_MS)
        self._redetect_timer.timeout.connect(self._retry_detection)
        self._last_redetect_at = 0.0
        self._schedule_redetect_retry()
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
        self._schedule_timer.timeout.connect(self._schedule_step)
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
        self._screens = self._screen_signature()
        # Monitor name -> last hardware brightness read, and (brightness
        # before, time) when a run of FlexLux writes began, so polling can tell
        # a write that never took effect from a change made on the monitor.
        self._hw_read = {}
        self._hw_write_check = {}
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
        self._driving = True
        try:
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
        finally:
            self._driving = False
        if self.warmth_slider is not None:
            self._set_warmth_controls(
                self.settings.value("warmth/enabled", False, type=bool),
                self.settings.value("warmth/level", self._WARMTH_DEFAULT_LEVEL, type=int))
        self._load_schedule()
        self._sync_schedule_widgets()
        self._schedule_step()

    def _load_schedule(self):
        s = self.settings
        if not s.contains("schedule/enabled") and s.contains("warmth/schedule_enabled"):
            # The old warmth-only window becomes the daily schedule; its times
            # do not carry over.
            s.setValue("schedule/enabled", s.value("warmth/schedule_enabled", False, type=bool))
            for key in ("warmth/schedule_enabled", "warmth/schedule_start", "warmth/schedule_end"):
                s.remove(key)
            log.info("Moved the warmth schedule setting to the daily schedule")
        self._schedule_enabled = s.value("schedule/enabled", False, type=bool)
        default = self._SCHEDULE_DEFAULT
        schedule = DailySchedule(
            s.value("schedule/wind_down_start", default.wind_down_start, type=int),
            s.value("schedule/night_start", default.night_start, type=int),
            s.value("schedule/wake_start", default.wake_start, type=int),
            s.value("schedule/day_start", default.day_start, type=int))
        if schedule.is_valid():
            self._schedule = schedule
        else:
            log.warning("Ignoring saved schedule with times out of order: %s", schedule)
            self._schedule = default
        self._schedule_paused = s.value("schedule/paused", False, type=bool)
        if self._schedule_paused and s.contains("schedule/paused_until"):
            self._paused_until = datetime.fromisoformat(s.value("schedule/paused_until", type=str))
        elif self._schedule_paused:
            # Paused by v1.9.0 or v1.9.1, which recorded no end.
            self._paused_until = next_time_of_day(self._now_datetime(), self._schedule.wake_start)
        self._night_warmth = max(0, min(100, s.value(
            "schedule/night_warmth", self._SCHEDULE_DEFAULT_NIGHT_WARMTH, type=int)))
        # v1.9.0 saved its default night brightness here before anyone could
        # pick it; the new default replaces it under a new key.
        s.remove("schedule/night_brightness")
        self._night_brightness = max(0, min(100, s.value(
            "schedule/night_dimming", self._SCHEDULE_DEFAULT_NIGHT_BRIGHTNESS, type=int)))
        self._held_darkening = {}
        if s.contains("schedule/held_until"):
            self._held_until = datetime.fromisoformat(s.value("schedule/held_until", type=str))
            for name in self.monitor_names:
                key = f"schedule/held_darkening/{name}"
                if s.contains(key):
                    self._held_darkening[name] = max(0, min(100, s.value(key, type=int)))
        self._day_brightness = {}
        for i, name in enumerate(self.monitor_names):
            key = f"schedule/day_brightness/{name}"
            if s.contains(key):
                upper = 200 if self._hw_capable[i] else 100
                self._day_brightness[name] = max(100, min(upper, s.value(key, type=int)))
            else:
                # A monitor the schedule has not seen yet keeps its current
                # setting as its daytime brightness, minus any darkening.
                self._day_brightness[name] = max(100, self.sliders[i].value())

    def _save_settings(self):
        for i, name in enumerate(self.monitor_names):
            self.settings.setValue(f"brightness/{name}", self.sliders[i].value())
        if self.link_checkbox:
            self.settings.setValue("link_monitors", self.link_checkbox.isChecked())
        if self.warmth_slider is not None:
            self.settings.setValue("warmth/enabled", self.warmth_checkbox.isChecked())
            self.settings.setValue("warmth/level", self.warmth_slider.value())
        self.settings.setValue("schedule/enabled", self._schedule_enabled)
        self.settings.setValue("schedule/paused", self._schedule_paused)
        if self._schedule_paused:
            self.settings.setValue("schedule/paused_until", self._paused_until.isoformat())
        else:
            self.settings.remove("schedule/paused_until")
        self.settings.remove("schedule/held_darkening")
        if self._held_darkening:
            self.settings.setValue("schedule/held_until", self._held_until.isoformat())
            for name, value in self._held_darkening.items():
                self.settings.setValue(f"schedule/held_darkening/{name}", value)
        else:
            self.settings.remove("schedule/held_until")
        self.settings.setValue("schedule/wind_down_start", self._schedule.wind_down_start)
        self.settings.setValue("schedule/night_start", self._schedule.night_start)
        self.settings.setValue("schedule/wake_start", self._schedule.wake_start)
        self.settings.setValue("schedule/day_start", self._schedule.day_start)
        self.settings.setValue("schedule/night_warmth", self._night_warmth)
        self.settings.setValue("schedule/night_dimming", self._night_brightness)
        for name, value in self._day_brightness.items():
            self.settings.setValue(f"schedule/day_brightness/{name}", value)
        self.settings.sync()

    def adjust_window_size(self):
        screen_width = QApplication.desktop().screenGeometry().width()
        self.layout().activate()
        self.setFixedSize(int(0.15 * screen_width), self.layout().sizeHint().height())

    def _fit_current_page(self):
        """Size the stack to the visible page; it otherwise takes the tallest one."""
        page = self._pages.currentWidget()
        # Unpolished labels have not taken their stylesheet fonts yet.
        page.ensurePolished()
        self._pages.setFixedHeight(page.sizeHint().height())

    def _on_page_changed(self, _index):
        self._fit_current_page()
        self.adjust_window_size()
        if self.isVisible():
            self.updatePosition()

    def _build_sliders(self, layout):
        # Page 0 holds the everyday controls; page 1 holds schedule settings.
        self._pages = QStackedWidget(self)
        main_page = QWidget(self._pages)
        self._pages.addWidget(main_page)
        layout.addWidget(self._pages)
        layout = QVBoxLayout(main_page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        # The zone names apply to every slider, so they head the column once.
        zone_style = "color: #555555; font-size: 9px;"
        zone_row = QHBoxLayout()
        zone_row.setContentsMargins(0, 0, 0, 0)
        zone_row.setSpacing(0)
        for text, stretch in (("Artificial", 1), ("|", 0), ("Natural", 1)):
            zone_label = QLabel(text)
            zone_label.setAlignment(Qt.AlignCenter)
            zone_label.setStyleSheet(zone_style if stretch else "color: #333333; font-size: 9px;")
            zone_row.addWidget(zone_label, stretch)
        layout.addLayout(zone_row)
        layout.addSpacing(2)

        self.sliders = []
        multi = len(self.monitor_names) > 1

        for i, name in enumerate(self.monitor_names):
            hw = self._hw_capable[i]

            if multi:
                if i > 0:
                    layout.addSpacing(6)
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
            self.sliders.append(slider)

        self.link_checkbox = None
        if multi:
            self.link_checkbox = PanelCheckBox("Link Monitors")
            self.link_checkbox.setStyleSheet(_CHECKBOX_STYLE)
            self.link_checkbox.setChecked(self.settings.value("link_monitors", True, type=bool))
            layout.addSpacing(6)
            layout.addWidget(self.link_checkbox)

        self.warmth_checkbox = None
        self.warmth_slider = None
        if self._gamma is not None:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            layout.addSpacing(4)
            self.warmth_checkbox = PanelCheckBox("Warmth")
            self.warmth_checkbox.setStyleSheet(_CHECKBOX_STYLE)
            self.warmth_slider = QSlider(Qt.Horizontal, self)
            self.warmth_slider.setAccessibleName("Screen warmth")
            self.warmth_slider.setStyleSheet(_slider_style(_WARMTH_GROOVE))
            row.addWidget(self.warmth_checkbox)
            row.addWidget(self.warmth_slider, 1)
            layout.addLayout(row)
            self._wire_warmth_controls()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        layout.addSpacing(4)
        self.schedule_checkbox = PanelCheckBox("Schedule")
        self.schedule_checkbox.setStyleSheet(_CHECKBOX_STYLE)
        self.schedule_checkbox.setAccessibleName("Daily schedule")
        self.schedule_status_label = QLabel()
        self.schedule_status_label.setStyleSheet(_SMALL_LABEL_STYLE)
        self.schedule_resume_button = self._make_nav_button("Resume", "Resume schedule")
        self.schedule_resume_button.setToolTip("Go back to the scheduled warmth and brightness")
        self.schedule_resume_button.clicked.connect(self._resume_schedule)
        self.schedule_more_button = self._make_nav_button("›", "Schedule settings")
        self.schedule_more_button.clicked.connect(lambda: self._pages.setCurrentIndex(1))
        row.addWidget(self.schedule_checkbox)
        row.addWidget(self.schedule_status_label, 1)
        row.addWidget(self.schedule_resume_button)
        row.addWidget(self.schedule_more_button)
        layout.addLayout(row)
        self._pages.addWidget(self._build_schedule_page())
        self._fit_current_page()
        self._pages.currentChanged.connect(self._on_page_changed)
        self._wire_schedule_controls()

    @staticmethod
    def _make_nav_button(text, accessible_name):
        button = QPushButton(text)
        button.setStyleSheet(_NAV_BUTTON_STYLE)
        button.setCursor(Qt.PointingHandCursor)
        button.setAccessibleName(accessible_name)
        return button

    def _add_night_slider(self, grid, row, title, groove):
        """A titled slider with its current value shown above its right end."""
        label = QLabel(title)
        label.setStyleSheet(_SMALL_LABEL_STYLE)
        value_label = QLabel()
        value_label.setStyleSheet(_VALUE_LABEL_STYLE)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider = QSlider(Qt.Horizontal, self)
        slider.setRange(0, 100)
        slider.setAccessibleName(title)
        slider.setStyleSheet(_slider_style(groove))
        grid.addWidget(label, row, 0, 1, 2)
        grid.addWidget(value_label, row, 2, 1, 2)
        grid.addWidget(slider, row + 1, 0, 1, 4)
        return slider, value_label

    def _build_schedule_page(self):
        page = QWidget(self._pages)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(2)
        back_button = self._make_nav_button("‹", "Back")
        back_button.clicked.connect(lambda: self._pages.setCurrentIndex(0))
        title = QLabel("Schedule")
        title.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        header.addWidget(back_button)
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        self.schedule_page_checkbox = PanelCheckBox("Warm and dim the screen every evening")
        self.schedule_page_checkbox.setStyleSheet(_CHECKBOX_STYLE)
        self.schedule_page_checkbox.setAccessibleName("Daily schedule")
        layout.addWidget(self.schedule_page_checkbox)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self.wind_down_start_edit = self._make_time_edit("Wind down starts at")
        self.night_start_edit = self._make_time_edit("Wind down ends at")
        self.wake_start_edit = self._make_time_edit("Wake up starts at")
        self.day_start_edit = self._make_time_edit("Wake up ends at")
        for row, (text, start, end) in enumerate((
                ("Wind down", self.wind_down_start_edit, self.night_start_edit),
                ("Wake up", self.wake_start_edit, self.day_start_edit))):
            label = QLabel(text)
            label.setStyleSheet(_SMALL_LABEL_STYLE)
            to_label = QLabel("to")
            to_label.setStyleSheet(_SMALL_LABEL_STYLE)
            grid.addWidget(label, row, 0)
            grid.addWidget(start, row, 1)
            grid.addWidget(to_label, row, 2)
            grid.addWidget(end, row, 3)

        self.night_warmth_slider = None
        self.night_warmth_value_label = None
        if self._gamma is not None:
            self.night_warmth_slider, self.night_warmth_value_label = self._add_night_slider(
                grid, 2, "Night warmth", _WARMTH_GROOVE)
        self.night_brightness_slider, self.night_brightness_value_label = self._add_night_slider(
            grid, 4, "Night dimming", _NIGHT_BRIGHTNESS_GROOVE)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        self.schedule_warning_label = QLabel("Wake up must start after wind down ends.")
        self.schedule_warning_label.setStyleSheet(_WARNING_LABEL_STYLE)
        # Keep its room so showing it never outgrows the panel's fixed height.
        policy = self.schedule_warning_label.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.schedule_warning_label.setSizePolicy(policy)
        self.schedule_warning_label.hide()
        layout.addWidget(self.schedule_warning_label)
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

    @staticmethod
    def _screen_signature():
        return tuple((s.name(), s.geometry().getRect()) for s in QApplication.screens())

    def _on_screens_changed(self):
        self._redetect_attempts = 0
        self._refresh_timer.start()

    def _check_screens(self):
        if self._screens != self._screen_signature() and not self._refresh_timer.isActive():
            self._on_screens_changed()

    def _schedule_redetect_retry(self):
        failed = [n for n in self.monitor_names
                  if self._backend and self._backend.hardware_probe_failed(n)]
        if failed and self._redetect_attempts < self._REDETECT_MAX_ATTEMPTS:
            self._redetect_timer.start()
        else:
            self._redetect_timer.stop()

    def _retry_detection(self):
        # Rebuilding the panel under the user's pointer would drop a drag.
        if self.isVisible():
            self._redetect_timer.start()
            return
        self._redetect_attempts += 1
        self._refresh_monitors(f"Retrying hardware brightness detection "
                               f"({self._redetect_attempts}/{self._REDETECT_MAX_ATTEMPTS})")

    def _redetect_after_failed_write(self, monitor_idx):
        """Detect monitors again, at most once per cooldown. Returns False
        during the cooldown so the caller treats the reading as an ordinary
        external change."""
        now = time.monotonic()
        if now - self._last_redetect_at < self._FAILED_WRITE_REDETECT_COOLDOWN_S:
            return False
        if any(s.isSliderDown() for s in self.sliders):
            # Polling sees the same mismatch again once the drag ends.
            return True
        self._last_redetect_at = now
        self._redetect_attempts = 0
        name = self.monitor_names[monitor_idx]
        QTimer.singleShot(0, lambda: self._refresh_monitors(
            f"Brightness change on {name} did not take effect"))
        return True

    def _refresh_monitors(self, reason):
        log.info("%s, refreshing monitors", reason)
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
        self._schedule_redetect_retry()
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
        # Leave room for the outline drawn in paintEvent.
        layout.setContentsMargins(1, 1, 1, 1)
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
        self.schedule_action = QAction("Daily Schedule", self)
        self.schedule_action.setCheckable(True)
        self.schedule_action.triggered.connect(self._set_schedule_enabled)
        self.resume_schedule_action = QAction("Resume Schedule", self)
        self.resume_schedule_action.triggered.connect(self._resume_schedule)
        self.resume_schedule_action.setVisible(False)
        self.quit_action = QAction("Quit", self)
        self.quit_action.triggered.connect(QApplication.instance().quit)
        self.tray_menu.addAction(self.schedule_action)
        self.tray_menu.addAction(self.resume_schedule_action)
        self.tray_menu.addSeparator()
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

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor("#3a3a3a"))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def _set_hardware_brightness(self, value, display):
        """Set hardware brightness (0-100) using the platform-appropriate backend."""
        if not self._backend:
            return
        if display in self._hw_read and abs(value - self._hw_read[display]) > self._POLL_THRESHOLD:
            now = time.monotonic()
            check = self._hw_write_check.get(display)
            if check is None or now - check[1] > self._WRITE_CHECK_WINDOW_S:
                self._hw_write_check[display] = (self._hw_read[display], now)
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
        settling = time.monotonic() - self._schedule_moved_hw_at < self._SCHEDULE_POLL_GRACE_S
        for i, name in enumerate(self.monitor_names):
            if not self._hw_capable[i] or settling:
                continue
            actual = self._get_hardware_brightness(name)
            if actual is None:
                continue
            self._hw_read[name] = actual
            expected = self._expected_hw_from_slider(i)
            if abs(actual - expected) <= self._POLL_THRESHOLD:
                self._hw_write_check.pop(name, None)
                continue

            check = self._hw_write_check.get(name)
            if (check is not None and time.monotonic() - check[1] <= self._WRITE_CHECK_WINDOW_S
                    and abs(actual - check[0]) <= self._POLL_THRESHOLD
                    and self._redetect_after_failed_write(i)):
                return
            self._hw_write_check.pop(name, None)

            new_slider = self._slider_from_hw(actual)
            log.info("External brightness change on %s: expected hw=%d, actual=%d, slider -> %d",
                     name, expected, actual, new_slider)

            if self.link_checkbox and self.link_checkbox.isChecked():
                self.link_checkbox.setChecked(False)

            self.sliders[i].blockSignals(True)
            self.sliders[i].setValue(new_slider)
            self.sliders[i].blockSignals(False)

            self._on_manual_brightness(i)
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
    _SCHEDULE_CHECK_INTERVAL_MS = 15000
    _SCHEDULE_SLEW_INTERVAL_MS = 200
    # Per slew tick: the default night warmth (2500K) arrives in about nine
    # seconds, and a full 100-step brightness change in ten.
    _SCHEDULE_WARMTH_STEP = 0.008
    _SCHEDULE_BRIGHTNESS_STEP = 2
    # Hardware brightness lags scheduled writes, so polling ignores
    # differences for this long after one rather than calling them manual.
    _SCHEDULE_POLL_GRACE_S = 10
    _SCREEN_CHECK_INTERVAL_MS = 3000
    _REDETECT_RETRY_MS = 30000
    _REDETECT_MAX_ATTEMPTS = 10
    # A reading within this long of FlexLux's write that still shows the
    # brightness from before it means the write went nowhere.
    _WRITE_CHECK_WINDOW_S = 30
    _FAILED_WRITE_REDETECT_COOLDOWN_S = 60
    # Matches the Govee light: winds down 8 PM to midnight, wakes 10-11 AM.
    _SCHEDULE_DEFAULT = DailySchedule(20 * 60, 0, 10 * 60, 11 * 60)
    _SCHEDULE_DEFAULT_NIGHT_WARMTH = 36  # 2500K
    _SCHEDULE_DEFAULT_NIGHT_BRIGHTNESS = 50  # 45% darker

    def _snap_and_update(self, monitor_idx, value):
        if not self._hw_capable[monitor_idx] and value > 100:
            self.sliders[monitor_idx].setValue(100)
            return
        if value != 100 and abs(value - 100) <= self._SNAP_THRESHOLD:
            self.sliders[monitor_idx].setValue(100)
            return
        if not self._driving:
            self._on_manual_brightness(monitor_idx)
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

    def _set_warmth_controls(self, enabled, level):
        """Set warmth widgets without their change handlers, then apply once."""
        level = max(0, min(100, level))
        self._show_warmth(enabled, level)
        self._warmth_strength = level / 100 if enabled else 0.0
        self._apply_warmth()

    def _show_warmth(self, enabled, level):
        self.warmth_checkbox.blockSignals(True)
        self.warmth_slider.blockSignals(True)
        self.warmth_checkbox.setChecked(enabled)
        self.warmth_slider.setValue(level)
        self.warmth_checkbox.blockSignals(False)
        self.warmth_slider.blockSignals(False)
        self._update_warmth_tooltip()

    def _update_warmth_tooltip(self):
        kelvin = warmth_kelvin(self.warmth_slider.value() / 100)
        self.warmth_slider.setToolTip(f"Warmth  ·  {kelvin}K")

    @staticmethod
    def _show_tooltip_while_dragging(slider):
        if slider.isSliderDown():
            QToolTip.showText(QCursor.pos(), slider.toolTip(), slider)

    def _on_warmth_toggled(self, checked):
        self._on_manual_warmth()
        self._warmth_strength = self.warmth_slider.value() / 100 if checked else 0.0
        self._apply_warmth()
        self._save_timer.start()

    def _on_warmth_level_changed(self, _value):
        self._update_warmth_tooltip()
        self._show_tooltip_while_dragging(self.warmth_slider)
        # Moving the slider means the user wants warmth on.
        if not self.warmth_checkbox.isChecked():
            self.warmth_checkbox.setChecked(True)
            return
        self._on_manual_warmth()
        self._warmth_strength = self.warmth_slider.value() / 100
        self._apply_warmth()
        self._save_timer.start()

    def _apply_warmth(self):
        if not self._gamma.set_warmth(warmth_rgb(self._warmth_strength)):
            self._offer_gamma_unlock()

    def _set_scheduled_warmth(self, strength):
        # Once scheduled warmth reaches zero, the slider shows the night level
        # so checking Warmth by hand during the day gives a useful tint.
        level = round(strength * 100) if strength > 0 else self._night_warmth
        self._show_warmth(strength > 0, level)
        self._warmth_strength = strength
        self._apply_warmth()
        self._save_timer.start()

    def _set_scheduled_brightness(self, monitor_idx, value):
        slider = self.sliders[monitor_idx]
        slider.blockSignals(True)
        slider.setValue(value)
        slider.blockSignals(False)
        self._on_slider_changed(monitor_idx, value)
        if self._hw_capable[monitor_idx]:
            self._schedule_moved_hw_at = time.monotonic()

    @staticmethod
    def _minutes(qtime):
        return qtime.hour() * 60 + qtime.minute()

    @staticmethod
    def _time_from_minutes(minutes):
        return QTime(minutes // 60 % 24, minutes % 60)

    @staticmethod
    def _now_datetime():
        return datetime.now()

    @staticmethod
    def _now_minutes():
        now = QTime.currentTime()
        return now.hour() * 60 + now.minute() + now.second() / 60

    def _schedule_phase(self):
        return self._schedule.phase_and_progress(self._now_minutes())

    def _schedule_time_edits(self):
        return (self.wind_down_start_edit, self.night_start_edit, self.wake_start_edit, self.day_start_edit)

    def _wire_schedule_controls(self):
        self.schedule_checkbox.toggled.connect(self._set_schedule_enabled)
        self.schedule_page_checkbox.toggled.connect(self._set_schedule_enabled)
        for edit in self._schedule_time_edits():
            edit.timeChanged.connect(self._on_schedule_times_edited)
        if self.night_warmth_slider is not None:
            self.night_warmth_slider.valueChanged[int].connect(self._on_night_warmth_changed)
        self.night_brightness_slider.valueChanged[int].connect(self._on_night_brightness_changed)

    def _sync_schedule_widgets(self):
        """Show the schedule state in the panel without triggering handlers."""
        widgets = [self.schedule_checkbox, self.schedule_page_checkbox, self.night_brightness_slider,
                   *self._schedule_time_edits()]
        if self.night_warmth_slider is not None:
            widgets.append(self.night_warmth_slider)
        for widget in widgets:
            widget.blockSignals(True)
        self.schedule_checkbox.setChecked(self._schedule_enabled)
        self.schedule_page_checkbox.setChecked(self._schedule_enabled)
        times = (self._schedule.wind_down_start, self._schedule.night_start,
                 self._schedule.wake_start, self._schedule.day_start)
        for edit, minutes in zip(self._schedule_time_edits(), times):
            edit.setTime(self._time_from_minutes(minutes))
        if self.night_warmth_slider is not None:
            self.night_warmth_slider.setValue(self._night_warmth)
        self.night_brightness_slider.setValue(self._night_brightness)
        for widget in widgets:
            widget.blockSignals(False)
        self.schedule_warning_label.hide()
        self._update_night_labels()
        self._update_schedule_status()

    def _update_night_labels(self):
        if self.night_warmth_slider is not None:
            kelvin = warmth_kelvin(self._night_warmth / 100)
            self.night_warmth_value_label.setText(f"{kelvin}K")
            self.night_warmth_slider.setToolTip("Left: neutral  ·  Right: warmest")
        darker = round(self._overlay_alpha_for_value(self._night_brightness) / 255 * 100)
        self.night_brightness_value_label.setText(f"{darker}% darker" if darker else "Not dimmed")
        self.night_brightness_slider.setToolTip("Left: darkest  ·  Right: not dimmed")

    def _update_schedule_status(self):
        if not self._schedule_enabled:
            status = "Off"
        elif self._schedule_paused:
            status = "Paused"
        else:
            status = _PHASE_NAMES[self._schedule_phase()[0]]
        paused = self._schedule_enabled and self._schedule_paused
        self.schedule_status_label.setText(status)
        self.schedule_resume_button.setVisible(paused)
        self.schedule_action.setChecked(self._schedule_enabled)
        self.resume_schedule_action.setVisible(paused)
        locale = QLocale.system()
        times = [locale.toString(self._time_from_minutes(m), QLocale.ShortFormat)
                 for m in (self._schedule.wind_down_start, self._schedule.night_start,
                           self._schedule.wake_start, self._schedule.day_start)]
        self.schedule_more_button.setToolTip(
            f"Wind down {times[0]}–{times[1]}  ·  Wake up {times[2]}–{times[3]}")

    def _set_schedule_enabled(self, enabled):
        if enabled == self._schedule_enabled:
            return
        self._schedule_enabled = enabled
        self._schedule_paused = False
        if enabled:
            phase, _ = self._schedule_phase()
            for i, name in enumerate(self.monitor_names):
                if phase == DAY or name not in self._day_brightness:
                    self._day_brightness[name] = max(100, self.sliders[i].value())
        else:
            # Turning the schedule off leaves the screen as it is, so it never
            # jumps to full brightness or neutral color at night.
            self._slew_timer.stop()
        log.info("Daily schedule %s", "enabled" if enabled else "disabled")
        self._sync_schedule_widgets()
        self._save_timer.start()
        self._schedule_step()

    def _on_schedule_times_edited(self, *_):
        schedule = DailySchedule(*(self._minutes(edit.time()) for edit in self._schedule_time_edits()))
        if not schedule.is_valid():
            self.schedule_warning_label.show()
            return
        self.schedule_warning_label.hide()
        self._schedule = schedule
        self._save_timer.start()
        self._schedule_step()

    def _on_night_warmth_changed(self, value):
        self._night_warmth = value
        self._update_night_labels()
        self._show_tooltip_while_dragging(self.night_warmth_slider)
        self._save_timer.start()
        self._schedule_step()

    def _on_night_brightness_changed(self, value):
        self._night_brightness = value
        self._update_night_labels()
        self._show_tooltip_while_dragging(self.night_brightness_slider)
        self._save_timer.start()
        self._schedule_step()

    def _on_manual_brightness(self, monitor_idx):
        """During the day a brightness change sets that monitor's daytime
        brightness, or holds its darkening until the next day starts; at any
        other time it pauses the schedule."""
        if not self._schedule_enabled or self._schedule_paused:
            return
        if self._schedule_phase()[0] != DAY:
            self._pause_schedule()
            return
        name = self.monitor_names[monitor_idx]
        value = self.sliders[monitor_idx].value()
        if value >= 100:
            self._day_brightness[name] = value
            self._held_darkening.pop(name, None)
        else:
            # Darkening by hand (a dark room) holds through the evening: the
            # schedule dims further only once it would be darker, and the
            # next day ends it.
            self._held_darkening[name] = value
            self._held_until = next_time_of_day(self._now_datetime(), self._schedule.day_start)
        self._save_timer.start()

    def _on_manual_warmth(self):
        if self._schedule_enabled and not self._schedule_paused:
            self._pause_schedule()

    def _pause_schedule(self):
        # A pause lasts until Resume or the next wake-up, so a change made one
        # evening never carries darkening or warmth into the next day.
        self._paused_until = next_time_of_day(self._now_datetime(), self._schedule.wake_start)
        log.info("Manual change: schedule paused until resumed or %s", self._paused_until)
        self._schedule_paused = True
        self._slew_timer.stop()
        self._update_schedule_status()
        self._save_timer.start()

    def _resume_schedule(self):
        if not self._schedule_paused:
            return
        log.info("Schedule resumed")
        self._schedule_paused = False
        self._save_timer.start()
        self._schedule_step()

    def _schedule_step(self):
        """Move warmth and brightness toward the schedule's values for now.

        The ordinary drift of the schedule is smaller than one step and lands
        at once. Bigger gaps (startup, resume, waking from sleep, edited
        settings) are crossed one step per slew tick instead of jumping.
        """
        if self._schedule_paused and self._now_datetime() >= self._paused_until:
            log.info("Wake-up reached: schedule resumed")
            self._schedule_paused = False
            self._save_timer.start()
        if not self._schedule_enabled or self._schedule_paused:
            self._slew_timer.stop()
            self._update_schedule_status()
            return
        if self._held_darkening and self._now_datetime() >= self._held_until:
            log.info("Day reached: held darkening ended")
            self._held_darkening = {}
            self._save_timer.start()
        phase, progress = self._schedule_phase()
        settled = True
        self._driving = True
        try:
            if self.warmth_slider is not None:
                target = scheduled_warmth(progress, self._night_warmth) / 100
                strength = step_toward(self._warmth_strength, target, self._SCHEDULE_WARMTH_STEP)
                if strength != self._warmth_strength:
                    self._set_scheduled_warmth(strength)
                settled = settled and strength == target
            for i, name in enumerate(self.monitor_names):
                day = self._day_brightness[name]
                held = self._held_darkening.get(name)
                if held is None:
                    target = scheduled_brightness(dimming_progress(phase, progress), day,
                                                  self._night_brightness)
                elif phase == WAKE_UP:
                    # Wake up brightens from wherever the night left off.
                    target = scheduled_brightness(progress, day, min(held, self._night_brightness))
                else:
                    target = min(held, scheduled_brightness(dimming_progress(phase, progress), day,
                                                            self._night_brightness))
                target = round(target)
                current = self.sliders[i].value()
                value = step_toward(current, target, self._SCHEDULE_BRIGHTNESS_STEP)
                if value != current:
                    self._set_scheduled_brightness(i, value)
                settled = settled and value == target
        finally:
            self._driving = False
        if settled:
            self._slew_timer.stop()
        elif not self._slew_timer.isActive():
            self._slew_timer.start()
        self._update_schedule_status()

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
        self._slew_timer.stop()
        self._schedule_enabled = False
        self._schedule_paused = False
        self._schedule = self._SCHEDULE_DEFAULT
        self._night_warmth = self._SCHEDULE_DEFAULT_NIGHT_WARMTH
        self._night_brightness = self._SCHEDULE_DEFAULT_NIGHT_BRIGHTNESS
        self._day_brightness = {}
        self._held_darkening = {}
        for i, slider in enumerate(self.sliders):
            slider.setValue(100)
        if self.link_checkbox:
            self.link_checkbox.setChecked(True)
        if self.warmth_slider is not None:
            self._set_warmth_controls(False, self._WARMTH_DEFAULT_LEVEL)
        self._sync_schedule_widgets()
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
        # Discard schedule times left out of order; the saved ones still apply.
        if not self.schedule_warning_label.isHidden():
            self._sync_schedule_widgets()
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
        self._slew_timer.stop()
        self._screen_check_timer.stop()
        self._redetect_timer.stop()
        self._refresh_timer.stop()
        if self._gamma is not None:
            self._gamma.restore()
        if hasattr(self, '_backend') and self._backend:
            self._backend.cleanup()
        for overlay in self.overlays:
            overlay.close()
