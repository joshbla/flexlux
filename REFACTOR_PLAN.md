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

Each phase produces a working application and is committed separately.

### Phase 1: Scaffold the package and entry point

Create the `flexlux/` package directory. Move the existing code into the package as-is, with a thin `__main__.py` entry point. Update `flexlux.spec`, launcher scripts, `README.md`, and `AGENTS.md` to use `python -m flexlux`. Delete the old top-level `flexlux.py`.

**Result:** App runs identically via `python -m flexlux`. No logic changes.

### Phase 2: Extract logging, utils, and overlay

Move `_setup_logging()` into `logging_setup.py`. Move `resource_path()` into `utils.py`. Move `OverlayWindow` into `overlay.py`. Each consuming module uses `log = logging.getLogger("FlexLux")` instead of the module-level global.

**Result:** Three small, self-contained modules extracted. App unchanged.

### Phase 3: Extract autostart

Move `_is_autostart_enabled`, `_set_autostart`, and `_get_autostart_executable` into `autostart.py` as free functions. `FlexLuxApp` calls into the module instead of using private methods. The executable path is passed as an argument rather than computed via `__file__`.

**Result:** ~110 lines removed from the main class. Platform autostart logic isolated.

### Phase 4: Extract brightness backends

Create the `brightness/` subpackage. Move `MacBrightnessControl` and its ctypes structs into `mac.py`. Create `sbc_backend.py` wrapping `screen_brightness_control` behind the same interface. Add the `BrightnessBackend` protocol and `get_backend()` factory in `brightness/__init__.py`. Remove all brightness-related platform checks from `app.py`.

**Result:** ~250 lines extracted. Largest single improvement. `app.py` no longer imports `sbc` or `ctypes`.

### Phase 5: Extract platform UI config

Create `platform_ui.py` with the `PlatformUIConfig` dataclass and a `get_ui_config()` factory. Update `app.py` and `overlay.py` to read window flags, positioning, event filter behavior, tray menu style, and min brightness from the config instead of branching on `platform.system()`.

**Result:** All remaining `platform.system()` calls removed from `app.py` and `overlay.py`. Platform behavior is declared in one place.

### Phase 6: Final cleanup

Review `app.py` for any remaining platform checks. Update imports, docstrings, and module-level comments. Verify `pyinstaller flexlux.spec` still produces working builds. Update `REFACTOR_PLAN.md` to mark completion or remove it.

**Result:** Refactor complete. Clean package structure with platform concerns fully isolated.
