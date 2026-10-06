# WebGrabber

WebGrabber is a site-crawling tool designed for two deployment modes:

- a local macOS desktop app for offline use
- a hosted web version running on a subdomain such as `webgrabber.example.com`

The project keeps the crawler logic in Python and exposes it through a desktop GUI or a web interface depending on the deployment target.

## Features

- Select a local destination folder or web output target
- Enter a website URL or domain
- Detect the base domain automatically
- Crawl internal pages and optionally linked files
- Respect robots.txt and basic crawl limits
- Pause, resume, or cancel crawling
- Preview counts before download
- Save a local index of discovered pages and files
- Run the same crawler as a hosted web service behind a subdomain

## Requirements

- Python 3.11+
- Python with Tk support (only needed when running the desktop app from source)
- macOS for the desktop build
- A domain and DNS configuration for the hosted web version

## Local desktop setup

```bash
cd "/Users/niclas/Documents/-= Projekt =-/WebGrabber"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

You can also double-click `start_webgrabber.command`. It creates `.venv` if needed, installs missing project dependencies, and starts the app. It does not install Homebrew or remove an existing environment.

## Hosted web setup on a subdomain

The repository contains two separate web deployment targets: an optional Flask app for Python application hosts, and a CGI package for Loopia UNIX. The Loopia package does not use Flask or require a permanent server process. Both web versions analyze first, show page count, crawl depth, internal page links, linked files, file types and discovered domains/subdomains, then let you choose the page/depth limits and whether to fetch **enbart text** or **allt innehåll** before ZIP generation.

### 1. Configure the domain

Create a DNS record pointing to the server host:

- `webgrabber.example.com` -> your server IP or load balancer
- `www.webgrabber.example.com` -> optional CNAME to the same host

The placeholder configuration is stored in `.env.example`:

```bash
WEBGRABBER_HOST=webgrabber.example.com
WEBGRABBER_PORT=8000
ALLOWED_HOSTS=webgrabber.example.com,www.webgrabber.example.com
```

### 2. Install web dependencies

```bash
cd "/Users/niclas/Documents/-= Projekt =-/WebGrabber"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-web.txt
```

### 3. Run the web app

```bash
export $(grep -v '^#' .env.example | xargs)
python -m webgrabber.web_app
```

Then open `http://webgrabber.example.com` in a browser or the configured host.

### 4. Optional reverse proxy config

A sample NGINX config is available at `deploy/nginx/webgrabber.example.com.conf.example`.

## Loopia UNIX CGI deployment

For `webgrabber.ylnic.se` on Loopia UNIX hosting, build and upload the standalone CGI package:

```sh
sh deploy/loopia/build_deploy.sh
```

The ZIP is written to `deploy/loopia/dist/webgrabber-loopia.zip`. Extract it locally and upload its `public_html/`, `app/`, and `private/` directories as siblings using FTPS. Set the subdomain document root to `public_html`, keep `app` and `private` outside it, and set `public_html/index.py` to **CHMOD 755**. The CGI script uses `/usr/local/bin/python3`; Python dependencies are vendored into `app/vendor` by the build script.

After upload, verify `https://webgrabber.ylnic.se/health` before submitting a crawl. Loopia uses two bounded CGI requests: one to analyze and preview the site, then one to crawl the selected page/depth range and create the ZIP. The analysis and selected crawl are each limited to 12 seconds and 5 MiB of response data; the current application limits are 200 pages, 200 linked files, depth 5, 2 MiB per response, and a 6 MiB ZIP. Actual results can be smaller when a time or data limit is reached. See `deploy/loopia/README.md` for cleanup and CGI timeout details.

## Build a macOS installer

On macOS, double-click `build_macos_installer.command`. The script builds a self-contained `WebGrabber.app` and a drag-and-drop disk image under `dist/`. Open the generated `.dmg` and drag `WebGrabber.app` to `Applications`.

The generated app is not signed or notarized. macOS may require Control-clicking the app and choosing **Open** the first time it is launched.

## Notes

This project is intentionally built as a local, offline-friendly scraper for public content only. It does not bypass access restrictions or circumvent anti-bot protections.

Hosted deployments should use HTTPS and the provider's supported runtime configuration. The Loopia CGI package is the option for UNIX web hosting without a resident application server.
