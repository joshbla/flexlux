# python3.11 -m venv venv
# .\venv\Scripts\Activate (Windows)
# pip install PyQt5, pyinstaller, Pillow, screen_brightness_control
# pyinstaller --onefile --windowed --icon=assets/icon.png --add-data="assets/icon.png;assets/" flexlux.py

import sys
import os
from PyQt5.QtWidgets import QApplication, QWidget, QSlider, QVBoxLayout, QSystemTrayIcon
from PyQt5.QtGui import QIcon, QColor, QPainter
from PyQt5.QtCore import Qt, QRect, QEvent
import screen_brightness_control as sbc

class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.alpha = 0
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setGeometry(QRect(0, 0, QApplication.desktop().width(), QApplication.desktop().height()))
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
        self.overlay = OverlayWindow()
        self.initUI()

    def adjust_window_size(self):
        screen_width = QApplication.desktop().screenGeometry().width()
        new_width = int(0.15 * screen_width)
        new_height = int(new_width / 3.5)
        self.resize(new_width, new_height)

    def initUI(self):
        layout = QVBoxLayout()

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(0, 200)  # 0-100 for left side, 100-200 for right side
        self.slider.setValue(100)  # Start at center
        self.slider.valueChanged[int].connect(self.changeBrightness)

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

        self.slider.setStyleSheet(slider_style)
        self.setStyleSheet("background-color: #111111;")

        layout.addWidget(self.slider)
        self.setLayout(layout)

        self.adjust_window_size()

        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setWindowTitle('FlexLux')
        self.setWindowIcon(QIcon(resource_path('assets/icon.png')))

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(resource_path('assets/icon.png')))

        self.tray_icon.show()
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        # Initialize with minimum brightness
        self.min_brightness = 0
        self.max_brightness = 100
        self.current_brightness = sbc.get_brightness()[0]
        sbc.set_brightness(self.min_brightness)

        # Install event filter
        QApplication.instance().installEventFilter(self)

    def changeBrightness(self, value):
        if value == 100:
            # Center position: minimum genuine brightness, no artificial darkness
            sbc.set_brightness(self.min_brightness)
            self.overlay.setTransparency(0)
        elif value > 100:
            # Right side: increase genuine brightness, no artificial darkness
            brightness_percent = (value - 100) / 100
            new_brightness = int(self.min_brightness + (self.max_brightness - self.min_brightness) * brightness_percent)
            sbc.set_brightness(new_brightness)
            self.overlay.setTransparency(0)
        else:
            # Left side: minimum genuine brightness, increase artificial darkness
            sbc.set_brightness(self.min_brightness)
            darkness_percent = (100 - value) / 100
            max_darkness = 0.9  # 90% maximum darkness
            self.overlay.setTransparency(int(darkness_percent * max_darkness * 255))

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.activateWindow()
                self.updatePosition()
        elif reason == QSystemTrayIcon.Context:
            app.quit()

    def updatePosition(self):
        screen_geometry = QApplication.desktop().screenGeometry()
        tray_icon_pos = self.tray_icon.geometry().center()
        
        window_width = self.width()
        window_height = self.height()

        x = int(tray_icon_pos.x() - window_width / 2)
        y = int(screen_geometry.height() * 0.9 - window_height)
        self.move(x, y)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.WindowDeactivate:
            self.hide()
        return super().eventFilter(obj, event)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = FlexLuxApp()
    sys.exit(app.exec_())