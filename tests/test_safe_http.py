from types import SimpleNamespace

import pytest

from webgrabber.safe_http import SafeHTTPClient, UnsafeTargetError, _PinnedHTTPConnection


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.1.1", "::1", "fc00::1"],
)
def test_rejects_non_public_literal_addresses(address):
    formatted = f"[{address}]" if ":" in address else address
    with pytest.raises(UnsafeTargetError):
        SafeHTTPClient._validate_url(f"http://{formatted}/")


def test_rejects_dns_answer_containing_private_address(monkeypatch):
    class FakeResolver:
        def __init__(self, configure=True):
            self.timeout = None

        def resolve(self, host, record_type, **kwargs):
            if record_type == "A":
                records = [
                    SimpleNamespace(address="93.184.216.34"),
                    SimpleNamespace(address="127.0.0.1"),
                ]
                return FakeAnswer(records)
            return FakeAnswer(None)

    class FakeAnswer:
        def __init__(self, records):
            self.rrset = records

        def __iter__(self):
            return iter(self.rrset or ())

    monkeypatch.setattr("webgrabber.safe_http.dns.resolver.Resolver", FakeResolver)

    with pytest.raises(UnsafeTargetError):
        SafeHTTPClient._validate_url("https://example.com/")


def test_pinned_connection_uses_validated_ip(monkeypatch):
    connected = []
    expected_socket = object()
    monkeypatch.setattr(
        "webgrabber.safe_http.urllib3_create_connection",
        lambda address, *args, **kwargs: connected.append(address) or expected_socket,
    )
    connection = _PinnedHTTPConnection("public.example", pinned_ip="93.184.216.34", port=80)

    assert connection._new_conn() is expected_socket
    assert connected == [("93.184.216.34", 80)]


def test_redirect_to_private_address_is_rejected_before_second_request(monkeypatch):
    requests_seen = []

    def request_once(scheme, host, port, address, path, headers, timeout):
        requests_seen.append((host, address, path))
        return SimpleNamespace(
            status=302,
            headers={"Location": "http://127.0.0.1/admin"},
            close=lambda: None,
        )

    client = SafeHTTPClient()
    monkeypatch.setattr(client, "_request_once", request_once)

    with pytest.raises(UnsafeTargetError):
        client.get("https://93.184.216.34/")

    assert requests_seen == [("93.184.216.34", "93.184.216.34", "/")]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "http://user@example.com/",
        "http://localhost/",
        "http://printer.local/",
    ],
)
def test_rejects_unsupported_schemes_and_credentials(url):
    with pytest.raises(UnsafeTargetError):
        SafeHTTPClient._validate_url(url)