#!/usr/bin/env python3
"""Demonstrate the FlexLux context menu"""
import sys
from PyQt5.QtWidgets import QApplication, QMenu, QAction
from PyQt5.QtCore import QTimer

app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

# Create the context menu
menu = QMenu()
menu.setStyleSheet('''
    QMenu { 
        font-size: 16px; 
        padding: 5px;
        background-color: white;
        border: 1px solid gray;
    } 
    QMenu::item { 
        padding: 8px 30px;
    }
    QMenu::item:selected {
        background-color: #0078d7;
        color: white;
    }
    QMenu::separator {
        height: 1px;
        background-color: lightgray;
        margin: 5px 0px;
    }
''')

show_action = QAction('Show/Hide', menu)
quit_action = QAction('Quit', menu)
menu.addAction(show_action)
menu.addSeparator()
menu.addAction(quit_action)

# Show menu at a visible location (near the slider)
menu.move(50, 550)
menu.show()

print("Context menu is now visible. Press Ctrl+C to close.")

# Keep the menu open for 10 seconds
QTimer.singleShot(10000, app.quit)

app.exec_()
