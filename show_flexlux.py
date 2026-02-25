#!/usr/bin/env python3
import subprocess
import time

# Find FlexLux windows
result = subprocess.run(['xdotool', 'search', '--name', 'FlexLux'], capture_output=True, text=True)
window_ids = result.stdout.strip().split('\n')

print(f"Found {len(window_ids)} FlexLux windows")

# Try to find the slider window (not the overlay which is fullscreen)
for wid in window_ids:
    if wid:
        geom_result = subprocess.run(['xdotool', 'getwindowgeometry', wid], capture_output=True, text=True)
        print(f"\nWindow {wid}:")
        print(geom_result.stdout)
        
        # Check if it's a small window (likely the slider or tray icon)
        if 'Geometry: 3x3' in geom_result.stdout or 'Geometry: 1x1' in geom_result.stdout:
            print(f"Found small window (tray icon): {wid}")
            # Try to simulate a click on this window
            print("Attempting to click on tray icon...")
            subprocess.run(['xdotool', 'mousemove', '--window', wid, '1', '1'])
            time.sleep(0.1)
            subprocess.run(['xdotool', 'click', '1'])
            time.sleep(0.5)
