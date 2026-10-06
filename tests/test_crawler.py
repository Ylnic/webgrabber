from types import SimpleNamespace

import pytest
import requests

from webgrabber.app import WebGrabberApp
from webgrabber.crawler import (
    CrawlSettings,
    CrawlResult,
    SiteCrawler,
    detect_file_type,
    extract_base_domain,
    normalize_url,
    should_visit,
)
from webgrabber.storage import DirectoryStorage


def test_normalize_url_removes_trailing_slash_and_fragments():
    assert normalize_url("https://example.com/path/?a=1#section") == "https://example.com/path/?a=1"


def test_extract_base_domain_handles_www_and_trailing_slash():
    assert extract_base_domain("https://www.example.se/about") == "example.se"
    assert extract_base_domain("example.se") == "example.se"


def test_should_visit_rejects_external_domains_and_duplicates():
    assert should_visit("https://example.se/about", "https://example.se", "https://example.se/about") is False
    assert should_visit("https://cdn.example.se/file.pdf", "https://example.se", "https://example.se/about") is False
    assert should_visit("https://example.se/contact", "https://example.se", "https://example.se/about") is True


def test_detect_file_type_identifies_common_files():
    assert detect_file_type("https://example.se/report.pdf") == "pdf"
    assert detect_file_type("https://example.se/image.jpg") == "jpg"
    assert detect_file_type("https://example.se/page.html") == "html"
    assert detect_file_type("https://example.se/file") == "unknown"


def test_crawl_fetches_start_page(monkeypatch, tmp_path):
    response = SimpleNamespace(
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        content=b"<html><body>Start page</body></html>",
    )
    monkeypatch.setattr("webgrabber.crawler.requests.get", lambda *args, **kwargs: response)

    crawler = SiteCrawler("https://example.se", DirectoryStorage(tmp_path))
    monkeypatch.setattr(crawler, "_is_allowed_by_robots", lambda url: True)

    result = crawler.crawl()

    assert result.pages == {"https://example.se"}
    assert (tmp_path / "pages" / "index.html").exists()
    run_log = (tmp_path / "webgrabber.log").read_text(encoding="utf-8")
    assert "CRAWL start" in run_log
    assert "GET start url=https://example.se" in run_log
    assert "PAGE sparad sida=1" in run_log
    assert "CRAWL slut status=complete sidor=1" in run_log


def test_crawler_works_with_non_filesystem_storage():
    class MemoryStorage:
        def __init__(self):
            self.pages = {}
            self.events = []

        def log_event(self, message):
            self.events.append(message)

        def store_page(self, url, html):
            self.pages[url] = html

        def store_file(self, url, content):
            return url

        def child(self, name):
            return self

    storage = MemoryStorage()
    response = SimpleNamespace(
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        content=b"<html><body>Memory page</body></html>",
    )
    crawler = SiteCrawler(
        "https://example.se",
        storage,
        http_get=lambda *args, **kwargs: response,
    )
    crawler._is_allowed_by_robots = lambda url: True

    result = crawler.crawl()

    assert result.pages == {"https://example.se"}
    assert storage.pages["https://example.se"].startswith("<html>")
    assert storage.events


def test_crawler_reports_runtime_limit_without_starting_requests(tmp_path):
    crawler = SiteCrawler(
        "https://example.se",
        DirectoryStorage(tmp_path),
        CrawlSettings(max_runtime_seconds=0),
    )
    crawler._is_allowed_by_robots = lambda url: pytest.fail("Request should not start")

    result = crawler.crawl()

    assert result.status == "time_limit"
    assert result.limit_reason == "max_runtime_seconds"


def test_crawler_stops_before_exceeding_total_byte_limit(monkeypatch, tmp_path):
    response = SimpleNamespace(
        status_code=200,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": "10",
        },
        content=b"0123456789",
    )
    monkeypatch.setattr("webgrabber.crawler.requests.get", lambda *args, **kwargs: response)
    crawler = SiteCrawler(
        "https://example.se",
        DirectoryStorage(tmp_path),
        CrawlSettings(max_download_bytes=5),
    )
    crawler._is_allowed_by_robots = lambda url: True

    result = crawler.crawl()

    assert result.pages == set()
    assert result.status == "limit_reached"
    assert result.limit_reason == "max_total_bytes"


