from __future__ import annotations

import fcntl
import hashlib
import html
import io
import json
import os
import secrets
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import requests

from webgrabber.crawler import CrawlResult, CrawlSettings, SiteCrawler, build_index, normalize_url
from webgrabber.safe_http import SafeHTTPClient
from webgrabber.storage import DirectoryStorage

MAX_REQUEST_BYTES = 8192
MAX_PAGES = 50
MAX_FILES = 50
MAX_DEPTH = 5
MAX_SINGLE_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_RUNTIME_SECONDS = 12.0
MAX_ZIP_BYTES = 6 * 1024 * 1024
RATE_LIMIT_COUNT = 6
RATE_LIMIT_WINDOW_SECONDS = 60 * 60
STALE_JOB_SECONDS = 60 * 60


class CGIRequestError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _response(status: int, content_type: str, body: bytes, **headers: str) -> tuple[int, dict[str, str], bytes]:
    result_headers = {"Content-Type": content_type, "Cache-Control": "no-store"}
    result_headers.update(headers)
    return status, result_headers, body


def _json_response(status: int, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
    return _response(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))


def _health(storage_root: Path) -> tuple[int, dict[str, str], bytes]:
    try:
        storage_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(storage_root, 0o700)
        with tempfile.NamedTemporaryFile(dir=storage_root, prefix="health-", delete=True):
            pass
    except OSError:
        return _json_response(503, {"status": "error", "storage": "unavailable"})
    return _json_response(200, {"status": "ok", "crawler": "ready", "storage": "writable"})


def _parse_payload(content_type: str, body: bytes) -> dict[str, Any]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CGIRequestError("Begäran måste vara UTF-8.") from exc
    if content_type.split(";", 1)[0].strip().lower() == "application/json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CGIRequestError("Ogiltig JSON.") from exc
        if not isinstance(payload, dict):
            raise CGIRequestError("Begäran måste vara ett JSON-objekt.")
        return payload
    values = parse_qs(text, keep_blank_values=True, max_num_fields=16)
    return {key: value[-1] for key, value in values.items()}


