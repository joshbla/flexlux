# TODO

## Verification

- Verify background brightness I/O on real Windows hardware: dragging the slider and leaving the panel open should stay smooth, including with a monitor whose DDC/CI is blocked (KVM/hub). Simulated slow-monitor checks passed on macOS; not yet run on Windows or Linux.

## Bugs

- Windows/Linux: monitors are matched to screens by list order because the sbc backend reports no display bounds, so dimming one monitor's slider can darken a different screen. This positional mapping is also a hidden fallback in the overlay setup.
- Windows/Linux: monitors are marked hardware-capable without probing DDC/CI, so a blocked monitor (KVM/hub) shows a working right half that does nothing. macOS already probes. A failing monitor also keeps the background brightness worker busy with multi-second retries, delaying writes to other monitors.
- Linux: the bottom-anchored panel position ignores the screen's vertical offset, so it can land at the wrong height on a stacked secondary screen. Windows was fixed in v1.7.0.
- macOS: "Launch at Startup" saved the App Translocation path when the app was first run from a quarantined download, so login launch silently failed (launchd exit 78). Write a stable path or refuse to enable autostart while translocated.
- macOS: when launched at login, m1ddc is not found because launchd's PATH excludes `/opt/homebrew/bin`, so external hardware brightness is unavailable until FlexLux is relaunched from a shell. The login-launched app logged no DDC/CI probe at all.
- macOS: the brightness-key interceptor logs to `flexlux.brightness.mac_keys` instead of the `FlexLux` logger, so its start/failure messages never reach `FlexLux.log`.

## Investigate

- macOS: running from source (Python 3.14, Qt 5.15.19) crashes with SIGTRAP after the event loop exits, with or without the background-worker change. Determine whether the packaged app also crashes on Quit.
- `logging_setup.py` uses default-value fallbacks for `LOCALAPPDATA` and `XDG_DATA_HOME`; decide whether to keep the XDG spec default and require `LOCALAPPDATA` explicitly.