def test_crawler_limits_file_count_before_reading_file_body(monkeypatch, tmp_path):
    requested = []

    def get_response(url, **kwargs):
        requested.append(url)
        if url == "https://example.se/":
            return SimpleNamespace(
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=b'<a href="/document.pdf">PDF</a>',
            )
        return SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "application/pdf", "Content-Length": "100"},
            content=b"must not be read",
            close=lambda: None,
        )

    monkeypatch.setattr("webgrabber.crawler.requests.get", get_response)
    crawler = SiteCrawler(
        "https://example.se/",
        DirectoryStorage(tmp_path),
        CrawlSettings(max_files=0, max_depth=1),
    )
    crawler._is_allowed_by_robots = lambda url: True

    result = crawler.crawl()

    assert requested == ["https://example.se/"]
    assert result.files == set()
    assert result.status == "limit_reached"
    assert result.limit_reason == "max_files"


def test_analysis_discovers_files_without_fetching_them(tmp_path):
    requests_seen = []

    def get_response(url, **kwargs):
        requests_seen.append(url)
        return SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=b'<a href="/guide.pdf">Guide</a>',
        )

    crawler = SiteCrawler(
        "https://example.se/",
        DirectoryStorage(tmp_path),
        CrawlSettings(include_files=False, max_depth=2),
        http_get=get_response,
    )
    crawler._is_allowed_by_robots = lambda url: True

    result = crawler.crawl()

    assert requests_seen == ["https://example.se/"]
    assert result.linked_files == {"https://example.se/guide.pdf"}
    assert result.files == set()
    assert result.file_types == {"pdf": 1}


def test_analysis_caps_discovered_file_urls(tmp_path):
    response = SimpleNamespace(
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        content=b'<a href="/one.pdf">One</a><a href="/two.pdf">Two</a>',
    )
    crawler = SiteCrawler(
        "https://example.se/",
        DirectoryStorage(tmp_path),
        CrawlSettings(include_files=False, max_files=1, max_depth=1),
        http_get=lambda *args, **kwargs: response,
    )
    crawler._is_allowed_by_robots = lambda url: True

    result = crawler.crawl()

    assert result.linked_files == {"https://example.se/one.pdf"}
    assert result.status == "limit_reached"
    assert result.limit_reason == "max_files"


def test_download_files_fetches_only_discovered_links(tmp_path):
    response = SimpleNamespace(
        status_code=200,
        headers={"Content-Length": "4"},
        iter_content=lambda chunk_size: iter((b"data",)),
        close=lambda: None,
    )
    requests_seen = []
    crawler = SiteCrawler(
        "https://example.se/",
        DirectoryStorage(tmp_path),
        CrawlSettings(max_download_bytes=10, max_single_file_bytes=8),
        http_get=lambda url, **kwargs: requests_seen.append(url) or response,
    )
    crawler.result.linked_files = {"https://example.se/guide.pdf"}
    crawler._is_allowed_by_robots = lambda url: True

    result = crawler.download_files()

    assert requests_seen == ["https://example.se/guide.pdf"]
    assert result.files == {"https://example.se/guide.pdf"}
    assert (tmp_path / "files" / "guide.pdf").read_bytes() == b"data"


def test_robots_request_has_timeout_and_honors_rules(monkeypatch, tmp_path):
    requests_seen = []

    def get_response(url, **kwargs):
        requests_seen.append((url, kwargs))
        return SimpleNamespace(
            status_code=200,
            text="User-agent: *\nDisallow: /private",
        )

    monkeypatch.setattr("webgrabber.crawler.requests.get", get_response)
    crawler = SiteCrawler("https://example.se", DirectoryStorage(tmp_path))

    assert crawler._is_allowed_by_robots("https://example.se/private") is False
    assert requests_seen[0][1]["timeout"] == (5, 10)
    run_log = (tmp_path / "webgrabber.log").read_text(encoding="utf-8")
    assert "ROBOTS blockerad url=https://example.se/private" in run_log


