# activate
# pyinstaller --onefile --windowed --icon=assets/icon.png --add-data="assets/icon.png;assets/" dimmer.py

import sys
import os
from PyQt5.QtWidgets import QApplication, QWidget, QSlider, QVBoxLayout, QSystemTrayIcon
from PyQt5.QtGui import QIcon, QColor, QPainter
from PyQt5.QtCore import Qt, QRect

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
    """ Get the absolute path for the given relative path.
        Especially useful for PyInstaller bundled applications.
    """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


class DimmerApp(QWidget):
    def __init__(self):
        super().__init__()
        self.overlay = OverlayWindow()
        self.initUI()

    def adjust_window_size(self):
        screen_width = QApplication.desktop().screenGeometry().width()

        # Calculate width and height based on the screen's resolution
        new_width = int(0.15 * screen_width)
        new_height = int(new_width / 3.5)

        self.resize(new_width, new_height)


    def initUI(self):
        layout = QVBoxLayout()

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(0, 255)
        self.slider.setValue(0)
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

        # Set the background color to nearly black
        self.setStyleSheet("background-color: #111111;")  # Nearly black color

        layout.addWidget(self.slider)
        self.setLayout(layout)

        self.adjust_window_size()  # Adjust the size of the slider window

        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setWindowTitle('Dimmer')
        self.setWindowIcon(QIcon(resource_path('assets/icon.png')))

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(resource_path('assets/icon.png')))

        self.tray_icon.show()
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

    def changeBrightness(self, value):
        self.overlay.setTransparency(value)

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
        screen_geometry = QApplication.desktop().screenGeometry()  # Get screen dimensions
        tray_icon_pos = self.tray_icon.geometry().center()
        
        window_width = self.width()
        window_height = self.height()

        x = int(tray_icon_pos.x() - window_width / 2)
        y = int(screen_geometry.height() * 0.9 - window_height)  # Positioning the widget at 90% of screen height
        self.move(x, y)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = DimmerApp()
    sys.exit(app.exec_())
