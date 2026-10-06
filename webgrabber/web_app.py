from __future__ import annotations

import os
import io
import json
import shutil
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request

from webgrabber.crawler import CrawlSettings, SiteCrawler, build_index, domain_folder_name, normalize_url
from webgrabber.safe_http import SafeHTTPClient
from webgrabber.storage import DirectoryStorage

app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")
app.config["WEBGRABBER_HOST"] = os.getenv("WEBGRABBER_HOST", "webgrabber.example.com")
app.config["WEBGRABBER_PORT"] = int(os.getenv("WEBGRABBER_PORT", "8000"))
app.config["TARGET_ROOT"] = Path(os.getenv("TARGET_ROOT", str(Path.home() / "WebGrabber")))
app.config["ALLOWED_HOSTS"] = [
    host.strip() for host in os.getenv("ALLOWED_HOSTS", "webgrabber.example.com,www.webgrabber.example.com").split(",") if host.strip()
]

JOBS: dict[str, dict[str, Any]] = {}
JOB_LOCK = threading.Lock()


def _make_job(start_url: str, settings: CrawlSettings) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    target_dir = app.config["TARGET_ROOT"] / job_id / domain_folder_name(start_url)
    target_dir.mkdir(parents=True, exist_ok=True)

    crawler = SiteCrawler(
        start_url,
        DirectoryStorage(target_dir),
        settings,
        progress_callback=lambda count, queued, current_url: None,
        http_get=SafeHTTPClient().get,
    )
    job = {
        "id": job_id,
        "status": "queued",
        "start_url": start_url,
        "target_dir": str(target_dir),
        "crawler": crawler,
        "result": None,
        "error": None,
    }
    with JOB_LOCK:
        JOBS[job_id] = job
    return job


def _run_job(job_id: str) -> None:
    job = JOBS[job_id]
    try:
        job["status"] = "running"
        result = job["crawler"].crawl()
        job["result"] = result
        job["status"] = result.status
    except Exception as exc:  # pragma: no cover - runtime safeguard
        job["status"] = "failed"
        job["error"] = str(exc)


@app.get("/")
def index():
    return render_template("index.html", hostname=app.config["WEBGRABBER_HOST"])


@app.post("/api/start")
def start_crawl():
    payload = request.get_json(silent=True) or {}
    start_url = (payload.get("start_url") or "").strip()
    if not start_url:
        return jsonify({"ok": False, "error": "Ange en URL eller domän."}), 400
    try:
        start_url = normalize_url(start_url)
        SafeHTTPClient._validate_url(start_url)
    except Exception:
        return jsonify({"ok": False, "error": "Ange en publik HTTP/HTTPS-adress."}), 400

    max_pages = payload.get("max_pages")
    if max_pages is not None:
        try:
            max_pages = int(max_pages)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Max antal sidor måste vara ett heltal."}), 400
    if max_pages is not None and max_pages <= 0:
        max_pages = None

    max_depth = payload.get("max_depth", 5)
    try:
        max_depth = int(max_depth)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Crawl-djup måste vara ett heltal."}), 400
    if not 0 <= max_depth <= 5:
        return jsonify({"ok": False, "error": "Crawl-djup måste vara 0–5."}), 400

    max_file_size_mb = payload.get("max_file_size_mb")
    if max_file_size_mb is not None:
        try:
            max_file_size_mb = int(max_file_size_mb)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Max filstorlek måste vara ett heltal i MB."}), 400
        if max_file_size_mb <= 0:
            max_file_size_mb = None

    if max_pages is None or not 1 <= max_pages <= 200:
        return jsonify({"ok": False, "error": "Max antal sidor måste vara 1–200."}), 400
    if max_file_size_mb is not None and not 1 <= max_file_size_mb <= 100:
        return jsonify({"ok": False, "error": "Max datamängd måste vara 1–100 MB."}), 400
    settings = CrawlSettings(
        max_pages=max_pages,
        max_depth=max_depth,
        max_file_size_mb=max_file_size_mb,
        include_files=False,
        include_text=True,
    )

    job = _make_job(start_url, settings)
    thread = threading.Thread(target=_run_job, args=(job["id"],), daemon=True)
    thread.start()

    return jsonify({"ok": True, "job_id": job["id"], "status": "queued"})


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Jobb hittades inte."}), 404

    result = job.get("result")
    payload = {
        "job_id": job_id,
        "status": job["status"],
        "start_url": job["start_url"],
        "target_dir": job["target_dir"],
    }
    if result is not None:
        payload["pages"] = sorted(result.pages)
        payload["linked_pages"] = sorted(result.linked_pages)
        payload["max_depth_reached"] = result.max_depth_reached
        payload["linked_files"] = sorted(result.linked_files)
        payload["file_count"] = len(result.linked_files)
        payload["file_types"] = dict(result.file_types)
        payload["external_domains"] = sorted(result.external_domains)
        payload["estimated_bytes"] = result.estimated_bytes
        payload["limit_reason"] = result.limit_reason
    if job.get("error"):
        payload["error"] = job["error"]
    return jsonify(payload)


@app.post("/api/download")
def download_job():
    payload = request.get_json(silent=True) or {}
    job_id = str(payload.get("job_id", ""))
    mode = str(payload.get("mode", "text"))
    if mode not in {"text", "all"}:
        return jsonify({"ok": False, "error": "Välj ett giltigt innehållsläge."}), 400
    job = JOBS.get(job_id)
    if not job or job["status"] not in {"complete", "time_limit", "limit_reached"}:
        return jsonify({"ok": False, "error": "Analysen hittades inte eller är inte klar."}), 404

    try:
        max_pages = int(payload.get("max_pages", job["crawler"].settings.max_pages))
        max_depth = int(payload.get("max_depth", job["crawler"].settings.max_depth))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Sidantal och crawl-djup måste vara heltal."}), 400
    if not 1 <= max_pages <= 200:
        return jsonify({"ok": False, "error": "Max antal sidor måste vara 1–200."}), 400
    if not 0 <= max_depth <= 5:
        return jsonify({"ok": False, "error": "Crawl-djup måste vara 0–5."}), 400

    output_dir = Path(job["target_dir"]).parent / "selected"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = CrawlSettings(
        max_pages=max_pages,
        max_depth=max_depth,
        max_file_size_mb=job["crawler"].settings.max_file_size_mb,
        max_download_bytes=job["crawler"].settings.max_download_bytes,
        max_single_file_bytes=job["crawler"].settings.max_single_file_bytes,
        max_files=job["crawler"].settings.max_files,
        include_files=False,
        include_text=True,
    )
    crawler = SiteCrawler(
        job["start_url"],
        DirectoryStorage(output_dir),
        settings,
        http_get=SafeHTTPClient().get,
    )
    result = crawler.crawl()
    if mode == "all":
        result = crawler.download_files()
    job["crawler"] = crawler
    job["target_dir"] = str(output_dir)
    job["status"] = result.status
    manifest = build_index(result)
    manifest["job_id"] = job_id
    DirectoryStorage(output_dir).write_json("index.json", manifest)
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and path.name != "webgrabber.log":
                archive.write(path, path.relative_to(output_dir).as_posix())
        archive.writestr("manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
    return Response(
        archive_buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="webgrabber-{job_id}.zip"'},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=app.config["WEBGRABBER_PORT"], debug=False)