def test_robots_timeout_is_logged_and_does_not_hang(monkeypatch, tmp_path):
    def timeout(url, **kwargs):
        raise requests.Timeout("robots request timed out")

    monkeypatch.setattr("webgrabber.crawler.requests.get", timeout)
    crawler = SiteCrawler("https://example.se", DirectoryStorage(tmp_path))

    assert crawler._is_allowed_by_robots("https://example.se/page") is True
    run_log = (tmp_path / "webgrabber.log").read_text(encoding="utf-8")
    assert "ROBOTS start" in run_log
    assert "ROBOTS nätverksfel" in run_log


def test_crawl_respects_max_pages(monkeypatch, tmp_path):
    visited = []

    def get_response(url, **kwargs):
        visited.append(url)
        return SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=b'<a href="https://example.se/second">Next</a>',
        )

    monkeypatch.setattr("webgrabber.crawler.requests.get", get_response)
    crawler = SiteCrawler("https://example.se", DirectoryStorage(tmp_path), CrawlSettings(max_pages=1))
    monkeypatch.setattr(crawler, "_is_allowed_by_robots", lambda url: True)

    result = crawler.crawl()

    assert result.pages == {"https://example.se"}
    assert visited == ["https://example.se"]


def test_crawl_allows_unlimited_pages(monkeypatch, tmp_path):
    visited = []

    def get_response(url, **kwargs):
        visited.append(url)
        if url == "https://example.se":
            content = b'<a href="https://example.se/second">Next</a>'
        else:
            content = b"Done"
        return SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=content,
        )

    monkeypatch.setattr("webgrabber.crawler.requests.get", get_response)
    crawler = SiteCrawler(
        "https://example.se",
        DirectoryStorage(tmp_path),
        CrawlSettings(max_pages=None, max_depth=1, max_file_size_mb=None),
    )
    monkeypatch.setattr(crawler, "_is_allowed_by_robots", lambda url: True)

    result = crawler.crawl()

    assert len(result.pages) == 2
    assert len(visited) == 2


def test_crawl_decodes_html_as_utf8_despite_conflicting_http_charset(monkeypatch, tmp_path):
    expected = 'ÅÄÖ åäö – — ”svensk text” – Tjänster, förbättringar, säkerhet, överlämning och löpande underhåll.'
    html = f'<html><head><meta charset="utf-8"></head><body>{expected}</body></html>'
    response = SimpleNamespace(
        status_code=200,
        headers={"Content-Type": "text/html; charset=ISO-8859-1"},
        content=html.encode("utf-8"),
    )
    monkeypatch.setattr("webgrabber.crawler.requests.get", lambda *args, **kwargs: response)

    crawler = SiteCrawler("https://example.se", DirectoryStorage(tmp_path))
    monkeypatch.setattr(crawler, "_is_allowed_by_robots", lambda url: True)

    crawler.crawl()

    saved_html = (tmp_path / "pages" / "index.html").read_text(encoding="utf-8")
    assert expected in saved_html
    assert not any(sequence in saved_html for sequence in ("Ã¥", "Ã¤", "Ã¶", "Ã", "Ã", "Ã", "â"))


