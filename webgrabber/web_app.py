from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from webgrabber.crawler import CrawlSettings, SiteCrawler, domain_folder_name

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
    target_dir = app.config["TARGET_ROOT"] / domain_folder_name(start_url)
    target_dir.mkdir(parents=True, exist_ok=True)

    crawler = SiteCrawler(
        start_url,
        str(target_dir),
        settings,
        progress_callback=lambda count, queued, current_url: None,
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

    max_pages = payload.get("max_pages")
    if max_pages is not None:
        try:
            max_pages = int(max_pages)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Max antal sidor måste vara ett heltal."}), 400
    if max_pages is not None and max_pages <= 0:
        max_pages = None

    max_depth = payload.get("max_depth", 3)
    try:
        max_depth = int(max_depth)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Crawl-djup måste vara ett heltal."}), 400

    max_file_size_mb = payload.get("max_file_size_mb")
    if max_file_size_mb is not None:
        try:
            max_file_size_mb = int(max_file_size_mb)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Max filstorlek måste vara ett heltal i MB."}), 400
        if max_file_size_mb <= 0:
            max_file_size_mb = None

    settings = CrawlSettings(
        max_pages=max_pages,
        max_depth=max_depth,
        max_file_size_mb=max_file_size_mb,
        include_files=payload.get("include_files", True),
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
        payload["files"] = sorted(result.files)
        payload["file_types"] = dict(result.file_types)
        payload["estimated_bytes"] = result.estimated_bytes
    if job.get("error"):
        payload["error"] = job["error"]
    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=app.config["WEBGRABBER_PORT"], debug=False)
