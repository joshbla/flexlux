# FlexLux

Adjust your monitor brightness through a simple taskbar app. Reach the maximum brightness for your monitor or dim your monitor below its natural settings by artificially darkening the screen.

Compatible with Windows, Linux, and macOS.

## Features

- 🌓 Adjust screen brightness beyond hardware limits
- 🎯 Simple slider interface accessible from system tray
- 🖥️ Cross-platform support (Windows, Linux, macOS)
- 🌑 Artificial darkening for ultra-low brightness
- ⚡ Lightweight and minimal resource usage

## How to Run FlexLux

There are two ways to run FlexLux:

### Option 1: Run from Source (Requires Python)

This method runs the Python script directly. Good for development or if you have Python installed.

#### Quick Start with Launcher Scripts

```bash
# Linux/macOS
./flexlux.sh

# Windows
flexlux.bat
```

These scripts automatically create a virtual environment, install dependencies, and launch the application.

#### Manual Installation

1. Clone the repository:
```bash
git clone https://github.com/joshbla/flexlux.git
cd flexlux
```

2. Create a virtual environment:
```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux/macOS
python3 -m venv venv
source venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
# or manually: pip install PyQt5 Pillow screen_brightness_control
```

4. Run the application:
```bash
python -m flexlux
```

### Option 2: Run as Compiled Executable (No Python Required)

This method uses a standalone executable that includes Python and all dependencies. Best for end users.

#### Using Pre-built Executable

If you have a pre-built executable:
- **Windows**: Double-click `flexlux.exe` in the `dist/` folder
- **macOS**: Double-click `flexlux.app` in the `dist/` folder
- **Linux**: Run `./dist/flexlux` from terminal or double-click if your file manager supports it

#### Building Your Own Executable

**Important:** PyInstaller creates platform-specific executables. You must build on each platform to get that platform's native format:

- **Windows**: Creates `flexlux.exe`
- **macOS**: Creates `flexlux.app` bundle
- **Linux**: Creates `flexlux` executable (no extension)

To build:

```bash
# First, install PyInstaller (if running from source)
pip install pyinstaller

# Build using the spec file (recommended)
pyinstaller flexlux.spec

# Or build manually:
# Windows
pyinstaller --onefile --windowed --icon=assets/icon.png --add-data="assets/icon.png;assets/" flexlux/__main__.py

# Linux/macOS  
pyinstaller --onefile --windowed --icon=assets/icon.png --add-data="assets/icon.png:assets/" flexlux/__main__.py
```

The executable will be created in the `dist/` directory.

## Prerequisites

- **For running from source**: Python 3.9 or higher (3.10+ recommended)
- **For compiled executable**: No prerequisites, everything is bundled

## Usage

1. Launch FlexLux - it will appear in your system tray
2. Click the tray icon to show/hide the brightness slider
3. Move the slider:
   - **Right** (100-200): Increase hardware brightness
   - **Center** (100): Minimum hardware brightness
   - **Left** (0-100): Apply artificial darkening overlay

Right-click the tray icon for additional options.

## Platform Notes

- **Windows**: Full functionality supported
- **Linux**: Requires X11 window system. Wayland support may be limited
- **macOS**: Artificial darkening works out of the box. Hardware brightness control for the built-in display uses the native DisplayServices framework. For **external monitors**, install [`m1ddc`](https://github.com/waydabber/m1ddc) to enable hardware brightness via DDC/CI:
  ```bash
  brew install m1ddc
  ```
  This is optional — if `m1ddc` is not installed, or if a display doesn't support DDC/CI (common with TVs), the hardware brightness side of the slider will be grayed out and the artificial darkening overlay still works normally.

## Troubleshooting

If brightness control doesn't work:
- Ensure your display drivers are up to date
- On Linux, you may need to run with elevated privileges: `sudo python -m flexlux`
- On macOS, if the hardware brightness slider is grayed out for an external monitor, install `m1ddc` (`brew install m1ddc`) and restart FlexLux. If it's still grayed out, your display likely doesn't support DDC/CI
- Check that `screen_brightness_control` supports your hardware

### Hardware brightness not working through a KVM switch or USB-C hub

Hardware brightness uses DDC/CI, which travels over the video cable itself. Many KVM switches and budget USB-C hubs/docks pass the monitor's identity (EDID) but drop DDC/CI traffic, so the display still appears by name while brightness commands fail. The symptom is that the hardware side of the slider is unavailable and only the artificial darkening overlay works.

- On macOS, you can confirm this with `m1ddc`: run `m1ddc display list` to find the display index, then `m1ddc get luminance -d <index>`. A "DDC communication failure" error while the monitor is listed means something in the video path is blocking DDC/CI.
- Connecting the monitor's USB upstream port does not help. It only enables the monitor's built-in USB ports, vendor lighting software, and firmware updates — brightness control does not travel over USB.
- Fixes: connect the video cable directly to the computer, use a Thunderbolt dock (which tunnels native DisplayPort, including DDC/CI), or use a KVM/hub that explicitly supports DDC/CI passthrough.

Confirmed with an LG 27GN950: DDC/CI fails through both a KVM switch and a GenesysLogic-based USB-C hub, on the same setup where the monitor is otherwise detected correctly.

## License

This project is licensed under the terms in the License.md file.
