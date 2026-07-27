# FlexLux

PyQt5 desktop app for adjusting monitor brightness via a system tray slider. The code lives in the `flexlux/` package. See `README.md` for full usage and build instructions.

**Platform note:** Primarily developed for Windows. macOS and Linux (X11) also supported but less tested.

## Cursor Cloud specific instructions

### Running the application

```bash
source venv/bin/activate
python3 -m flexlux
```

The app starts as a **system tray icon** — it does not open a visible window by default. Click the tray icon to toggle the brightness slider. Right-click for Quit.

- **Slider left (0–100):** artificial darkening overlay (PyQt5 translucent window)
- **Slider center (100):** minimum hardware brightness, no overlay
- **Slider right (100–200):** increase hardware brightness

### Caveats on Linux cloud VMs

- `screen-brightness-control` will warn/fail when no real monitor hardware is detected. The overlay-based dimming (left half of slider) still works since it's purely PyQt5.
- The app needs an X11 display. The VM typically has `DISPLAY=:1` with Xvfb or Xorg already running.
- Set `XDG_RUNTIME_DIR=/tmp/runtime-ubuntu` to suppress a harmless Qt warning.
- The system tray icon is very small (~3×3px) in some desktop environments; use `xdotool` to find/interact with FlexLux windows programmatically if needed.

### Hardware brightness caveat (DDC/CI)

Hardware brightness requires an unbroken DDC/CI path over the video cable. KVM switches and budget USB-C hubs commonly drop DDC/CI (the monitor still appears by name, but commands fail), leaving the app overlay-only for that display. The monitor's USB upstream port is not a workaround — it carries no brightness control. The primary dev machine deliberately runs a one-cable USB-C hub setup, so external hardware brightness is expected to be unavailable there; do not chase this as a bug.

### Linting

No project-specific lint config exists. For basic checks:

```bash
source venv/bin/activate
flake8 flexlux/ --max-line-length=120
```

Pre-existing style warnings (whitespace, line length) are expected.

### Building

```bash
source venv/bin/activate
pyinstaller flexlux.spec
```

Produces a platform-specific executable in `dist/`. See `README.md` for details.

### Tests

No automated test suite exists in this repo (test files are `.gitignore`d).
