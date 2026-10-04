#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

show_error() {
  print -u2 -- "$1"
  if [[ -t 0 ]]; then
    read -r "?Try Enter to close..."
  fi
  exit 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  show_error "This installer builder must run on macOS."
fi

if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  show_error "Install Python 3 with Tk support from python.org, then run this file again."
fi

VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  python3 -m venv "$VENV_DIR" || show_error "Could not create .venv."
elif ! "$PYTHON_BIN" -c 'import tkinter' >/dev/null 2>&1; then
  show_error ".venv exists but has no working Tk support. It was left untouched."
fi

export PYTHONUTF8=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
"$PYTHON_BIN" -m pip install -r "$SCRIPT_DIR/requirements.txt" -r "$SCRIPT_DIR/requirements-build.txt" || {
  show_error "Could not install the build requirements. Check your network connection."
}

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/webgrabber-build.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
APP_OUTPUT="$WORK_DIR/dist"
mkdir -p "$APP_OUTPUT"

echo "Building WebGrabber.app..."
"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name WebGrabber \
  --osx-bundle-identifier org.webgrabber.app \
  --icon "$SCRIPT_DIR/WebGrabber.app/Contents/Resources/applet.icns" \
  --distpath "$APP_OUTPUT" \
  --workpath "$WORK_DIR/work" \
  --specpath "$WORK_DIR/spec" \
  --collect-all reportlab \
  "$SCRIPT_DIR/main.py" || show_error "PyInstaller could not build the macOS app."

STAGE_DIR="$WORK_DIR/stage"
mkdir -p "$STAGE_DIR"
/usr/bin/ditto "$APP_OUTPUT/WebGrabber.app" "$STAGE_DIR/WebGrabber.app"
/bin/ln -s /Applications "$STAGE_DIR/Applications"

INSTALLER_DIR="$SCRIPT_DIR/dist"
mkdir -p "$INSTALLER_DIR"
INSTALLER_PATH="$INSTALLER_DIR/WebGrabber-macOS-$(date +%Y%m%d-%H%M%S).dmg"

echo "Creating drag-and-drop DMG..."
/usr/bin/hdiutil create \
  -volname "WebGrabber" \
  -srcfolder "$STAGE_DIR" \
  -format UDZO \
  "$INSTALLER_PATH" || show_error "Could not create the DMG installer."

echo "Installer created: $INSTALLER_PATH"
echo "Open the DMG and drag WebGrabber.app to Applications."