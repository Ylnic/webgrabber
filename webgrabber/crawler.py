from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

DEFAULT_HEADERS = {
    "User-Agent": "WebGrabber/0.1 (+local-macOS-tool)"
}


def decode_html_response(response: requests.Response) -> str:
    return response.content.decode("utf-8", errors="replace")


FILE_TYPE_MAP = {
    "pdf": "pdf",
    "doc": "doc",
    "docx": "docx",
    "xls": "xls",
    "xlsx": "xlsx",
    "ppt": "ppt",
    "pptx": "pptx",
    "zip": "zip",
    "rar": "rar",
    "tar": "tar",
    "gz": "gz",
    "mp4": "mp4",
    "mov": "mov",
    "avi": "avi",
    "mkv": "mkv",
    "mp3": "mp3",
    "wav": "wav",
    "flac": "flac",
    "jpg": "jpg",
    "jpeg": "jpeg",
    "png": "png",
    "gif": "gif",
    "webp": "webp",
    "svg": "svg",
    "bmp": "bmp",
    "txt": "txt",
    "csv": "csv",
    "json": "json",
    "xml": "xml",
    "html": "html",
    "htm": "htm",
    "css": "css",
    "js": "js",
}


@dataclass
class CrawlSettings:
    max_pages: int | None = 200
    max_depth: int = 3
    max_file_size_mb: int | None = 50
    max_download_bytes: int | None = None
    include_files: bool = True
    include_text: bool = True
    allowed_file_extensions: tuple[str, ...] = (
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "zip",
        "rar",
        "tar",
        "gz",
        "jpg",
        "jpeg",
        "png",
        "gif",
        "webp",
        "svg",
        "mp4",
        "mov",
        "avi",
        "mkv",
        "mp3",
        "wav",
        "flac",
        "txt",
        "csv",
        "json",
        "xml",
    )


@dataclass
class CrawlResult:
    start_url: str
    base_domain: str
    pages: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)
    external_domains: set[str] = field(default_factory=set)
    external_pages: set[str] = field(default_factory=set)
    skipped_external_domains: set[str] = field(default_factory=set)
    file_types: Counter[str] = field(default_factory=Counter)
    estimated_bytes: int = 0
    status: str = "ready"


def normalize_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        raise ValueError("URL cannot be empty")
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if not parsed.scheme:
        parsed = parsed._replace(scheme="https")
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if ":" in netloc and netloc.rsplit(":", 1)[1].isdigit() and scheme in {"http", "https"}:
        host = netloc.rsplit(":", 1)[0]
        port = netloc.rsplit(":", 1)[1]
        if port in {"80", "443"}:
            netloc = host
    normalized = parsed._replace(scheme=scheme, netloc=netloc, fragment="")
    return urlunsplit(normalized)


def extract_base_domain(url: str) -> str:
    value = normalize_url(url)
    parsed = urlsplit(value)
    host = parsed.netloc or parsed.path
    if not host:
        return ""
    host = host.split(":", 1)[0].lower()
    return host.removeprefix("www.")


def domain_folder_name(url: str) -> str:
    domain = extract_base_domain(url)
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    name = re.sub(r"[^A-Za-z0-9.-]+", "_", domain).strip(".-")
    return name or "website"


