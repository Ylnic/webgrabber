from __future__ import annotations

import json
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from bs4 import BeautifulSoup

from webgrabber.crawler import (
    CrawlSettings,
    SiteCrawler,
    build_index,
    decode_html_response,
    domain_folder_name,
    extract_base_domain,
)
from webgrabber.storage import DirectoryStorage


class WebGrabberApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WebGrabber")
        self.geometry("880x620")
        self.minsize(820, 520)
        self.configure(bg="#f5f5f5")

        self.config = {
            "target_dir": str(Path.home() / "WebGrabber"),
            "start_url": "",
            "mode": "text",
            "max_pages": 200,
            "max_depth": 3,
            "max_file_size_mb": 50,
        }
        self.crawler: SiteCrawler | None = None
        self.crawl_thread: threading.Thread | None = None
        self.download_thread: threading.Thread | None = None
        self.current_step = 0
        self.preview_result = None
        self.step_names = [
            "Målmapp",
            "Webbplats",
            "Begränsningar",
            "Genomsökning",
            "Förhandsgranskning",
            "Innehåll",
            "Nedladdning",
        ]

        self.top_bar = ttk.Frame(self, padding=12)
        self.top_bar.pack(fill="x")
        ttk.Label(self.top_bar, text="WebGrabber", font=("SF Pro Display", 18, "bold")).pack(anchor="w")
        self.step_label = ttk.Label(self.top_bar, text="Steg 1 av 7")
        self.step_label.pack(anchor="w", pady=(6, 0))

        self.content = ttk.Frame(self, padding=(16, 8, 16, 20))
        self.content.pack(fill="both", expand=True)

        self.footer = ttk.Frame(self, padding=(12, 8, 12, 16))
        self.footer.pack(fill="x")
        self.back_btn = ttk.Button(self.footer, text="Tillbaka", command=self.prev_step)
        self.next_btn = ttk.Button(self.footer, text="Nästa", command=self.next_step)
        self.back_btn.pack(side="left")
        self.next_btn.pack(side="right")

        self.form_holder = ttk.Frame(self.content)
        self.form_holder.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Redo för att starta.")
        self.status = ttk.Label(self.footer, textvariable=self.status_var, foreground="#1a5f8d")
        self.status.pack(side="left", padx=(0, 10))

        self.render_step()

    def render_step(self):
        for child in self.form_holder.winfo_children():
            child.destroy()

        self.step_label.config(text=f"Steg {self.current_step + 1} av {len(self.step_names)}: {self.step_names[self.current_step]}")
        self.back_btn.state(["disabled"] if self.current_step == 0 else ["!disabled"])
        self.next_btn.state(["!disabled"])

        if self.current_step == 0:
            self.render_målmapp()
        elif self.current_step == 1:
            self.render_webbplats()
        elif self.current_step == 2:
            self.render_begränsningar()
        elif self.current_step == 3:
            self.render_genomsökning()
        elif self.current_step == 4:
            self.render_preview()
        elif self.current_step == 5:
            self.render_innehåll()
        else:
            self.render_downloading()

    def render_målmapp(self):
        ttk.Label(self.form_holder, text="Välj överordnad mapp för hämtningar", font=("SF Pro Display", 14, "bold")).pack(anchor="w", pady=(0, 12))
        ttk.Label(self.form_holder, text="En undermapp skapas automatiskt för varje domän.").pack(anchor="w")
        ttk.Label(self.form_holder, text="Överordnad mapp:").pack(anchor="w")
        folder_row = ttk.Frame(self.form_holder)
        folder_row.pack(fill="x", pady=(6, 12))
        self.folder_var = tk.StringVar(value=self.config["target_dir"])
        ttk.Entry(folder_row, textvariable=self.folder_var, width=80).pack(side="left", fill="x", expand=True)
        ttk.Button(folder_row, text="Bläddra…", command=self.choose_folder).pack(side="left", padx=(8, 0))

    def render_webbplats(self):
        ttk.Label(self.form_holder, text="Ange webbplats eller domän", font=("SF Pro Display", 14, "bold")).pack(anchor="w", pady=(0, 12))
        ttk.Label(self.form_holder, text="URL / domän:").pack(anchor="w")
        self.url_var = tk.StringVar(value=self.config["start_url"])
        ttk.Entry(self.form_holder, textvariable=self.url_var, width=100).pack(fill="x", pady=(6, 8))
        ttk.Label(self.form_holder, text="Programmet identifierar automatiskt basdomänen.", foreground="#555555").pack(anchor="w")

    def render_innehåll(self):
        ttk.Label(self.form_holder, text="Välj vad som ska hämtas", font=("SF Pro Display", 14, "bold")).pack(anchor="w", pady=(0, 12))
        self.mode_var = tk.StringVar(value=self.config["mode"])
        mode_frame = ttk.Frame(self.form_holder)
        mode_frame.pack(fill="x")
        for label, value in (("Hämta enbart text", "text"), ("Hämta allt innehåll", "text_files")):
            ttk.Radiobutton(mode_frame, text=label, variable=self.mode_var, value=value).pack(anchor="w", pady=4)

    def render_begränsningar(self):
        ttk.Label(self.form_holder, text="Begränsningar för genomsökning", font=("SF Pro Display", 14, "bold")).pack(anchor="w", pady=(0, 12))
        grid = ttk.Frame(self.form_holder)
        grid.pack(fill="x")

        ttk.Label(grid, text="Max antal sidor:").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=6)
        self.max_pages_var = tk.StringVar(value=str(self.config["max_pages"]))
        self.max_pages_entry = ttk.Entry(grid, textvariable=self.max_pages_var, width=12)
        self.max_pages_entry.grid(row=0, column=1, sticky="w")
        self.unlimited_pages_var = tk.BooleanVar(value=self.config["max_pages"] is None)
        ttk.Checkbutton(
            grid,
            text="Obegränsat antal sidor",
            variable=self.unlimited_pages_var,
            command=lambda: self._toggle_limit_entry(self.max_pages_entry, self.unlimited_pages_var),
        ).grid(row=0, column=2, sticky="w", padx=(10, 0))

        ttk.Label(grid, text="Crawl-djup:").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=6)
        self.max_depth_var = tk.StringVar(value=str(self.config["max_depth"]))
        ttk.Entry(grid, textvariable=self.max_depth_var, width=12).grid(row=1, column=1, sticky="w")

        ttk.Label(grid, text="Max total storlek (MB):").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=6)
        self.max_size_var = tk.StringVar(value=str(self.config["max_file_size_mb"]))
        self.max_size_entry = ttk.Entry(grid, textvariable=self.max_size_var, width=12)
        self.max_size_entry.grid(row=2, column=1, sticky="w")
        self.unlimited_size_var = tk.BooleanVar(value=self.config["max_file_size_mb"] is None)
        ttk.Checkbutton(
            grid,
            text="Obegränsad storlek",
            variable=self.unlimited_size_var,
            command=lambda: self._toggle_limit_entry(self.max_size_entry, self.unlimited_size_var),
        ).grid(row=2, column=2, sticky="w", padx=(10, 0))
        if self.unlimited_pages_var.get():
            self.max_pages_entry.state(["disabled"])
        if self.unlimited_size_var.get():
            self.max_size_entry.state(["disabled"])

    @staticmethod
    def _toggle_limit_entry(entry, variable):
        entry.state(["disabled"] if variable.get() else ["!disabled"])

    @staticmethod
    def _parse_optional_limit(value: str, unlimited: bool) -> int | None:
        if unlimited:
            return None
        limit = int(value)
        if limit <= 0:
            raise ValueError("Limit must be positive")
        return limit

    def render_genomsökning(self):
        ttk.Label(self.form_holder, text="Genomsökning pågår", font=("SF Pro Display", 14, "bold")).pack(anchor="w", pady=(0, 12))
        self.progress_var = tk.StringVar(value="Detta kan ta några sekunder…")
        ttk.Label(self.form_holder, textvariable=self.progress_var).pack(anchor="w", pady=(0, 14))

        self.progress = ttk.Progressbar(
            self.form_holder,
            mode="determinate",
            maximum=max(1, self.config["max_pages"] or 1),
        )
        self.progress.pack(fill="x", pady=(0, 14))
        self.next_btn.state(["disabled"])

        ctl = ttk.Frame(self.form_holder)
        ctl.pack(fill="x")
        ttk.Button(ctl, text="Pausa", command=self.pause_crawl).pack(side="left", padx=(0, 8))
        ttk.Button(ctl, text="Återuppta", command=self.resume_crawl).pack(side="left", padx=(0, 8))
        ttk.Button(ctl, text="Avbryt", command=self.cancel_crawl).pack(side="left")
        ttk.Button(ctl, text="Öppna logg", command=self.open_run_log).pack(side="left", padx=(8, 0))

    def render_downloading(self):
        ttk.Label(self.form_holder, text="Hämtar sidor och filer", font=("SF Pro Display", 14, "bold")).pack(anchor="w", pady=(0, 12))
        self.download_progress_var = tk.StringVar(value="Förbereder hämtning…")
        ttk.Label(self.form_holder, textvariable=self.download_progress_var).pack(anchor="w", pady=(0, 14))
        self.download_progress = ttk.Progressbar(self.form_holder, mode="determinate", maximum=1)
        self.download_progress.pack(fill="x", pady=(0, 14))
        self.back_btn.state(["disabled"])
        self.next_btn.state(["disabled"])
        self.next_btn.config(text="Hämtar…")

    def render_preview(self):
        ttk.Label(self.form_holder, text="Förhandsgranskning", font=("SF Pro Display", 14, "bold")).pack(anchor="w", pady=(0, 12))
        if not self.preview_result:
            ttk.Label(self.form_holder, text="Ingen crawl är ännu genomförd.").pack(anchor="w")
            return

        page_count = len(self.preview_result.pages)
        file_count = len(self.preview_result.linked_files)
        total_bytes = self.preview_result.estimated_bytes
        external = ", ".join(sorted(self.preview_result.external_domains)) or "Ingen"
        file_types = ", ".join(f"{k}: {v}" for k, v in self.preview_result.file_types.items()) or "Inga filer"

        summary = (
            f"Webbsidor: {page_count}\n"
            f"Länkade filer: {file_count}\n"
            f"Filtyper: {file_types}\n"
            f"Uppskattad datamängd: {total_bytes / (1024 * 1024):.2f} MB\n"
            f"Upptäckta domäner/subdomäner ({len(self.preview_result.external_domains)}): {external}"
        )
        ttk.Label(self.form_holder, text=summary, justify="left", anchor="w").pack(anchor="w")

        self.next_btn.config(text="Välj innehåll")

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Välj målmapp")
        if folder:
            self.folder_var.set(folder)
            self.config["target_dir"] = folder

    def next_step(self):
        if self.current_step == 0:
            self.config["target_dir"] = self.folder_var.get()
            self.current_step += 1
            self.render_step()
            return

        if self.current_step == 1:
            self.config["start_url"] = self.url_var.get().strip()
            if not self.config["start_url"]:
                messagebox.showerror("Saknas", "Ange en URL eller domän.")
                return
            self.current_step += 1
            self.render_step()
            return

        if self.current_step == 2:
            try:
                max_pages = self._parse_optional_limit(
                    self.max_pages_var.get(), self.unlimited_pages_var.get()
                )
                max_depth = int(self.max_depth_var.get())
                max_file_size_mb = self._parse_optional_limit(
                    self.max_size_var.get(), self.unlimited_size_var.get()
                )
                if max_depth < 0:
                    raise ValueError("Crawl depth cannot be negative")
            except ValueError:
                messagebox.showerror(
                    "Felaktigt värde",
                    "Ange positiva gränser eller välj obegränsat. Crawl-djup får inte vara negativt.",
                )
                return
            self.config["max_pages"] = max_pages
            self.config["max_depth"] = max_depth
            self.config["max_file_size_mb"] = max_file_size_mb
            self.current_step += 1
            self.start_crawl()
            self.render_step()
            return

        if self.current_step == 4:
            self.current_step += 1
            self.next_btn.config(text="Starta hämtning")
            self.render_step()
            return

        if self.current_step == 5:
            self.config["mode"] = self.mode_var.get()
            self.download_material()
            return

    def prev_step(self):
        if self.current_step > 0:
            self.current_step -= 1
            self.next_btn.config(text="Nästa")
            self.render_step()

    def start_crawl(self):
        self.status_var.set("Genomsöker webbplatsen…")
        settings = CrawlSettings(
            max_pages=self.config["max_pages"],
            max_depth=self.config["max_depth"],
            max_file_size_mb=self.config["max_file_size_mb"],
            include_files=False,
            include_text=True,
        )
        site_output_dir = self._site_output_dir()
        site_output_dir.mkdir(parents=True, exist_ok=True)
        self.crawler = SiteCrawler(
            self.config["start_url"],
            DirectoryStorage(site_output_dir),
            settings,
            progress_callback=lambda count, queued, current_url: self.after(
                0,
                self._update_crawl_progress,
                count,
                queued,
                current_url,
            ),
        )
        self.crawl_thread = threading.Thread(target=self._crawl_worker, daemon=True)
        self.crawl_thread.start()

    def _site_output_dir(self) -> Path:
        return Path(self.config["target_dir"]) / domain_folder_name(self.config["start_url"])

    def _domain_output_dir(self, url: str) -> Path:
        site_output_dir = self._site_output_dir()
        if self.crawler is None or extract_base_domain(url) == self.crawler.base_domain:
            return site_output_dir
        return site_output_dir / domain_folder_name(url)

    def _crawl_worker(self):
        try:
            result = self.crawler.crawl()
            self.preview_result = result
            self.crawler.log_event("APP huvudcrawl färdig; visar resultat")
            self.after(0, lambda: self._finish_crawl())
        except Exception as exc:
            self.crawler.log_event(f"APP huvudcrawl fel={exc!r}")
            self.after(0, self._crawl_failed, str(exc))

    def _finish_crawl(self):
        if self.crawler is not None:
            self.preview_result = self.crawler.result
            external_domains = sorted(
                {extract_base_domain(url) for url in self.preview_result.external_pages}
            )
            if self.preview_result.status == "complete" and external_domains:
                domains = ", ".join(domain_folder_name(domain) for domain in external_domains)
                include_external = messagebox.askyesno(
                    "Externa domäner hittades",
                    f"Vill du även hämta sidor från dessa externa domäner?\n\n{domains}\n\n"
                    "Varje domän sparas i en egen undermapp.",
                )
                if include_external:
                    self.status_var.set("Genomsöker valda externa domäner…")
                    self.progress_var.set("Söker externa sidor…")
                    self.crawler.progress_callback = lambda count, queued, current_url: self.after(
                        0,
                        self._update_crawl_progress,
                        count,
                        queued,
                        current_url,
                    )
                    self.crawl_thread = threading.Thread(
                        target=self._external_crawl_worker,
                        daemon=True,
                    )
                    self.crawl_thread.start()
                    return
            self._show_preview()

    def _update_crawl_progress(self, page_count: int, queued_count: int, current_url: str):
        if self.current_step != 3:
            return
        if self.config["max_pages"] is None:
            maximum = max(1, page_count + queued_count)
            count_label = f"{page_count} sidor klara · obegränsat"
        else:
            maximum = max(1, self.config["max_pages"])
            count_label = f"{page_count} av {maximum} sidor klara"
        self.progress.configure(maximum=maximum, value=min(page_count, maximum))
        if current_url:
            parsed = urlsplit(current_url)
            current_label = f"Hämtar {parsed.netloc}{parsed.path}"
            if len(current_label) > 88:
                current_label = current_label[:85] + "…"
            self.progress_var.set(
                f"{current_label} · {count_label} · {queued_count} väntar"
            )
        else:
            self.progress_var.set(f"{count_label} · {queued_count} väntar")

    def _external_crawl_worker(self):
        try:
            self.crawler.crawl_external_domains()
            self.after(0, self._finish_external_crawl)
        except Exception as exc:
            self.crawler.log_event(f"APP extern crawl fel={exc!r}")
            self.after(0, self._crawl_failed, str(exc))

    def _finish_external_crawl(self):
        skipped_domains = sorted(self.crawler.result.skipped_external_domains)
        if skipped_domains:
            reasons = []
            if self.config["max_pages"] is not None:
                reasons.append("max antal sidor")
            if self.config["max_file_size_mb"] is not None:
                reasons.append("max total storlek")
            reason = " eller ".join(reasons) or "en inställd gräns"
            messagebox.showwarning(
                "Externa domäner hoppades över",
                f"{', '.join(skipped_domains)} hämtades inte eftersom {reason} redan var nådd. "
                "Öka gränsen eller välj obegränsat för att hämta dem.",
            )
        self._show_preview()

    def _show_preview(self):
        if self.crawler is not None:
            self.preview_result = self.crawler.result
            self.current_step = 5
            self.next_btn.config(text="Ladda ner")
            self.status_var.set("Genomsökning klar. Granska resultatet innan nedladdning.")
            self.render_step()

    def _crawl_failed(self, error: str):
        self.status_var.set(f"Fel: {error}")
        messagebox.showerror("Kunde inte genomsöka webbplatsen", error)
        self.current_step = 1
        self.render_step()

    def pause_crawl(self):
        if self.crawler:
            self.crawler.pause()
            self.status_var.set("Genomsökning pausad.")

    def resume_crawl(self):
        if self.crawler:
            self.crawler.resume()
            self.status_var.set("Genomsökning återupptagen.")

    def cancel_crawl(self):
        if self.crawler:
            self.crawler.cancel()
            self.status_var.set("Genomsökning avbruten.")

    def open_run_log(self):
        log_path = self._site_output_dir() / "webgrabber.log"
        if not log_path.is_file():
            messagebox.showinfo("Logg saknas", "Loggfilen skapas när genomsökningen startar.")
            return
        try:
            subprocess.Popen(["open", str(log_path)])
        except OSError as exc:
            messagebox.showerror("Kunde inte öppna loggen", str(exc))

    def _reset_wizard(self):
        self.config.update(
            start_url="",
            mode="text",
            max_pages=200,
            max_depth=3,
            max_file_size_mb=50,
        )
        self.crawler = None
        self.crawl_thread = None
        self.download_thread = None
        self.current_step = 0
        self.preview_result = None
        self.next_btn.config(text="Nästa")
        self.status_var.set("Redo för att starta.")
        self.render_step()

    def _create_page_pdfs(self, output_dir: Path) -> list[Path]:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate

        font_name = "Helvetica"
        font_path = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
        if font_path.is_file():
            font_name = "WebGrabberUnicode"
            if font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
        style = ParagraphStyle(
            "WebGrabberBody",
            fontName=font_name,
            fontSize=10,
            leading=14,
        )

        pages_dir = output_dir / "pages"
        pdf_dir = output_dir / "pdf"
        created: list[Path] = []
        for source in sorted(pages_dir.rglob("*.html")):
            soup = BeautifulSoup(source.read_text(encoding="utf-8"), "html.parser")
            for element in soup(("script", "style", "noscript")):
                element.decompose()
            text = soup.get_text(separator="\n", strip=True)
            if not text:
                continue

            destination = pdf_dir / source.relative_to(pages_dir).with_suffix(".pdf")
            destination.parent.mkdir(parents=True, exist_ok=True)
            document = SimpleDocTemplate(str(destination), pagesize=A4, title=source.stem)
            document.build([Paragraph(escape(line), style) for line in text.splitlines() if line.strip()])
            created.append(destination)
        return created

    def download_material(self):
        if self.crawler is None:
            return
        result = self.crawler.result
        output_dir = self._site_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        if not result.pages and not result.linked_files:
            messagebox.showinfo("Inget att ladda ner", "Det fanns inget material att hämta.")
            try:
                subprocess.Popen(["open", str(output_dir)])
            except OSError as exc:
                messagebox.showerror("Kunde inte öppna mappen", str(exc))
            self._reset_wizard()
            return

        self.current_step = 6
        self.render_step()
        self.download_thread = threading.Thread(target=self._download_worker, daemon=True)
        self.download_thread.start()

    def _update_download_progress(self, completed: int, total: int):
        if self.current_step != 6:
            return
        self.download_progress.configure(maximum=max(1, total), value=completed)
        self.download_progress_var.set(f"Hämtat {completed} av {total} sidor")

    def _download_worker(self):
        result = self.crawler.result
        output_dir = self._site_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        if self.config.get("mode", "text") == "text_files":
            result = self.crawler.download_files()
        pages = sorted(result.pages)
        self.crawler.log_event(f"DOWNLOAD start pages={len(pages)} output={output_dir}")
        self.after(0, self._update_download_progress, 0, len(pages))
        download_errors: list[str] = []
        for completed, page in enumerate(pages, start=1):
            parsed = urlsplit(page)
            relative_path = parsed.path.strip("/") or "index.html"
            if not relative_path.endswith(".html"):
                relative_path = f"{relative_path}.html"
            destination = self._domain_output_dir(page) / "pages" / relative_path
            if destination.is_file():
                self.crawler.log_event(f"DOWNLOAD sida redo url={page} path={destination}")
            else:
                error = f"{page}: sidan saknas i den sparade crawl-datan"
                download_errors.append(error)
                self.crawler.log_event(f"DOWNLOAD sida saknas url={page} path={destination}")
            self.after(0, self._update_download_progress, completed, len(pages))

        try:
            DirectoryStorage(output_dir).write_json("index.json", build_index(result))
        except Exception as exc:
            self.after(0, self._download_failed, str(exc))
            return
        pdf_count = 0
        try:
            domains = {result.base_domain}
            domains.update(extract_base_domain(page) for page in result.pages)
            for domain in sorted(domains):
                domain_dir = (
                    output_dir
                    if domain == result.base_domain
                    else output_dir / domain_folder_name(domain)
                )
                self.crawler.log_event(f"PDF start domain={domain} output={domain_dir}")
                pdf_count += len(self._create_page_pdfs(domain_dir))
        except Exception as exc:
            download_errors.append(f"PDF-export: {exc}")
            self.crawler.log_event(f"PDF fel={exc!r}")
        self.crawler.log_event(
            f"DOWNLOAD slut pages={len(pages)} pdf={pdf_count} errors={len(download_errors)}"
        )
        self.after(0, self._download_finished, pdf_count, download_errors)

    def _download_failed(self, error: str):
        if self.crawler is not None:
            self.crawler.log_event(f"DOWNLOAD avbruten error={error}")
        self.status_var.set(f"Fel: {error}")
        messagebox.showerror("Nedladdningen misslyckades", error)
        self.current_step = 5
        self.render_step()

    def _download_finished(self, pdf_count: int, errors: list[str]):
        if pdf_count:
            messagebox.showinfo("PDF-filer klara", f"{pdf_count} PDF-filer skapades.")
        if errors:
            messagebox.showwarning(
                "Hämtningen slutfördes med fel",
                "Vissa sidor kunde inte hämtas:\n\n" + "\n".join(errors[:10]),
            )
        output_dir = self._site_output_dir()
        messagebox.showinfo("Nedladdning klar", f"Material sparades i {output_dir}")
        try:
            subprocess.Popen(["open", str(output_dir)])
        except OSError as exc:
            messagebox.showerror("Kunde inte öppna mappen", str(exc))
        self._reset_wizard()


def main():
    app = WebGrabberApp()
    app.mainloop()
