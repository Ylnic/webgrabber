from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

class CrawlStorage(Protocol):
    def log_event(self, message: str) -> None: ...

    def store_page(self, url: str, html: str) -> None: ...

    def store_file(self, url: str, content: bytes) -> str: ...

    def child(self, name: str) -> CrawlStorage: ...


class DirectoryStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._log_lock = threading.Lock()

    def log_event(self, message: str) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            with self._log_lock:
                with (self.root / "webgrabber.log").open("a", encoding="utf-8") as log_file:
                    log_file.write(f"{timestamp} {message}\n")
        except OSError:
            pass

    @staticmethod
    def _safe_segments(path: str) -> list[str]:
        return [segment for segment in path.split("/") if segment not in {"", ".", ".."}]

    def store_page(self, url: str, html: str) -> None:
        parsed = urlsplit(url)
        segments = self._safe_segments(parsed.path)
        relative_path = "/".join(segments) or "index.html"
        if not relative_path.endswith(".html"):
            relative_path = f"{relative_path}.html" if relative_path != "index.html" else "index.html"
        destination = self.root / "pages" / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(html, encoding="utf-8", errors="replace")

    def store_file(self, url: str, content: bytes) -> str:
        parsed = urlsplit(url)
        segments = self._safe_segments(parsed.path)
        relative_path = "/".join(segments) or "download"
        destination = self.root / "files" / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return str(destination)

    def write_json(self, name: str, value: dict) -> None:
        destination = self.root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(value, indent=2), encoding="utf-8")

    def child(self, name: str) -> DirectoryStorage:
        safe_name = "".join(char for char in name if char.isalnum() or char in ".-_ ")
        return DirectoryStorage(self.root / (safe_name or "website"))