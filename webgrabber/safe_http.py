from __future__ import annotations

import ipaddress
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import dns.exception
import dns.resolver
import requests
import urllib3
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.util import Timeout
from urllib3.util.connection import create_connection as urllib3_create_connection

DNS_LOOKUP_SECONDS = 1.0


class UnsafeTargetError(requests.RequestException):
    pass


class _PinnedConnectionMixin:
    def __init__(self, host: str, *args, pinned_ip: str, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(host, *args, **kwargs)

    def _new_conn(self):
        try:
            return urllib3_create_connection(
                (self.pinned_ip, self.port),
                self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except OSError as exc:
            raise urllib3.exceptions.NewConnectionError(self, str(exc)) from exc


class _PinnedHTTPConnection(_PinnedConnectionMixin, HTTPConnection):
    pass


class _PinnedHTTPSConnection(_PinnedConnectionMixin, HTTPSConnection):
    pass


class _PinnedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection


class _PinnedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


class SafeHTTPClient:
    def __init__(
        self,
        max_redirects: int = 5,
        max_connect_timeout: float = 2.0,
        max_read_timeout: float = 1.0,
    ):
        self.max_redirects = max_redirects
        self.max_connect_timeout = max_connect_timeout
        self.max_read_timeout = max_read_timeout

    @staticmethod
    def _resolve_public_addresses(host: str, port: int) -> list[str]:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            resolver = dns.resolver.Resolver(configure=True)
            resolver.timeout = DNS_LOOKUP_SECONDS / 2
            deadline = time.monotonic() + DNS_LOOKUP_SECONDS
            addresses = []
            for record_type in ("A", "AAAA"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    answer = resolver.resolve(
                        host,
                        record_type,
                        lifetime=remaining,
                        raise_on_no_answer=False,
                    )
                except dns.resolver.NoAnswer:
                    continue
                except dns.resolver.NXDOMAIN as exc:
                    if not addresses:
                        raise requests.ConnectionError(f"DNS name does not exist: {host}") from exc
                    break
                except dns.exception.Timeout as exc:
                    if not addresses:
                        raise requests.Timeout(f"DNS lookup timed out for {host}") from exc
                    break
                except dns.resolver.NoNameservers as exc:
                    if not addresses:
                        raise requests.ConnectionError(f"DNS lookup failed for {host}") from exc
                    break
                if answer.rrset is not None:
                    addresses.extend(record.address for record in answer)
            addresses = list(dict.fromkeys(addresses))
        else:
            addresses = [str(literal)]

        if not addresses:
            raise UnsafeTargetError(f"No IP addresses found for {host}")
        for address in addresses:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError as exc:
                raise UnsafeTargetError(f"Invalid DNS result for {host}") from exc
            if not parsed.is_global:
                raise UnsafeTargetError(f"Non-public network address rejected for {host}")
        return addresses

    @classmethod
    def _validate_url(cls, url: str) -> tuple[str, int, list[str], str]:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise UnsafeTargetError("Only HTTP and HTTPS URLs are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeTargetError("URLs containing credentials are not allowed")
        host = (parsed.hostname or "").rstrip(".").lower()
        if not host:
            raise UnsafeTargetError("URL must include a hostname")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise UnsafeTargetError("URL has an invalid hostname") from exc
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")) or host == "home.arpa":
            raise UnsafeTargetError("Local and reserved hostnames are not allowed")
        try:
            port = parsed.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise UnsafeTargetError("URL has an invalid port") from exc
        if port not in {80, 443}:
            raise UnsafeTargetError("Only standard HTTP and HTTPS ports are allowed")
        addresses = cls._resolve_public_addresses(host, port)
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        return host, port, addresses, path

    @staticmethod
    def _request_once(
        scheme: str,
        host: str,
        port: int,
        address: str,
        path: str,
        headers: dict[str, str],
        timeout: tuple[float, float],
    ):
        pool_type = _PinnedHTTPSPool if scheme == "https" else _PinnedHTTPPool
        pool_options = {
            "pinned_ip": address,
            "maxsize": 1,
            "block": True,
        }
        if scheme == "https":
            pool_options["cert_reqs"] = "CERT_REQUIRED"
        pool = pool_type(host, port=port, **pool_options)
        try:
            response = pool.urlopen(
                "GET",
                path,
                headers=headers,
                redirect=False,
                retries=False,
                timeout=Timeout(connect=timeout[0], read=timeout[1]),
                preload_content=False,
            )
            response._webgrabber_pool = pool
            return response
        except urllib3.exceptions.HTTPError as exc:
            pool.close()
            raise requests.ConnectionError(str(exc)) from exc

    def get(self, url: str, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        timeout = kwargs.get("timeout", (5, 15))
        if not isinstance(timeout, tuple):
            timeout = (timeout, timeout)
        timeout = (
            min(float(timeout[0]), self.max_connect_timeout),
            min(float(timeout[1]), self.max_read_timeout),
        )
        deadline = time.monotonic() + sum(timeout)
        current_url = url

        for redirect_count in range(self.max_redirects + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise requests.Timeout("HTTP request time budget exceeded")
            host, port, addresses, path = self._validate_url(current_url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise requests.Timeout("HTTP request time budget exceeded")
            scheme = urlsplit(current_url).scheme.lower()
            connect_timeout = min(timeout[0], remaining * 0.4)
            read_timeout = min(timeout[1], remaining - connect_timeout)
            response = self._request_once(
                scheme,
                host,
                port,
                addresses[0],
                path,
                headers,
                (connect_timeout, read_timeout),
            )
            location = response.headers.get("Location")
            if response.status not in {301, 302, 303, 307, 308} or not location:
                return _SafeResponse(response, deadline)
            self._close_response(response)
            if redirect_count == self.max_redirects:
                raise requests.TooManyRedirects("Too many redirects")
            current_url = urljoin(current_url, location)

        raise requests.TooManyRedirects("Too many redirects")

    @staticmethod
    def _close_response(response) -> None:
        response.close()
        pool = getattr(response, "_webgrabber_pool", None)
        if pool is not None:
            pool.close()


class _SafeResponse:
    def __init__(self, response, deadline: float):
        self._response = response
        self._deadline = deadline
        self.status_code = response.status
        self.headers = response.headers

    def iter_content(self, chunk_size: int = 64 * 1024):
        try:
            for chunk in self._response.stream(chunk_size, decode_content=True):
                if time.monotonic() >= self._deadline:
                    return
                yield chunk
        finally:
            self.close()

    @property
    def text(self):
        body = bytearray()
        try:
            for chunk in self._response.stream(16 * 1024, decode_content=True):
                if time.monotonic() >= self._deadline:
                    break
                if len(body) + len(chunk) > 256 * 1024:
                    body.extend(chunk[: 256 * 1024 - len(body)])
                    break
                body.extend(chunk)
        finally:
            self.close()
        return bytes(body).decode("utf-8", errors="replace")

    def close(self):
        SafeHTTPClient._close_response(self._response)