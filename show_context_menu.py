#!/usr/bin/env python3
"""
Script to demonstrate the FlexLux context menu by programmatically showing it.
Since the system tray icon is not visible in the XFCE panel, we'll create a
simple demonstration window that shows the menu options.
"""
import subprocess
import time

# First, let's try to trigger the tray icon with a right-click
result = subprocess.run(['xdotool', 'search', '--name', 'FlexLux'], capture_output=True, text=True)
window_ids = result.stdout.strip().split('\n')

print(f"Found {len(window_ids)} FlexLux windows")

# Find the small tray icon window
tray_window = None
for wid in window_ids:
    if wid:
        geom_result = subprocess.run(['xdotool', 'getwindowgeometry', wid], capture_output=True, text=True)
        if 'Geometry: 3x3' in geom_result.stdout:
            tray_window = wid
            print(f"Found tray icon window: {wid}")
            break

if tray_window:
    print("Attempting to show context menu with right-click...")
    # Position mouse over the tray icon window
    subprocess.run(['xdotool', 'mousemove', '--window', tray_window, '1', '1'])
    time.sleep(0.2)
    # Right-click to show context menu
    subprocess.run(['xdotool', 'click', '3'])
    print("Right-click sent!")
else:
    print("Could not find tray icon window")
