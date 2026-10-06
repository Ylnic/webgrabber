#!/usr/local/bin/python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR / "app"
sys.path.insert(0, str(APP_DIR / "vendor"))
sys.path.insert(0, str(APP_DIR))

from webgrabber.loopia import handle_request


def main() -> None:
    content_length = os.environ.get("CONTENT_LENGTH", "0")
    try:
        length = min(max(int(content_length), 0), 8192)
    except ValueError:
        length = 0
    body = sys.stdin.buffer.read(length) if length else b""
    storage_root = Path(os.environ.get("WEBGRABBER_STORAGE", str(ROOT_DIR / "private" / "jobs")))
    status, headers, payload = handle_request(
        os.environ.get("REQUEST_METHOD", "GET").upper(),
        os.environ.get("PATH_INFO", "/") or "/",
        os.environ.get("QUERY_STRING", ""),
        os.environ.get("CONTENT_TYPE", ""),
        content_length,
        body,
        storage_root,
        os.environ.get("REMOTE_ADDR", "unknown"),
    )
    reason = {
        200: "OK",
        400: "Bad Request",
        405: "Method Not Allowed",
        404: "Not Found",
        413: "Payload Too Large",
        429: "Too Many Requests",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }.get(status, "Error")
    sys.stdout.write(f"Status: {status} {reason}\r\n")
    for key, value in headers.items():
        sys.stdout.write(f"{key}: {value}\r\n")
    sys.stdout.write(f"Content-Length: {len(payload)}\r\n\r\n")
    sys.stdout.flush()
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()