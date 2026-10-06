import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from webgrabber.loopia import handle_request
from webgrabber.safe_http import SafeHTTPClient


def test_health_endpoint_checks_private_storage(tmp_path):
    status, headers, body = handle_request(
        "GET", "/health", "", "", "0", b"", tmp_path / "private"
    )

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(body) == {
        "status": "ok",
        "crawler": "ready",
        "storage": "writable",
    }


def test_homepage_is_served_without_starting_crawler(tmp_path):
    status, headers, body = handle_request("GET", "/", "", "", "0", b"", tmp_path)

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"WebGrabber" in body


def test_post_rejects_private_ip_before_creating_job(tmp_path):
    body = b'{"start_url":"http://127.0.0.1/","max_pages":1,"max_depth":0}'
    status, _, response = handle_request(
        "POST",
        "/",
        "",
        "application/json",
        str(len(body)),
        body,
        tmp_path / "private",
    )

    assert status == 400
    assert b"publik" in response
    assert not list((tmp_path / "private").glob("job-*"))


@pytest.mark.parametrize(
    ("mode", "expected_files"),
    [("text", []), ("all", ["https://example.com/guide.pdf"])],
)
def test_analysis_previews_then_returns_selected_zip(tmp_path, monkeypatch, mode, expected_files):
    def resolve(host, port):
        return ["93.184.216.34"]

    class FakeHTTPResponse:
        status = 200

        def __init__(self, body, content_type="text/html; charset=utf-8"):
            self.body = body
            self.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}

        def stream(self, chunk_size, decode_content=True):
            yield self.body

        def close(self):
            pass

    def request_once(scheme, host, port, address, path, headers, timeout):
        if path == "/robots.txt":
            return FakeHTTPResponse(b"User-agent: *\nAllow: /")
        if path == "/guide.pdf":
            return FakeHTTPResponse(b"PDF!", "application/pdf")
        if path == "/about":
            return FakeHTTPResponse(b"<html><body>About</body></html>")
        return FakeHTTPResponse(
            b'<html><body>Public page<a href="/about">About</a>'
            b'<a href="/guide.pdf">Guide</a></body></html>'
        )

    monkeypatch.setattr(SafeHTTPClient, "_resolve_public_addresses", staticmethod(resolve))
    monkeypatch.setattr(SafeHTTPClient, "_request_once", staticmethod(request_once))
    body = b"action=analyze&start_url=https%3A%2F%2Fexample.com%2F&max_pages=1&max_depth=0"
    storage_root = tmp_path / "private"

    status, headers, preview = handle_request(
        "POST",
        "/",
        "",
        "application/x-www-form-urlencoded",
        str(len(body)),
        body,
        storage_root,
        "203.0.113.12",
    )

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Hämta enbart text".encode() in preview
    assert "Hämta allt innehåll".encode() in preview
    assert b"L\xc3\xa4nkade filer (max 50)</dt><dd>1</dd>" in preview
    assert b"Interna sidl\xc3\xa4nkar</dt><dd>1</dd>" in preview
    assert b"Maxdjup (0\xe2\x80\x935)" in preview
    job_id = re.search(rb'name="job_id" value="([A-Za-z0-9_-]{32})"', preview).group(1).decode()
    job_dir = storage_root / f"job-{job_id}"
    assert job_dir.is_dir()

    download_body = (
        f"action=download&job_id={job_id}&mode={mode}&max_pages=2&max_depth=1".encode()
    )
    status, headers, archive_bytes = handle_request(
        "POST",
        "/",
        "",
        "application/x-www-form-urlencoded",
        str(len(download_body)),
        download_body,
        storage_root,
        "203.0.113.12",
    )

    assert status == 200
    assert headers["Content-Type"] == "application/zip"
    assert headers["Content-Disposition"].startswith("attachment; filename=\"webgrabber-")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert "pages/index.html" in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["job_id"] in headers["Content-Disposition"]
        assert manifest["pages"] == ["https://example.com/", "https://example.com/about"]
        assert manifest["files"] == expected_files
    assert not job_dir.exists()


def test_download_rejects_unbounded_selection(tmp_path):
    body = (
        b"action=download&job_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&mode=text"
        b"&max_pages=999&max_depth=5"
    )
    status, _, response = handle_request(
        "POST",
        "/",
        "",
        "application/x-www-form-urlencoded",
        str(len(body)),
        body,
        tmp_path,
    )

    assert status == 400
    assert b"max_pages" in response