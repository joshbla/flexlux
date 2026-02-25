# Python 3.9+ required (3.10+ recommended)
# python3.11 -m venv venv
# .\venv\Scripts\Activate (Windows) or source venv/bin/activate (Linux/macOS)
# pip install PyQt5 pyinstaller Pillow screen_brightness_control
# Build commands:
# Windows: pyinstaller --onefile --windowed --icon=assets/icon.png --add-data="assets/icon.png;assets/" flexlux.py
# Linux/macOS: pyinstaller --onefile --windowed --icon=assets/icon.png --add-data="assets/icon.png:assets/" flexlux.py
# Or use the spec file: pyinstaller flexlux.spec

import sys
import os
import platform
from PyQt5.QtWidgets import QApplication, QWidget, QSlider, QVBoxLayout, QSystemTrayIcon, QMenu, QAction, QLabel
from PyQt5.QtGui import QIcon, QColor, QPainter, QCursor
from PyQt5.QtCore import Qt, QRect, QEvent, QTimer
import screen_brightness_control as sbc

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
        elif platform.system() == "Darwin":  # macOS
            self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
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
        self.min_brightness = 0
        self.max_brightness = 100
        self._detect_monitors()
        self._create_overlays()
        self.initUI()
        self.hide_timer = QTimer()
        self.hide_timer.timeout.connect(self.check_focus_and_hide)

    def _detect_monitors(self):
        try:
            self.monitor_names = sbc.list_monitors()
        except Exception:
            self.monitor_names = []
        self._has_hardware_monitors = len(self.monitor_names) > 0
        if not self.monitor_names:
            self.monitor_names = ["Display"]

    def _create_overlays(self):
        desktop = QApplication.desktop()
        screen_count = desktop.screenCount()
        if len(self.monitor_names) > 1 and screen_count > 1:
            self.overlays = []
            for i in range(screen_count):
                self.overlays.append(OverlayWindow(desktop.screenGeometry(i)))
        else:
            self.overlays = [OverlayWindow()]

    def adjust_window_size(self):
        screen_width = QApplication.desktop().screenGeometry().width()
        new_width = int(0.15 * screen_width)
        single_height = int(new_width / 3.5)
        n = len(self.monitor_names)
        if n > 1:
            new_height = single_height * n + 16 * (n - 1)
        else:
            new_height = single_height
        self.resize(new_width, new_height)

    def initUI(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)

        slider_style = """
        .QSlider {
            max-height: 70px;
        }
        QSlider::groove:horizontal {
            height: 10px;
            background-color: darkgray;
        }
        QSlider::handle:horizontal {
            width: 50px;
            background-color: white;
            border-radius: 25px;
            margin: -20px 0;
        }
        """

        self.sliders = []
        multi = len(self.monitor_names) > 1

        for i, name in enumerate(self.monitor_names):
            if multi:
                label = QLabel(name)
                label.setStyleSheet("color: #888888; font-size: 10px; margin: 0; padding: 0;")
                layout.addWidget(label)

            slider = QSlider(Qt.Horizontal, self)
            slider.setRange(0, 200)
            slider.setValue(100)
            slider.setStyleSheet(slider_style)
            slider.valueChanged[int].connect(lambda value, idx=i: self._on_slider_changed(idx, value))
            layout.addWidget(slider)
            self.sliders.append(slider)

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
        
        # Add context menu for cross-platform compatibility
        self.tray_menu = QMenu()
        self.show_action = QAction("Show/Hide", self)
        self.show_action.triggered.connect(self.toggle_window)
        self.quit_action = QAction("Quit", self)
        self.quit_action.triggered.connect(app.quit)
        self.tray_menu.addAction(self.show_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.quit_action)
        self.tray_icon.setContextMenu(self.tray_menu)

        self.tray_icon.show()
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        # Initialize brightness safely per monitor
        for name in self.monitor_names:
            try:
                if self._has_hardware_monitors:
                    sbc.set_brightness(self.min_brightness, display=name)
            except Exception as e:
                print(f"Warning: Could not initialize brightness for {name}: {e}")

        # Platform-specific event handling
        if platform.system() != "Darwin":  # Not macOS
            QApplication.instance().installEventFilter(self)

    def _on_slider_changed(self, monitor_idx, value):
        monitor_name = self.monitor_names[monitor_idx]
        overlay = self.overlays[min(monitor_idx, len(self.overlays) - 1)]
        try:
            if value == 100:
                if self._has_hardware_monitors:
                    sbc.set_brightness(self.min_brightness, display=monitor_name)
                overlay.setTransparency(0)
            elif value > 100:
                if self._has_hardware_monitors:
                    brightness_percent = (value - 100) / 100
                    new_brightness = int(self.min_brightness + (self.max_brightness - self.min_brightness) * brightness_percent)
                    sbc.set_brightness(new_brightness, display=monitor_name)
                overlay.setTransparency(0)
            else:
                if self._has_hardware_monitors:
                    sbc.set_brightness(self.min_brightness, display=monitor_name)
                darkness_percent = (100 - value) / 100
                max_darkness = 0.9
                overlay.setTransparency(int(darkness_percent * max_darkness * 255))
        except Exception as e:
            print(f"Warning: Could not change brightness for {monitor_name}: {e}")

    def toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()
            self.raise_()
            self.updatePosition()

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_window()
        elif reason == QSystemTrayIcon.Context and platform.system() == "Windows":
            # On Windows, right-click shows context menu automatically
            pass

    def updatePosition(self):
        # Get cursor position for better cross-platform positioning
        cursor_pos = QCursor.pos()
        screen_geometry = QApplication.desktop().screenGeometry(cursor_pos)
        
        window_width = self.width()
        window_height = self.height()

        # Position near cursor but ensure it's on screen
        x = cursor_pos.x() - window_width // 2
        y = screen_geometry.height() - window_height - 50  # 50px from bottom
        
        # Ensure window stays on screen
        x = max(screen_geometry.x(), min(x, screen_geometry.x() + screen_geometry.width() - window_width))
        y = max(screen_geometry.y(), min(y, screen_geometry.y() + screen_geometry.height() - window_height))
        
        self.move(x, y)

    def check_focus_and_hide(self):
        # Check if the window or any of its children has focus
        if not self.isActiveWindow() and not any(w.hasFocus() for w in self.findChildren(QWidget)):
            self.hide()
            self.hide_timer.stop()

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
        for overlay in self.overlays:
            overlay.close()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Keep running in system tray
    ex = FlexLuxApp()
    sys.exit(app.exec_())