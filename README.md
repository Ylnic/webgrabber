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

This repository now includes a simple web app intended to run under a subdomain such as `webgrabber.example.com`.

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

## Build a macOS installer

On macOS, double-click `build_macos_installer.command`. The script builds a self-contained `WebGrabber.app` and a drag-and-drop disk image under `dist/`. Open the generated `.dmg` and drag `WebGrabber.app` to `Applications`.

The generated app is not signed or notarized. macOS may require Control-clicking the app and choosing **Open** the first time it is launched.

## Notes

This project is intentionally built as a local, offline-friendly scraper for public content only. It does not bypass access restrictions or circumvent anti-bot protections.

The hosted version should run on a dedicated subdomain and be configured behind HTTPS, with explicit DNS records and a reverse proxy such as NGINX.
