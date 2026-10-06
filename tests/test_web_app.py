import io
import json
import zipfile
from types import SimpleNamespace

from webgrabber import web_app
from webgrabber.safe_http import SafeHTTPClient


class FakeResponse:
    status_code = 200

    def __init__(self, content, content_type="text/html; charset=utf-8"):
        self.content = content
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(content))}

    def iter_content(self, chunk_size):
        yield self.content

    def close(self):
        pass


def test_flask_web_flow_analyzes_then_downloads_selected_content(tmp_path, monkeypatch):
    monkeypatch.setattr(
        SafeHTTPClient,
        "_resolve_public_addresses",
        staticmethod(lambda host, port: ["93.184.216.34"]),
    )

    def fake_get(self, url, **kwargs):
        if url.endswith("/robots.txt"):
            return FakeResponse(b"User-agent: *\nAllow: /")
        if url.endswith(".pdf"):
            return FakeResponse(b"PDF!", "application/pdf")
        if url.endswith("/about"):
            return FakeResponse(b"<html><body>About</body></html>")
        return FakeResponse(
            b'<html><a href="/about">About</a><a href="/guide.pdf">Guide</a></html>'
        )

    monkeypatch.setattr(SafeHTTPClient, "get", fake_get)

    class ImmediateThread:
        def __init__(self, target, args=(), **kwargs):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(web_app.threading, "Thread", ImmediateThread)
    monkeypatch.setitem(web_app.app.config, "TESTING", True)
    monkeypatch.setitem(web_app.app.config, "TARGET_ROOT", tmp_path)
    web_app.JOBS.clear()

    client = web_app.app.test_client()
    start = client.post(
        "/api/start",
        json={"start_url": "https://example.com/", "max_pages": 1, "max_depth": 0},
    )

    assert start.status_code == 200
    job_id = start.json["job_id"]
    status = client.get(f"/api/jobs/{job_id}")
    assert status.json["status"] == "complete"
    assert status.json["file_count"] == 1
    assert status.json["linked_files"] == ["https://example.com/guide.pdf"]
    assert status.json["linked_pages"] == ["https://example.com/about"]
    assert status.json["max_depth_reached"] == 0

    archive_response = client.post(
        "/api/download",
        json={"job_id": job_id, "mode": "all", "max_pages": 2, "max_depth": 1},
    )

    assert archive_response.status_code == 200
    assert archive_response.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive_response.data)) as archive:
        assert "pages/index.html" in archive.namelist()
        assert "files/guide.pdf" in archive.namelist()
        assert "https://example.com/about" in json.loads(archive.read("manifest.json"))["pages"]

    web_app.JOBS.clear()


def test_flask_template_exposes_requested_mode_labels():
    response = web_app.app.test_client().get("/")

    assert response.status_code == 200
    assert "Hämta enbart text".encode() in response.data
    assert "Hämta allt innehåll".encode() in response.data
    assert b"Analysera webbplats" in response.data
    assert "Hämta högst antal sidor".encode() in response.data
