# WebGrabber on Loopia UNIX

This deployment runs as Python CGI on Loopia UNIX hosting. Each request starts a short-lived Python 3.11 process; it does not start Flask, Gunicorn, or a resident worker.

## Build the upload package

From the repository root on macOS or Linux:

```sh
sh deploy/loopia/build_deploy.sh
```

The script vendors `requests`, `beautifulsoup4`, `dnspython`, `urllib3`, and their Python dependencies into the package. It selects Linux x86_64 binary wheels for CPython 3.11 even when built from macOS. Flask is intentionally not included: the CGI entrypoint does not use Flask or require a server process. The regular Flask deployment remains separate.

The output is:

```text
deploy/loopia/dist/webgrabber-loopia.zip
```

The archive contains three sibling directories:

```text
public_html/   The subdomain document root and CGI entrypoint
app/           WebGrabber Python modules and vendored dependencies
private/       Storage outside the document root
```

## Upload and configure the subdomain

1. Create or select `webgrabber.ylnic.se` in Loopia and point its document root to the uploaded `public_html` directory.
2. Extract the ZIP locally. Connect with **FTPS** and upload the `public_html`, `app`, and `private` directories so they remain siblings. Do not place `app` or `private` below `public_html`.
3. In the FTPS client, set `public_html/index.py` to **CHMOD 755**. The build script sets this mode in the ZIP too, but some FTP clients do not preserve archive permissions. `.htaccess` files should be readable, normally CHMOD 644. Keep `private` restricted to the account owner, normally CHMOD 700.
4. Ensure the account can execute `/usr/local/bin/python3` and that this is the Python 3.11 CGI interpreter on the account.
5. Enable HTTPS for the subdomain in Loopia.

The shebang is already set to:

```text
#!/usr/local/bin/python3
```

The root `.htaccess` enables CGI for `.py`, chooses `index.py` as the directory index, and maps `/health` to the health check. If URL rewriting is unavailable, use `https://webgrabber.ylnic.se/?health=1` instead.

## Verify before use

After upload and permissions are set, open:

```text
https://webgrabber.ylnic.se/health
```

Expected response:

```json
{"status":"ok","crawler":"ready","storage":"writable"}
```

This checks that CGI ran, Python dependencies imported, and the private storage location is writable. It does not start a crawl.

## CGI limits

CGI cannot keep a crawl running after its HTTP request ends. The web flow uses two bounded requests: the first analyzes pages and follows linked external domains before saving a private preview; the second repeats that crawl for the selected page/depth range and creates the ZIP. Both steps discover linked files on the main and external domains. Analysis state is stored outside the document root for up to one hour. No resident worker, polling loop, or resumed process is required.

Hard application limits are 12 seconds per request, at most 200 pages, 200 discovered/downloaded files, depth 5, 2 MiB per response, 5 MiB of response data per request, and a 6 MiB ZIP. The selected page/file counts are upper bounds: the runtime and data limits can stop either the analysis or download sooner. DNS lookup is bounded to 1 second per validation, connection timeouts are capped at 2 seconds, and socket read timeouts are capped at 1 second. The service allows one active crawl at a time and limits each client address to 6 requests per hour (up to 3 analyze-and-download runs). A limited analysis or file download is reported in the preview or ZIP manifest with its status and limit reason.

These application budgets are deliberately conservative, but the exact CGI kill timeout and outbound network policy are controlled by Loopia and may vary by account. A provider-level timeout can still terminate a CGI process before it returns; no CGI implementation can turn that into a durable background job. If `/health` works but crawl requests are terminated, ask Loopia support for the account's CGI execution limit and permitted outbound DNS/HTTP traffic. Do not raise the application budgets until that limit has been confirmed.

Each analysis uses a cryptographically random job ID and a private per-job directory. The directory is removed after the ZIP response is formed; unclaimed previews and stale `job-*` directories are cleaned after one hour on a later request.

## Source of truth and updates

Keep the GitHub repository as the source of truth. After changing crawler or CGI code, rebuild the ZIP and upload the new package over FTPS. The deployment package includes the shared `crawler.py` and its storage adapter; it does not include the macOS GUI, Flask app, or desktop bundle.