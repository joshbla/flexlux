import sys
import os
import platform
import logging

if __package__ is None or __package__ == "":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

from flexlux import VERSION
from flexlux.app import FlexLuxApp

log = logging.getLogger("FlexLux")


def main():
    log.info("FlexLux v%s starting on %s", VERSION, platform.system())
    if platform.system() == "Windows":
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ex = FlexLuxApp()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