def test_download_material_saves_html_as_utf8(monkeypatch, tmp_path):
    expected = 'ÅÄÖ åäö – — ”svensk text” – Tjänster, förbättringar, säkerhet, överlämning och löpande underhåll.'
    html = f'<html><head><meta charset="utf-8"></head><body>{expected}</body></html>'
    monkeypatch.setattr("webgrabber.app.messagebox.showinfo", lambda *args, **kwargs: None)
    opened = []
    reset = []
    pdf_calls = []
    progress_updates = []
    log_messages = []
    monkeypatch.setattr("webgrabber.app.messagebox.askyesno", lambda *args, **kwargs: pytest.fail("PDF prompt shown"))

    class FakeThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    class FakeProgress:
        def configure(self, **kwargs):
            progress_updates.append((kwargs["maximum"], kwargs["value"]))

    class FakeProgressVar:
        def set(self, value):
            pass

    monkeypatch.setattr("webgrabber.app.threading.Thread", FakeThread)

    result = CrawlResult(start_url="https://example.se", base_domain="example.se")
    result.pages.add("https://example.se/")
    site_dir = tmp_path / "example.se"
    page_path = site_dir / "pages" / "index.html"
    page_path.parent.mkdir(parents=True)
    page_path.write_text(html, encoding="utf-8")
    app = SimpleNamespace(
        crawler=SimpleNamespace(result=result, log_event=log_messages.append),
            config={"target_dir": str(tmp_path), "start_url": "https://www.example.se", "mode": "text"},
        _site_output_dir=lambda: tmp_path / "example.se",
        _domain_output_dir=lambda url: tmp_path / "example.se",
        _reset_wizard=lambda: reset.append(True),
        _create_page_pdfs=lambda path: pdf_calls.append(path) or [],
        current_step=5,
        render_step=lambda: None,
        download_progress=FakeProgress(),
        download_progress_var=FakeProgressVar(),
        after=lambda delay, callback, *args: callback(*args),
    )
    app._download_finished = lambda pdf_count, errors: WebGrabberApp._download_finished(
        app, pdf_count, errors
    )
    app._download_worker = lambda: WebGrabberApp._download_worker(app)
    app._update_download_progress = lambda completed, total: WebGrabberApp._update_download_progress(
        app, completed, total
    )

    monkeypatch.setattr(
        "webgrabber.app.subprocess.Popen",
        lambda *args, **kwargs: opened.append(args[0]),
    )

    WebGrabberApp.download_material(app)

    saved_html = (site_dir / "pages" / "index.html").read_text(encoding="utf-8")
    assert expected in saved_html
    assert not any(sequence in saved_html for sequence in ("Ã¥", "Ã¤", "Ã¶", "Ã", "Ã", "Ã", "â"))
    assert pdf_calls == [site_dir]
    assert opened == [["open", str(site_dir)]]
    assert reset == [True]
    assert progress_updates == [(1, 0), (1, 1)]
    assert any("DOWNLOAD sida redo" in message for message in log_messages)


def test_create_page_pdfs_writes_unicode_pdf_per_page(tmp_path):
    pytest.importorskip("reportlab")
    expected = "ÅÄÖ åäö – — ”svensk text”"
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    (pages_dir / "index.html").write_text(
        f"<html><body><p>{expected}</p><script>not visible</script></body></html>",
        encoding="utf-8",
    )

    created = WebGrabberApp._create_page_pdfs(None, tmp_path)

    assert created == [tmp_path / "pdf" / "index.pdf"]
    assert created[0].read_bytes().startswith(b"%PDF-")


def test_finish_step_keeps_app_open_for_next_crawl():
    downloads = []
    closes = []
    app = SimpleNamespace(
        current_step=5,
        mode_var=SimpleNamespace(get=lambda: "text"),
        config={},
        download_material=lambda: downloads.append(True),
        destroy=lambda: closes.append(True),
    )

    WebGrabberApp.next_step(app)

    assert downloads == [True]
    assert closes == []


def test_reset_wizard_returns_to_start_and_keeps_output_folder():
    class FakeButton:
        def config(self, **kwargs):
            self.text = kwargs["text"]

    class FakeStatus:
        def set(self, value):
            self.value = value

    rendered = []
    app = SimpleNamespace(
        config={
            "target_dir": "/tmp/webgrabber-output",
            "start_url": "https://example.se",
            "mode": "text_files",
            "max_pages": 15,
            "max_depth": 2,
            "max_file_size_mb": 10,
        },
        crawler=object(),
        crawl_thread=object(),
        current_step=5,
        preview_result=object(),
        next_btn=FakeButton(),
        status_var=FakeStatus(),
        render_step=lambda: rendered.append(True),
    )

    WebGrabberApp._reset_wizard(app)

    assert app.current_step == 0
    assert app.config["target_dir"] == "/tmp/webgrabber-output"
    assert app.config["start_url"] == ""
    assert app.config["mode"] == "text"
    assert app.crawler is None
    assert app.crawl_thread is None
    assert app.preview_result is None
    assert app.next_btn.text == "Nästa"
    assert rendered == [True]


