# How to Create a GitHub Release for FlexLux

## Prerequisites
- GitHub repository with push access
- Either: Access to platforms you want to build for OR GitHub Actions setup

## Two Ways to Create Releases

### Option 1: Manual Building (Requires Access to Each Platform)

#### 1. Compile the Executables

On each platform, run:
```bash
pyinstaller flexlux.spec
```

This creates:
- **Windows**: `dist/flexlux.exe`
- **Linux**: `dist/flexlux` (no extension)
- **macOS**: `dist/flexlux.app` (actually a folder)

**Note**: You must have access to each platform to build its executable. You cannot build a Windows .exe on Linux or a macOS .app on Windows.

#### 2. Prepare Release Files

Rename files for clarity:
- Windows: `flexlux-windows.exe`
- Linux: `flexlux-linux`
- macOS: Create a zip file: `flexlux-macos.zip` (since .app is a folder)

For macOS:
```bash
cd dist
zip -r flexlux-macos.zip flexlux.app
```

#### 3. Create GitHub Release

1. Go to your repository on GitHub
2. Click on "Releases" (right side of the page)
3. Click "Create a new release" or "Draft a new release"
4. Fill in the details:
   - **Choose a tag**: Create a new tag like `v1.0.0`
   - **Release title**: e.g., "FlexLux v1.0.0"
   - **Description**: Add release notes (see example below)
   - **Attach binaries**: Upload the executables you built
5. Choose "Publish release"

### Option 2: Automated Building with GitHub Actions (Recommended)

If you don't have access to all platforms, use GitHub Actions to automatically build on all platforms.

#### Setup (Already Done!)

The workflow file `.github/workflows/build-release.yml` has already been created in this repository. It will automatically trigger when you push a version tag.

#### How to Create a Release

Simply push a version tag:
```bash
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions will automatically:
- Build executables on Windows, Linux, and macOS
- Create a release with all three executables attached
- Add release notes with download instructions

#### What Happens Behind the Scenes

1. The workflow detects the version tag push
2. It runs three parallel builds (Windows, Linux, macOS)
3. Each build:
   - Sets up Python 3.11
   - Installs dependencies
   - Runs PyInstaller
   - Renames/packages the output appropriately
4. Creates a GitHub release with all artifacts attached

## Version Numbering

Use semantic versioning: `vMAJOR.MINOR.PATCH`
- **MAJOR**: Breaking changes
- **MINOR**: New features, backwards compatible
- **PATCH**: Bug fixes

## Example Release Description

```markdown
## FlexLux v1.0.0

Cross-platform brightness control application with system tray interface.

### What's New
- Added cross-platform support (Windows, Linux, macOS)
- Improved error handling
- Enhanced documentation

### Features
- 🖥️ Windows, Linux, and macOS support
- 🌓 Hardware and software brightness control
- 🎯 Simple system tray interface
- 🌑 Artificial darkening below hardware limits

### Downloads
- **Windows**: Download `flexlux-windows.exe`
- **Linux**: Download `flexlux-linux` (make executable with `chmod +x`)
- **macOS**: Download and extract `flexlux-macos.zip`

### Requirements
- Windows: Windows 10 or later
- Linux: X11 display server
- macOS: macOS 10.15 or later (may require security permissions)

### Installation
1. Download the appropriate file for your platform
2. Windows/macOS: Simply run the executable
3. Linux: Make executable first: `chmod +x flexlux-linux`