def _integer(payload: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(payload.get(key, default))
    except (TypeError, ValueError) as exc:
        raise CGIRequestError(f"{key} måste vara ett heltal.") from exc
    if not minimum <= value <= maximum:
        raise CGIRequestError(f"{key} måste vara mellan {minimum} och {maximum}.")
    return value


def _acquire_slot(storage_root: Path):
    slot_path = storage_root / ".active.lock"
    descriptor = os.open(slot_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise CGIRequestError("En crawl pågår redan. Försök igen om en stund.", 429)
    return descriptor


def _check_rate_limit(storage_root: Path, remote_addr: str) -> None:
    address_key = hashlib.sha256(remote_addr.encode("utf-8", errors="replace")).hexdigest()
    rate_path = storage_root / f".rate-{address_key}"
    descriptor = os.open(rate_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw = os.read(descriptor, 4096)
        try:
            timestamps = [float(value) for value in raw.decode("ascii").splitlines()]
        except (UnicodeDecodeError, ValueError):
            timestamps = []
        now = time.time()
        timestamps = [stamp for stamp in timestamps if now - stamp < RATE_LIMIT_WINDOW_SECONDS]
        if len(timestamps) >= RATE_LIMIT_COUNT:
            raise CGIRequestError("För många jobb från din anslutning. Försök igen senare.", 429)
        timestamps.append(now)
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, "\n".join(str(stamp) for stamp in timestamps).encode("ascii"))
    finally:
        os.close(descriptor)


def _cleanup_stale_jobs(storage_root: Path) -> None:
    cutoff = time.time() - STALE_JOB_SECONDS
    for path in storage_root.glob("job-*"):
        try:
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _render_form(message: str = "") -> bytes:
    safe_message = html.escape(message)
    message_html = f'<p class="message">{safe_message}</p>' if message else ""
    page = f"""<!doctype html>
<html lang="sv"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WebGrabber</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f6f8;color:#17252e;font:16px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:680px;margin:7vh auto;padding:24px}}section{{background:#fff;border:1px solid #d6e0e5;border-radius:8px;padding:28px;box-shadow:0 14px 36px #142a3612}}
h1{{font-size:30px;margin:0 0 8px}}p{{color:#526570;line-height:1.5}}label{{display:block;font-weight:650;margin:18px 0 7px}}
input,select,button{{width:100%;font:inherit;border:1px solid #c6d3da;border-radius:5px;padding:11px 12px}}button{{margin-top:22px;background:#146f67;color:white;border:0;font-weight:700;cursor:pointer}}button:hover{{background:#105951}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.message{{padding:10px;background:#fff2dd;border-left:3px solid #ba6c00;color:#533400}}
@media(max-width:600px){{main{{margin:0 auto;padding:12px}}section{{padding:20px}}.grid{{grid-template-columns:1fr;gap:0}}}}
</style></head><body><main><section><h1>WebGrabber</h1>
<p>Hämta ett begränsat urval offentligt tillgängliga sidor och filer som en ZIP-fil.</p>{message_html}
<form method="post" action="/">
<input type="hidden" name="action" value="analyze">
<label for="start_url">Webbplats</label><input id="start_url" name="start_url" type="url" placeholder="https://example.se" maxlength="2048" required>
<p>Webbplatsen analyseras först (upp till {MAX_PAGES} sidor och djup {MAX_DEPTH}). Därefter väljer du omfattning och innehåll.</p>
<button type="submit">Analysera webbplats</button></form></section></main></body></html>"""
    return page.encode("utf-8")


def _render_preview(job_id: str, result: CrawlResult) -> bytes:
    default_pages = max(1, min(MAX_PAGES, len(result.pages)))
    default_depth = min(MAX_DEPTH, result.max_depth_reached)
    domain_items = "".join(
        f"<li>{html.escape(domain)}</li>" for domain in sorted(result.external_domains)
    ) or "<li>Inga externa domäner eller subdomäner hittades</li>"
    file_types = ", ".join(
        f"{html.escape(file_type)}: {count}"
        for file_type, count in sorted(result.file_types.items())
    ) or "Inga filer hittades"
    limit_note = ""
    if result.limit_reason:
        limit_note = (
            f'<p class="notice">Analysen är ofullständig: '
            f'{html.escape(result.limit_reason)}.</p>'
        )
    page = f"""<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>WebGrabber – analys</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f6f8;color:#17252e;font:16px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:760px;margin:7vh auto;padding:24px}}section{{background:#fff;border:1px solid #d6e0e5;border-radius:8px;padding:28px;box-shadow:0 14px 36px #142a3612}}
h1{{font-size:28px;margin:0 0 8px}}p{{color:#526570;line-height:1.5}}dl{{display:grid;grid-template-columns:1fr auto;gap:12px;border-top:1px solid #d6e0e5;padding-top:12px}}dt{{color:#526570}}dd{{margin:0;font-weight:700}}ul{{padding-left:22px;line-height:1.7}}
fieldset{{border:0;padding:0;margin:22px 0 0}}legend{{font-weight:700;margin-bottom:10px}}label{{display:flex;gap:10px;align-items:center;padding:10px 0}}input[type=radio]{{width:18px;height:18px;accent-color:#146f67}}
button{{width:100%;padding:12px;border:0;border-radius:5px;background:#146f67;color:white;font:inherit;font-weight:700;cursor:pointer}}.notice{{padding:10px;background:#fff2dd;border-left:3px solid #ba6c00;color:#533400}}
a{{display:inline-block;margin-top:18px;color:#146f67}}@media(max-width:600px){{main{{margin:0 auto;padding:12px}}section{{padding:20px}}}}
</style></head><body><main><section><h1>Analysresultat</h1>
<p>Granska webbplatsen innan du väljer vad som ska hämtas.</p>{limit_note}
<dl><dt>Webbsidor hittade</dt><dd>{len(result.pages)}</dd><dt>Djup som analyserats</dt><dd>{result.max_depth_reached}</dd>
<dt>Interna sidlänkar</dt><dd>{len(result.linked_pages)}</dd><dt>Länkade filer (max {MAX_FILES})</dt><dd>{len(result.linked_files)}</dd>
<dt>Filtyper</dt><dd>{file_types}</dd><dt>Analyserad datamängd</dt><dd>{result.estimated_bytes / (1024 * 1024):.2f} MB</dd>
<dt>Upptäckta domäner/subdomäner</dt><dd>{len(result.external_domains)}</dd>
<dt>Analysstatus</dt><dd>{html.escape(result.status)}</dd></dl>
<h2>Externa domäner och subdomäner</h2><ul>{domain_items}</ul>
<form method="post" action="/"><input type="hidden" name="action" value="download">
<input type="hidden" name="job_id" value="{html.escape(job_id, quote=True)}">
<div class="grid"><div><label for="max_pages">Hämta högst antal sidor (1–{MAX_PAGES})</label>
<input id="max_pages" name="max_pages" type="number" min="1" max="{MAX_PAGES}" value="{default_pages}" required></div>
<div><label for="max_depth">Maxdjup (0–{MAX_DEPTH})</label>
<input id="max_depth" name="max_depth" type="number" min="0" max="{MAX_DEPTH}" value="{default_depth}" required></div></div>
<fieldset><legend>Vad vill du hämta?</legend>
<label><input type="radio" name="mode" value="text" checked>Hämta enbart text</label>
<label><input type="radio" name="mode" value="all">Hämta allt innehåll</label></fieldset>
<button type="submit">Hämta som ZIP</button></form><a href="/">Analysera en annan webbplats</a>
</section></main></body></html>"""
    return page.encode("utf-8")


def _create_archive(job_id: str, result, output_dir: Path) -> bytes:
    manifest = build_index(result)
    manifest["job_id"] = job_id
    DirectoryStorage(output_dir).write_json("index.json", manifest)
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file() or path.name == "webgrabber.log":
                continue
            archive.write(path, path.relative_to(output_dir).as_posix())
        archive.writestr("manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
    payload = archive_buffer.getvalue()
    if len(payload) > MAX_ZIP_BYTES:
        raise CGIRequestError("Resultatet överskred ZIP-gränsen. Minska antal sidor eller filer.", 413)
    return payload


def _serialize_result(result: CrawlResult) -> dict[str, Any]:
    return {
        "start_url": result.start_url,
        "base_domain": result.base_domain,
        "pages": sorted(result.pages),
        "linked_pages": sorted(result.linked_pages),
        "max_depth_reached": result.max_depth_reached,
        "files": sorted(result.files),
        "linked_files": sorted(result.linked_files),
        "external_domains": sorted(result.external_domains),
        "external_pages": sorted(result.external_pages),
        "skipped_external_domains": sorted(result.skipped_external_domains),
        "file_types": dict(result.file_types),
        "estimated_bytes": result.estimated_bytes,
        "status": result.status,
        "limit_reason": result.limit_reason,
    }


def _deserialize_result(value: dict[str, Any]) -> CrawlResult:
    result = CrawlResult(start_url=value["start_url"], base_domain=value["base_domain"])
    result.pages = set(value.get("pages", []))
    result.linked_pages = set(value.get("linked_pages", []))
    result.max_depth_reached = int(value.get("max_depth_reached", 0))
    result.files = set(value.get("files", []))
    result.linked_files = set(value.get("linked_files", []))
    result.external_domains = set(value.get("external_domains", []))
    result.external_pages = set(value.get("external_pages", []))
    result.skipped_external_domains = set(value.get("skipped_external_domains", []))
    result.file_types.update(value.get("file_types", {}))
    result.estimated_bytes = int(value.get("estimated_bytes", 0))
    result.status = str(value.get("status", "complete"))
    result.limit_reason = value.get("limit_reason")
    return result


def _run_analysis(payload: dict[str, Any], storage_root: Path, remote_addr: str) -> tuple[int, dict[str, str], bytes]:
    start_url = str(payload.get("start_url", "")).strip()
    if not start_url or len(start_url) > 2048:
        raise CGIRequestError("Ange en URL på högst 2048 tecken.")
    try:
        start_url = normalize_url(start_url)
    except ValueError as exc:
        raise CGIRequestError("URL:en är ogiltig eller pekar inte på en publik HTTP/HTTPS-adress.") from exc

    storage_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(storage_root, 0o700)
    _cleanup_stale_jobs(storage_root)
    _check_rate_limit(storage_root, remote_addr)
    lock_fd = _acquire_slot(storage_root)
    job_id = secrets.token_urlsafe(24)
    job_dir = storage_root / f"job-{job_id}"
    keep_job = False
    try:
        try:
            SafeHTTPClient._validate_url(start_url)
        except requests.RequestException as exc:
            raise CGIRequestError(
                "URL:en är ogiltig eller pekar inte på en publik HTTP/HTTPS-adress."
            ) from exc
        job_dir.mkdir(mode=0o700)
        os.chmod(job_dir, 0o700)
        output_dir = job_dir / "result"
        output_dir.mkdir(mode=0o700)
        settings = CrawlSettings(
            max_pages=MAX_PAGES,
            max_files=MAX_FILES,
            max_depth=MAX_DEPTH,
            max_file_size_mb=None,
            max_download_bytes=MAX_TOTAL_BYTES,
            max_single_file_bytes=MAX_SINGLE_FILE_BYTES,
            max_runtime_seconds=MAX_RUNTIME_SECONDS,
            include_files=False,
            include_text=True,
        )
        crawler = SiteCrawler(
            start_url,
            DirectoryStorage(output_dir),
            settings,
            http_get=SafeHTTPClient().get,
        )
        result = crawler.crawl()
        (job_dir / "analysis.json").write_text(
            json.dumps(
                {
                    "result": _serialize_result(result),
                    "created_at": time.time(),
                }
            ),
            encoding="utf-8",
        )
        keep_job = True
        return _response(
            200,
            "text/html; charset=utf-8",
            _render_preview(job_id, result),
        )
    finally:
        if not keep_job:
            shutil.rmtree(job_dir, ignore_errors=True)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _run_download(payload: dict[str, Any], storage_root: Path, remote_addr: str) -> tuple[int, dict[str, str], bytes]:
    job_id = str(payload.get("job_id", ""))
    if len(job_id) != 32 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in job_id):
        raise CGIRequestError("Analysen hittades inte eller har gått ut.", 404)
    mode = str(payload.get("mode", "text"))
    if mode not in {"text", "all"}:
        raise CGIRequestError("Välj ett giltigt innehållsläge.")
    max_pages = _integer(payload, "max_pages", MAX_PAGES, 1, MAX_PAGES)
    max_depth = _integer(payload, "max_depth", MAX_DEPTH, 0, MAX_DEPTH)

    _cleanup_stale_jobs(storage_root)
    _check_rate_limit(storage_root, remote_addr)
    lock_fd = _acquire_slot(storage_root)
    job_dir = storage_root / f"job-{job_id}"
    try:
        metadata_path = job_dir / "analysis.json"
        if not metadata_path.is_file() or time.time() - metadata_path.stat().st_mtime > STALE_JOB_SECONDS:
            raise CGIRequestError("Analysen hittades inte eller har gått ut. Kör analysen igen.", 404)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        analyzed_result = _deserialize_result(metadata["result"])
        output_dir = job_dir / "selected"
        output_dir.mkdir(mode=0o700)
        settings = CrawlSettings(
            max_pages=max_pages,
            max_files=MAX_FILES,
            max_depth=max_depth,
            max_download_bytes=MAX_TOTAL_BYTES,
            max_single_file_bytes=MAX_SINGLE_FILE_BYTES,
            max_runtime_seconds=MAX_RUNTIME_SECONDS,
            include_files=False,
            include_text=True,
        )
        crawler = SiteCrawler(
            analyzed_result.start_url,
            DirectoryStorage(output_dir),
            settings,
            http_get=SafeHTTPClient().get,
        )
        result = crawler.crawl()
        if mode == "all":
            result = crawler.download_files()
        archive = _create_archive(job_id, result, output_dir)
        filename = f"webgrabber-{job_id}.zip"
        return _response(
            200,
            "application/zip",
            archive,
            **{"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def handle_request(
    method: str,
    path: str,
    query_string: str,
    content_type: str,
    content_length: str,
    body: bytes,
    storage_root: Path,
    remote_addr: str = "unknown",
) -> tuple[int, dict[str, str], bytes]:
    if path in {"/health", "/api/health", "/index.py/health"} or "health" in parse_qs(query_string):
        return _health(storage_root)
    if method == "GET":
        return _response(200, "text/html; charset=utf-8", _render_form())
    if method != "POST":
        return _json_response(405, {"error": "Metoden stöds inte."})
    try:
        length = int(content_length or "0")
    except ValueError:
        return _json_response(400, {"error": "Ogiltig Content-Length."})
    if length < 0 or length > MAX_REQUEST_BYTES or len(body) != length:
        return _json_response(413, {"error": "Begäran är för stor eller ofullständig."})
    try:
        payload = _parse_payload(content_type, body)
        action = str(payload.get("action", "analyze"))
        if action == "analyze":
            return _run_analysis(payload, storage_root, remote_addr)
        if action == "download":
            return _run_download(payload, storage_root, remote_addr)
        raise CGIRequestError("Ogiltig åtgärd.")
    except CGIRequestError as exc:
        if content_type.split(";", 1)[0].strip().lower() == "application/x-www-form-urlencoded":
            return _response(exc.status, "text/html; charset=utf-8", _render_form(str(exc)))
        return _json_response(exc.status, {"error": str(exc)})
    except Exception:
        return _json_response(500, {"error": "Crawlningen misslyckades. Försök med en mindre webbplats."})