def test_download_without_material_opens_folder_and_resets(monkeypatch, tmp_path):
    opened = []
    reset = []
    monkeypatch.setattr("webgrabber.app.messagebox.showinfo", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "webgrabber.app.subprocess.Popen",
        lambda *args, **kwargs: opened.append(args[0]),
    )
    app = SimpleNamespace(
        crawler=SimpleNamespace(result=CrawlResult(start_url="https://example.se", base_domain="example.se")),
        config={"target_dir": str(tmp_path), "start_url": "https://example.se"},
        _site_output_dir=lambda: tmp_path / "example.se",
        _reset_wizard=lambda: reset.append(True),
    )

    WebGrabberApp.download_material(app)

    assert opened == [["open", str(tmp_path / "example.se")]]
    assert reset == [True]


def test_site_output_dir_uses_domain_name(tmp_path):
    app = SimpleNamespace(
        config={
            "target_dir": str(tmp_path),
            "start_url": "https://www.example.se/about",
        }
    )

    assert WebGrabberApp._site_output_dir(app) == tmp_path / "example.se"


def test_open_run_log_opens_selected_domain_log(monkeypatch, tmp_path):
    opened = []
    site_dir = tmp_path / "example.se"
    site_dir.mkdir()
    log_path = site_dir / "webgrabber.log"
    log_path.write_text("test log", encoding="utf-8")
    monkeypatch.setattr(
        "webgrabber.app.subprocess.Popen",
        lambda args: opened.append(args),
    )
    app = SimpleNamespace(_site_output_dir=lambda: site_dir)

    WebGrabberApp.open_run_log(app)

    assert opened == [["open", str(log_path)]]


def test_external_pages_resolve_under_main_domain_folder(tmp_path):
    app = SimpleNamespace(
        config={"target_dir": str(tmp_path), "start_url": "https://aftonbladet.se"},
        crawler=SimpleNamespace(base_domain="aftonbladet.se"),
        _site_output_dir=lambda: tmp_path / "aftonbladet.se",
    )

    assert WebGrabberApp._domain_output_dir(app, "https://lyko.se/product") == (
        tmp_path / "aftonbladet.se" / "lyko.se"
    )


def test_start_crawl_uses_domain_output_directory(monkeypatch, tmp_path):
    crawler_args = []

    class FakeStatus:
        def set(self, value):
            self.value = value

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            pass

    monkeypatch.setattr(
        "webgrabber.app.SiteCrawler",
        lambda *args, **kwargs: crawler_args.append((args, kwargs)) or SimpleNamespace(),
    )
    monkeypatch.setattr("webgrabber.app.threading.Thread", FakeThread)
    app = SimpleNamespace(
        config={
            "target_dir": str(tmp_path),
            "start_url": "https://www.example.se",
            "max_pages": 1,
            "max_depth": 0,
            "max_file_size_mb": 1,
            "mode": "text",
        },
        status_var=FakeStatus(),
        _site_output_dir=lambda: tmp_path / "example.se",
        _crawl_worker=lambda: None,
    )

    WebGrabberApp.start_crawl(app)

    assert crawler_args[0][0][1].root == tmp_path / "example.se"
    assert callable(crawler_args[0][1]["progress_callback"])


