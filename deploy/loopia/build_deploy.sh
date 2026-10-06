#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
BUILD_DIR="$SCRIPT_DIR/dist"
PYTHON_BIN=${PYTHON_BIN:-python3}
mkdir -p "$BUILD_DIR"
STAGE_DIR=$(mktemp -d "$BUILD_DIR/stage.XXXXXX")

cleanup() {
    rm -rf "$STAGE_DIR"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$STAGE_DIR/public_html" "$STAGE_DIR/app/webgrabber" \
    "$STAGE_DIR/app/vendor" "$STAGE_DIR/private"

"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required")'
"$PYTHON_BIN" -m pip install --disable-pip-version-check \
    --platform manylinux2014_x86_64 --python-version 3.11 --implementation cp --abi cp311 \
    --only-binary=:all: --no-compile --target "$STAGE_DIR/app/vendor" \
    -r "$SCRIPT_DIR/requirements.txt"
find "$STAGE_DIR/app/vendor" -type d -name __pycache__ -prune -exec rm -rf {} +

cp "$PROJECT_ROOT/webgrabber/__init__.py" "$STAGE_DIR/app/webgrabber/"
cp "$PROJECT_ROOT/webgrabber/crawler.py" "$STAGE_DIR/app/webgrabber/"
cp "$PROJECT_ROOT/webgrabber/storage.py" "$STAGE_DIR/app/webgrabber/"
cp "$PROJECT_ROOT/webgrabber/safe_http.py" "$STAGE_DIR/app/webgrabber/"
cp "$PROJECT_ROOT/webgrabber/loopia.py" "$STAGE_DIR/app/webgrabber/"
cp "$SCRIPT_DIR/public_html/index.py" "$STAGE_DIR/public_html/"
cp "$SCRIPT_DIR/public_html/.htaccess" "$STAGE_DIR/public_html/"
cp "$SCRIPT_DIR/private/.htaccess" "$STAGE_DIR/private/"
cp "$SCRIPT_DIR/README.md" "$STAGE_DIR/README-Loopia.txt"
chmod 755 "$STAGE_DIR/public_html/index.py"
chmod 700 "$STAGE_DIR/private"

rm -f "$BUILD_DIR/webgrabber-loopia.zip"
cd "$STAGE_DIR"
zip -X -qr "$BUILD_DIR/webgrabber-loopia.zip" .
printf 'Created deployment package: %s\n' "$BUILD_DIR/webgrabber-loopia.zip"