def detect_file_type(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.lower()
    if not path:
        return "unknown"
    name = path.rsplit("/", 1)[-1]
    candidate = name.split("?", 1)[0].split("#", 1)[0]
    ext = os.path.splitext(candidate)[1].lstrip(".").lower()
    return FILE_TYPE_MAP.get(ext, "unknown")


def should_visit(candidate: str, base_url: str, current_url: str | None = None) -> bool:
    if not candidate:
        return False
    try:
        candidate_url = normalize_url(candidate)
    except ValueError:
        return False
    if current_url and normalize_url(current_url) == candidate_url:
        return False
    parsed = urlsplit(candidate_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    base_host = extract_base_domain(base_url)
    candidate_host = parsed.netloc.split(":", 1)[0].lower().removeprefix("www.")
    if candidate_host != base_host:
        return False
    return True


class SiteCrawler:
    def __init__(
        self,
        start_url: str,
        output_dir: str,
        settings: CrawlSettings | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        additional_start_urls: Iterable[str] = (),
    ):
        self.start_url = normalize_url(start_url)
        self.base_domain = extract_base_domain(self.start_url)
        self.output_dir = Path(output_dir)
        self.settings = settings or CrawlSettings()
        self.progress_callback = progress_callback
        self.additional_start_urls = tuple(additional_start_urls)
        self.pause_event = False
        self.cancel_event = False
        self._status = "idle"
        self._log_lock = threading.Lock()
        self.result = CrawlResult(start_url=self.start_url, base_domain=self.base_domain)

    def log_event(self, message: str) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            with self._log_lock:
                with (self.output_dir / "webgrabber.log").open("a", encoding="utf-8") as log_file:
                    log_file.write(f"{timestamp} {message}\n")
        except OSError:
            pass

    @property
    def status(self) -> str:
        return self._status

    def pause(self) -> None:
        self.pause_event = True
        self.log_event("Crawl pausad av användaren")

    def resume(self) -> None:
        self.pause_event = False
        self.log_event("Crawl återupptagen av användaren")

    def cancel(self) -> None:
        self.cancel_event = True
        self.log_event("Crawl avbruten av användaren")

    def _is_allowed_by_robots(self, url: str) -> bool:
        robots_url = urljoin(urlsplit(url)._replace(path="/", query="", fragment="").geturl(), "/robots.txt")
        self.log_event(f"ROBOTS start url={robots_url}")
        try:
            response = requests.get(robots_url, timeout=(5, 10), headers=DEFAULT_HEADERS)
            self.log_event(f"ROBOTS svar status={response.status_code} url={robots_url}")
            if response.status_code >= 400:
                return True
            robots = RobotFileParser()
            robots.parse(response.text.splitlines())
            allowed = robots.can_fetch("WebGrabber", url)
            self.log_event(f"ROBOTS {'tillåten' if allowed else 'blockerad'} url={url}")
            return allowed
        except requests.RequestException as exc:
            self.log_event(f"ROBOTS nätverksfel url={robots_url} fel={exc!r}; fortsätter")
            return True
        except Exception as exc:
            self.log_event(f"ROBOTS fel url={robots_url} fel={exc!r}; fortsätter")
            return True

    def _extract_links(self, html: str, page_url: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            try:
                absolute = urljoin(page_url, href)
                links.append(normalize_url(absolute))
            except ValueError:
                continue
        for tag in soup.find_all({"img", "script", "link", "video", "audio", "source"}):
            for attribute in ("src", "href", "data-src"):
                value = tag.get(attribute)
                if not value:
                    continue
                try:
                    links.append(normalize_url(urljoin(page_url, value)))
                except ValueError:
                    continue
        return links

    def _extract_external_page_links(self, html: str, page_url: str) -> set[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            try:
                link = normalize_url(urljoin(page_url, anchor["href"]))
            except ValueError:
                continue
            if urlsplit(link).scheme not in {"http", "https"}:
                continue
            if not should_visit(link, self.start_url):
                host = extract_base_domain(link)
                if host and host != self.base_domain:
                    links.add(link)
        return links

    def _store_page(self, url: str, html: str) -> None:
        parsed = urlsplit(url)
        relative_path = parsed.path.strip("/") or "index.html"
        if not relative_path.endswith(".html"):
            relative_path = f"{relative_path}.html" if relative_path != "index.html" else "index.html"
        destination = self.output_dir / "pages" / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(html, encoding="utf-8", errors="replace")

    def _store_file(self, url: str, content: bytes) -> str:
        parsed = urlsplit(url)
        relative_path = parsed.path.strip("/") or "download"
        name = relative_path.rsplit("/", 1)[-1] or "download"
        if not os.path.splitext(name)[1]:
            ext = detect_file_type(url)
            if ext != "unknown":
                name = f"{name}.{ext}"
                relative_path = f"{relative_path}.{ext}"
        destination = self.output_dir / "files" / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return str(destination)

    def _read_response_content(
        self,
        response: requests.Response,
        remaining_bytes: int | None,
    ) -> bytes | None:
        if remaining_bytes is not None:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > remaining_bytes:
                        self.log_event(
                            f"SKIP body för stor content_length={content_length} kvar={remaining_bytes}"
                        )
                        close = getattr(response, "close", None)
                        if close is not None:
                            close()
                        return None
                except ValueError:
                    pass

        content = bytearray()
        last_logged_bytes = 0
        iter_content = getattr(response, "iter_content", None)
        chunks = iter_content(chunk_size=64 * 1024) if iter_content else (response.content,)
        for chunk in chunks:
            if not chunk:
                continue
            if remaining_bytes is not None and len(content) + len(chunk) > remaining_bytes:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
                return None
            content.extend(chunk)
            if len(content) - last_logged_bytes >= 1024 * 1024:
                self.log_event(f"BODY läst_bytes={len(content)}")
                last_logged_bytes = len(content)
        return bytes(content)

    def crawl(self) -> CrawlResult:
        self._status = "running"
        self.cancel_event = False
        self.pause_event = False
        seeds = dict.fromkeys((self.start_url, *self.additional_start_urls))
        queue = deque((normalize_url(url), 0) for url in seeds)
        discovered_pages: set[str] = set()
        discovered_files: set[str] = set()
        external_domains: set[str] = set()
        external_pages: set[str] = set()
        file_types: Counter[str] = Counter()
        pending_pages = 0
        max_bytes = self.settings.max_download_bytes
        if max_bytes is None and self.settings.max_file_size_mb is not None:
            max_bytes = self.settings.max_file_size_mb * 1024 * 1024
        self.log_event(
            f"CRAWL start url={self.start_url} max_pages={self.settings.max_pages} "
            f"max_depth={self.settings.max_depth} max_bytes={max_bytes}"
        )

        while queue and not self.cancel_event:
            if self.settings.max_pages is not None and pending_pages >= self.settings.max_pages:
                break
            while self.pause_event:
                if self.cancel_event:
                    return self.result
                import time
                time.sleep(0.2)

            url, depth = queue.popleft()
            normalized = normalize_url(url)
            self.log_event(f"QUEUE url={normalized} depth={depth} kvar_i_kö={len(queue)}")
            if self.progress_callback is not None:
                self.progress_callback(pending_pages, len(queue), normalized)
            if normalized in discovered_pages:
                self.log_event(f"SKIP redan besökt url={normalized}")
                continue
            if depth > self.settings.max_depth:
                self.log_event(f"SKIP djup url={normalized} depth={depth}")
                continue
            if not self._is_allowed_by_robots(normalized):
                continue

            try:
                self.log_event(f"GET start url={normalized}")
                response = requests.get(normalized, timeout=(5, 15), headers=DEFAULT_HEADERS, stream=True)
                if response.status_code >= 400:
                    self.log_event(f"GET fel status={response.status_code} url={normalized}")
                    close = getattr(response, "close", None)
                    if close is not None:
                        close()
                    continue
                self.log_event(
                    f"GET svar status={response.status_code} content_type={response.headers.get('Content-Type', '')} "
                    f"content_length={response.headers.get('Content-Length', '?')} url={normalized}"
                )
            except requests.RequestException as exc:
                self.log_event(f"GET nätverksfel url={normalized} fel={exc!r}")
                continue

            is_html = response.headers.get("Content-Type", "").startswith("text/html") or normalized.endswith((".html", ".htm", "/"))
            detected_type = detect_file_type(normalized)
            should_store_file = self.settings.include_files and detected_type != "unknown"
            if not is_html and not should_store_file:
                self.log_event(f"SKIP filtyp url={normalized}")
                close = getattr(response, "close", None)
                if close is not None:
                    close()
                continue

            total_limit = self.settings.max_download_bytes
            if total_limit is None and self.settings.max_file_size_mb is not None:
                total_limit = self.settings.max_file_size_mb * 1024 * 1024
            remaining_bytes = (
                None
                if total_limit is None
                else max(0, total_limit - self.result.estimated_bytes)
            )
            try:
                content = self._read_response_content(response, remaining_bytes)
            except requests.RequestException as exc:
                self.log_event(f"BODY nätverksfel url={normalized} fel={exc!r}")
                continue
            if content is None:
                continue
            self.result.estimated_bytes += len(content)
            self.log_event(f"BODY klar bytes={len(content)} totalt={self.result.estimated_bytes} url={normalized}")

            if is_html:
                if not should_visit(normalized, self.start_url, next(iter(discovered_pages), None)):
                    self.log_event(f"SKIP dubblett/extern HTML url={normalized}")
                    continue
                discovered_pages.add(normalized)
                self.result.pages = discovered_pages
                pending_pages += 1
                if self.progress_callback is not None:
                    self.progress_callback(pending_pages, len(queue), "")
                self.result.status = f"crawling: {pending_pages}"
                html = content.decode("utf-8", errors="replace")
                try:
                    self._store_page(normalized, html)
                    self.log_event(f"PAGE sparad sida={pending_pages} url={normalized}")
                except Exception as exc:
                    self.log_event(f"PAGE skrivfel url={normalized} fel={exc!r}")

                for link in self._extract_links(html, normalized):
                    if should_visit(link, self.start_url, normalized):
                        if link not in discovered_pages and link not in queue:
                            queue.append((link, depth + 1))
                    else:
                        candidate_host = urlsplit(link).netloc.split(":", 1)[0].lower().removeprefix("www.")
                        if candidate_host and candidate_host != self.base_domain:
                            external_domains.add(candidate_host)
                external_pages.update(self._extract_external_page_links(html, normalized))
                self.result.external_pages = external_pages
                self.result.external_domains = external_domains
                self.log_event(
                    f"PAGE länkar intern_kö={len(queue)} externa_url:er={len(external_pages)}"
                )
                if self.progress_callback is not None:
                    self.progress_callback(pending_pages, len(queue), "")
                continue

            if should_store_file:
                if normalized not in discovered_files:
                    discovered_files.add(normalized)
                    file_types[detected_type] += 1
                    self.result.files = discovered_files
                    self.result.file_types = file_types
                    self.result.external_domains = external_domains
                self._store_file(normalized, content)
                self.log_event(f"FILE sparad fil={normalized} bytes={len(content)}")

        if self.progress_callback is not None:
            self.progress_callback(pending_pages, len(queue), "")
        self.result.pages = discovered_pages
        self.result.files = discovered_files
        self.result.file_types = file_types
        self.result.external_domains = external_domains
        self.result.status = "complete" if not self.cancel_event else "cancelled"
        self._status = self.result.status
        self.log_event(
            f"CRAWL slut status={self.result.status} sidor={len(discovered_pages)} "
            f"filer={len(discovered_files)} bytes={self.result.estimated_bytes} kvar_i_kö={len(queue)}"
        )
        return self.result

    def crawl_external_domains(self) -> None:
        self._status = "running"
        remaining_pages = (
            None
            if self.settings.max_pages is None
            else max(0, self.settings.max_pages - len(self.result.pages))
        )
        total_limit = self.settings.max_download_bytes
        if total_limit is None and self.settings.max_file_size_mb is not None:
            total_limit = self.settings.max_file_size_mb * 1024 * 1024
        remaining_bytes = (
            None
            if total_limit is None
            else max(0, total_limit - self.result.estimated_bytes)
        )
        external_domains = sorted({extract_base_domain(url) for url in self.result.external_pages})
        self.log_event(
            f"EXTERNAL start domains={','.join(external_domains)} "
            f"remaining_pages={remaining_pages} remaining_bytes={remaining_bytes}"
        )
        for index, domain in enumerate(external_domains):
            if self.cancel_event:
                break
            if remaining_pages is not None and remaining_pages <= 0:
                self.result.skipped_external_domains.update(external_domains[index:])
                self.log_event(f"EXTERNAL skip domains={','.join(external_domains[index:])} reason=max_pages")
                break
            if remaining_bytes is not None and remaining_bytes <= 0:
                self.result.skipped_external_domains.update(external_domains[index:])
                self.log_event(f"EXTERNAL skip domains={','.join(external_domains[index:])} reason=max_bytes")
                break
            seeds = sorted(
                url for url in self.result.external_pages
                if extract_base_domain(url) == domain
            )
            if not seeds:
                continue

            domain_output_dir = self.output_dir / domain_folder_name(domain)
            domain_output_dir.mkdir(parents=True, exist_ok=True)
            self.log_event(
                f"EXTERNAL domain_start domain={domain} seeds={len(seeds)} output={domain_output_dir}"
            )

            progress_offset = len(self.result.pages)
            child = SiteCrawler(
                seeds[0],
                str(domain_output_dir),
                replace(
                    self.settings,
                    max_pages=remaining_pages,
                    max_file_size_mb=None,
                    max_download_bytes=remaining_bytes,
                ),
                progress_callback=(
                    lambda count, queued, current_url, offset=progress_offset: self.progress_callback(
                        offset + count, queued, current_url
                    )
                    if self.progress_callback is not None
                    else None
                ),
                additional_start_urls=seeds[1:],
            )
            child.crawl()
            self.result.pages.update(child.result.pages)
            self.result.files.update(child.result.files)
            self.result.file_types.update(child.result.file_types)
            self.result.estimated_bytes += child.result.estimated_bytes
            if remaining_pages is not None:
                remaining_pages -= len(child.result.pages)
            if remaining_bytes is not None:
                remaining_bytes -= child.result.estimated_bytes
            self.log_event(
                f"EXTERNAL domain_done domain={domain} pages={len(child.result.pages)} "
                f"files={len(child.result.files)} bytes={child.result.estimated_bytes}"
            )

        self.result.status = "complete" if not self.cancel_event else "cancelled"
        self._status = self.result.status
        self.log_event(
            f"EXTERNAL slut status={self.result.status} pages={len(self.result.pages)} "
            f"skipped={','.join(sorted(self.result.skipped_external_domains)) or 'none'}"
        )


def build_index(result: CrawlResult, output_dir: str) -> dict:
    index = {
        "start_url": result.start_url,
        "base_domain": result.base_domain,
        "page_count": len(result.pages),
        "file_count": len(result.files),
        "file_types": dict(result.file_types),
        "external_domains": sorted(result.external_domains),
        "external_pages": sorted(result.external_pages),
        "skipped_external_domains": sorted(result.skipped_external_domains),
        "estimated_bytes": result.estimated_bytes,
        "pages": sorted(result.pages),
        "files": sorted(result.files),
        "status": result.status,
    }
    output_path = Path(output_dir) / "index.json"
    output_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index