def test_external_domain_crawl_uses_own_folder(monkeypatch, tmp_path):
    responses = {
        "https://aftonbladet.se/": "<a href='https://lyko.se/product'>Lyko</a>",
        "https://lyko.se/product": "<a href='/other'>Other</a>",
        "https://lyko.se/other": "Lyko page",
    }

    def get_response(url, **kwargs):
        return SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=f"<html><body>{responses[url]}</body></html>".encode("utf-8"),
        )

    monkeypatch.setattr("webgrabber.crawler.requests.get", get_response)
    monkeypatch.setattr(SiteCrawler, "_is_allowed_by_robots", lambda self, url: True)
    progress = []
    output_dir = tmp_path / "aftonbladet.se"
    crawler = SiteCrawler(
        "https://aftonbladet.se/",
        DirectoryStorage(output_dir),
        CrawlSettings(max_pages=10, max_depth=1),
        progress_callback=lambda *values: progress.append(values),
    )

    result = crawler.crawl()
    assert result.external_domains == {"lyko.se"}
    assert result.external_pages == {"https://lyko.se/product"}

    crawler.crawl_external_domains()

    assert "https://lyko.se/product" in result.pages
    assert "https://lyko.se/other" in result.pages
    assert (output_dir / "lyko.se" / "pages" / "product.html").exists()
    assert (output_dir / "lyko.se" / "pages" / "other.html").exists()
    assert progress[-1][0] == 3
    assert any(event[2] == "https://lyko.se/product" for event in progress)


def test_external_domains_are_reported_when_main_page_limit_is_exhausted(monkeypatch, tmp_path):
    monkeypatch.setattr(SiteCrawler, "_is_allowed_by_robots", lambda self, url: True)
    crawler = SiteCrawler(
        "https://aftonbladet.se/",
        DirectoryStorage(tmp_path / "aftonbladet.se"),
        CrawlSettings(max_pages=1, max_depth=1, max_file_size_mb=None),
    )
    crawler.result.pages.add("https://aftonbladet.se/")
    crawler.result.external_pages.add("https://lyko.se/product")

    crawler.crawl_external_domains()

    assert crawler.result.skipped_external_domains == {"lyko.se"}
    assert not (tmp_path / "aftonbladet.se" / "lyko.se").exists()


def test_finish_crawl_asks_before_crawling_external_domains(monkeypatch):
    prompts = []
    threads = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            threads.append(self)

        def start(self):
            pass

    monkeypatch.setattr(
        "webgrabber.app.messagebox.askyesno",
        lambda *args, **kwargs: prompts.append(args) or True,
    )
    monkeypatch.setattr("webgrabber.app.threading.Thread", FakeThread)
    result = CrawlResult(start_url="https://aftonbladet.se", base_domain="aftonbladet.se")
    result.external_pages.add("https://lyko.se/product")
    result.status = "complete"
    status_updates = []
    progress_updates = []
    app = SimpleNamespace(
        crawler=SimpleNamespace(result=result),
        current_step=3,
        status_var=SimpleNamespace(set=status_updates.append),
        progress_var=SimpleNamespace(set=progress_updates.append),
        _update_crawl_progress=lambda count, queued, current_url: None,
        _external_crawl_worker=lambda: None,
    )

    WebGrabberApp._finish_crawl(app)

    assert "lyko.se" in prompts[0][1]
    assert threads[0].kwargs["target"] == app._external_crawl_worker
    assert callable(app.crawler.progress_callback)
    assert status_updates == ["Genomsöker valda externa domäner…"]


def test_update_crawl_progress_updates_count_queue_and_current_url():
    values = []
    messages = []
    app = SimpleNamespace(
        current_step=3,
        config={"max_pages": 10},
        progress=SimpleNamespace(configure=lambda **kwargs: values.append(kwargs)),
        progress_var=SimpleNamespace(set=messages.append),
    )

    WebGrabberApp._update_crawl_progress(app, 3, 4, "https://example.se/page")

    assert values == [{"maximum": 10, "value": 3}]
    assert messages == ["Hämtar example.se/page · 3 av 10 sidor klara · 4 väntar"]


def test_update_crawl_progress_is_determinate_when_pages_are_unlimited():
    values = []
    messages = []
    app = SimpleNamespace(
        current_step=3,
        config={"max_pages": None},
        progress=SimpleNamespace(configure=lambda **kwargs: values.append(kwargs)),
        progress_var=SimpleNamespace(set=messages.append),
    )

    WebGrabberApp._update_crawl_progress(app, 5, 6, "")

    assert values == [{"maximum": 11, "value": 5}]
    assert messages == ["5 sidor klara · obegränsat · 6 väntar"]


