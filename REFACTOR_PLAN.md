# FlexLux Refactoring Plan

Split the single-file application (`flexlux.py`, ~900 lines) into a proper Python package with platform-specific code isolated behind clean interfaces.

## Motivation

The app supports Windows, macOS, and Linux, but all platform-specific code is interleaved in one file via scattered `platform.system()` checks. This makes it hard to:

- Fix a bug on one platform without risking regressions on others
- Understand what behavior differs per platform at a glance
- Test or develop for a single platform in isolation

## Current platform-specific code

| Area | Windows | macOS | Linux | Approx. lines |
|------|---------|-------|-------|---------------|
| **Brightness backend** | `screen_brightness_control` | `MacBrightnessControl` (ctypes DisplayServices + m1ddc subprocess) | `screen_brightness_control` | ~233 (Mac alone) |
| **Autostart** | `winreg` registry key | LaunchAgent plist file | XDG `.desktop` file | ~110 |
| **Overlay window flags** | `Tool` | `WindowDoesNotAcceptFocus` (no `Tool`) | `X11BypassWindowManagerHint` | 3 branches |
| **App window flags** | `Tool \| Frameless` | `Tool \| Frameless \| StaysOnTop` | same as Windows | 2 branches |
| **Tray context menu** | `setContextMenu()` | manual `popup()` on right-click | same as Windows | 2 call sites |
| **Window positioning** | Near bottom (taskbar) | Near top (menu bar, +30px offset) | Near bottom | 1 branch |
| **Event filter** | Installed on app | Skipped | Installed on app | 1 branch |
| **Min brightness** | `0` | `1` | `0` | 1 branch |

**Total:** 8+ `platform.system()` branch points scattered across the file.

## Target package layout

```
flexlux/
    __init__.py              # VERSION, package docstring
    __main__.py              # Entry point (replaces top-level flexlux.py)
    app.py                   # FlexLuxApp — UI, tray, sliders, settings (platform-agnostic)
    overlay.py               # OverlayWindow widget
    autostart.py             # Cross-platform autostart dispatch
    platform_ui.py           # PlatformUIConfig dataclass — window flags, positioning, etc.
    logging_setup.py         # Logger configuration
    utils.py                 # resource_path() helper
    brightness/
        __init__.py          # BrightnessBackend protocol + get_backend() factory
        mac.py               # macOS: DisplayServices + m1ddc
        sbc_backend.py       # Windows/Linux: screen_brightness_control wrapper
```

### Design principles

- **`app.py` should have zero `platform.system()` calls.** All platform knowledge lives in `brightness/`, `autostart.py`, and `platform_ui.py`.
- **Each platform-varying module exposes a uniform interface.** The brightness subpackage uses a `BrightnessBackend` protocol; autostart exposes `is_enabled()` / `set_enabled()`; platform_ui provides a `PlatformUIConfig` dataclass.
- **Conditional imports stay at the boundary.** `brightness/__init__.py` lazily imports `mac.py` or `sbc_backend.py` based on platform. No consumer needs to know which backend is in use.

### Key interfaces

**BrightnessBackend protocol:**
```python
class BrightnessBackend:
    def list_monitors(self) -> list[str]: ...
    def has_hardware_control(self, display: str) -> bool: ...
    def get_brightness(self, display: str) -> int | None: ...
    def set_brightness(self, value: int, display: str) -> None: ...
    def get_display_bounds(self, display: str) -> tuple | None: ...
    def cleanup(self) -> None: ...
```

**PlatformUIConfig dataclass:**
```python
@dataclass
class PlatformUIConfig:
    overlay_flags: Qt.WindowFlags
    app_window_flags: Qt.WindowFlags
    min_brightness: int
    panel_y_position: str            # "top" or "bottom"
    use_event_filter: bool
    use_manual_tray_menu: bool
    menu_bar_offset: int
    taskbar_offset: int
```

### External file updates required

- **`flexlux.spec`**: Change entry point from `['flexlux.py']` to `['flexlux/__main__.py']`.
- **`flexlux.sh`**: Change `python flexlux.py` to `python -m flexlux`.
- **`flexlux.bat`**: Change `python flexlux.py` to `python -m flexlux`.
- **`README.md`**: Update "Run the application" command and manual build instructions.
- **`AGENTS.md`**: Update run/build instructions.
- **Autostart `__file__` references**: Pass executable path from `app.py` instead of computing inside autostart, since `__file__` would point to the wrong module after the split.

---

## Phase plan

Each phase produces a working application and was committed separately.

### Phase 1: Scaffold the package and entry point (v1.4.0) ✓

Created the `flexlux/` package directory. Moved existing code into the package with a thin `__main__.py` entry point. Updated `flexlux.spec`, launcher scripts, `README.md`, and `AGENTS.md` to use `python -m flexlux`. Deleted the old top-level `flexlux.py`.

### Phase 2: Extract logging, utils, and overlay (v1.4.1) ✓

Moved `_setup_logging()` into `logging_setup.py`, `resource_path()` into `utils.py`, `OverlayWindow` into `overlay.py`. Logging is configured once in `__init__.py`; all modules use `logging.getLogger("FlexLux")`.

### Phase 3: Extract autostart (v1.4.2) ✓

Moved autostart logic into `autostart.py` as free functions (`is_enabled()`, `set_enabled()`). ~110 lines removed from `FlexLuxApp`.

### Phase 4: Extract brightness backends (v1.4.3) ✓

Created `brightness/` subpackage with `BrightnessBackend` protocol, `MacBrightnessBackend` (mac.py), and `SbcBrightnessBackend` (sbc_backend.py). ~250 lines extracted. `app.py` no longer imports `sbc`, `ctypes`, `subprocess`, or `shutil`.

### Phase 5: Extract platform UI config (v1.4.4) ✓

Created `platform_ui.py` with frozen `PlatformUIConfig` dataclass. All 7 `platform.system()` calls removed from `app.py` and `overlay.py`. Platform behavior declared in one place.

### Phase 6: Final cleanup (v1.4.5) ✓

Added hidden imports for lazy-loaded brightness backends to `flexlux.spec` so PyInstaller CI builds work. Verified zero `platform.system()` calls in `app.py` and `overlay.py`. Refactor complete.

## Final module inventory

| Module | Lines | Purpose |
|--------|-------|---------|
| `__init__.py` | 4 | VERSION, logging setup trigger |
| `__main__.py` | 27 | Entry point (`python -m flexlux`) |
| `app.py` | 460 | FlexLuxApp — UI, tray, sliders, settings (platform-agnostic) |
| `autostart.py` | 109 | Cross-platform autostart (winreg / launchd / XDG) |
| `brightness/__init__.py` | 23 | BrightnessBackend protocol + factory |
| `brightness/mac.py` | 245 | macOS: DisplayServices + m1ddc |
| `brightness/sbc_backend.py` | 32 | Windows/Linux: screen_brightness_control wrapper |
| `logging_setup.py` | 30 | Logger configuration |
| `overlay.py` | 36 | Translucent darkening overlay widget |
| `platform_ui.py` | 75 | PlatformUIConfig dataclass (window flags, slider dims, etc.) |
| `utils.py` | 9 | PyInstaller-aware asset path resolution |
