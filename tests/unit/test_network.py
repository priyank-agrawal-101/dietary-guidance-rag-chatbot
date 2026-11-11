import socket

import httpx
import pytest

from dietary_rag.ingestion.network import (
    DownloadError,
    Fetcher,
    _transport,
    public_addresses,
    validate_url,
)
from dietary_rag.settings import FetchSettings

HOSTS = frozenset({"example.gov"})
URL = "https://example.gov/food"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.gov",
        "https://other.gov",
        "https://user:pw@example.gov",
        "https://example.gov:444",
        "https://example.gov:bad",
        "https://127.0.0.1",
    ],
)
def test_only_exact_approved_https_hosts(url):
    with pytest.raises(DownloadError, match="destination|approved"):
        validate_url(url, HOSTS)


@pytest.mark.parametrize(
    "addresses",
    [["127.0.0.1"], ["::1"], ["169.254.169.254"], ["10.1.1.1"], ["8.8.8.8", "192.168.1.1"]],
)
def test_private_and_mixed_dns_is_rejected(addresses):
    def resolver(*a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses]

    with pytest.raises(DownloadError):
        public_addresses("example.gov", 443, resolver)


def fetch_with(handler, **settings):
    def streaming(request):
        response = handler(request)
        if response.is_stream_consumed:
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=httpx.ByteStream(response.content),
            )
        return response

    return Fetcher(
        FetchSettings(**settings), transport=httpx.MockTransport(streaming), sleep=lambda _: None
    )


def test_safe_relative_redirect_and_unsafe_redirect_before_request():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/food":
            return httpx.Response(302, headers={"location": "/article"})
        return httpx.Response(200, content=b"body")

    result = fetch_with(handler).fetch(URL, HOSTS, 100)
    assert result.resolved_url == "https://example.gov/article"
    assert len(calls) == 2
    calls.clear()

    def unsafe(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.test/private"})

    with pytest.raises(DownloadError) as exc:
        fetch_with(unsafe).fetch(URL, HOSTS, 100)
    assert exc.value.code == "unsafe_destination"
    assert calls == [URL]


@pytest.mark.parametrize("status", [403, 404, 405])
def test_nontransient_http_errors_do_not_retry(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=b"error page should not be read")

    with pytest.raises(DownloadError) as exc:
        fetch_with(handler).fetch(URL, HOSTS, 100)
    assert exc.value.status_code == status
    assert len(calls) == 1


def test_transient_status_and_transport_timeout_retry_bound():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        if len(calls) == 2:
            raise httpx.ReadTimeout("synthetic timeout")
        return httpx.Response(200, content=b"okay")

    assert fetch_with(handler).fetch(URL, HOSTS, 100).attempts == 3
    calls.clear()
    with pytest.raises(DownloadError) as exc:
        fetch_with(handler, max_retries=1).fetch(URL, HOSTS, 100)
    assert exc.value.code == "network_error"
    assert len(calls) == 2


def test_redirect_loop_has_a_bound():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "/loop"})

    with pytest.raises(DownloadError) as exc:
        fetch_with(handler, max_redirects=2).fetch(URL, HOSTS, 100)
    assert exc.value.code == "redirect_limit"
    assert len(calls) == 3


@pytest.mark.parametrize("headers", [{"content-length": "101"}, {}])
def test_header_and_stream_limits(headers):
    with pytest.raises(DownloadError) as exc:
        fetch_with(lambda request: httpx.Response(200, headers=headers, content=b"a" * 101)).fetch(
            URL, HOSTS, 100
        )
    assert exc.value.code == "size_limit"


def test_default_transport_connects_to_verified_ip(monkeypatch):
    import httpcore

    from dietary_rag.ingestion import network

    calls = []
    sentinel = object()

    class Backend:
        def connect_tcp(self, host, port, timeout, local_address, socket_options):
            calls.append((host, port))
            return sentinel

    monkeypatch.setattr(httpcore, "SyncBackend", Backend)
    monkeypatch.setattr(network, "public_addresses", lambda host, port: ("8.8.8.8",))
    transport = _transport()
    backend = transport.pool._network_backend
    assert backend.connect_tcp("example.gov", 443, 1) is sentinel
    assert calls == [("8.8.8.8", 443)]
    transport.close()


def test_attempt_deadline_and_dns_timeout(monkeypatch):
    from threading import Event

    from dietary_rag.ingestion import network

    values = iter([0, 0, 21])
    monkeypatch.setattr(network.time, "monotonic", lambda: next(values))
    with pytest.raises(DownloadError) as exc:
        fetch_with(lambda request: httpx.Response(200, content=b"body"), max_retries=0).fetch(
            URL, HOSTS, 100
        )
    assert exc.value.code == "request_timeout"
    finished = Event()

    def stuck_dns(host, port):
        finished.wait(1)
        return ("8.8.8.8",)

    monkeypatch.setattr(network, "public_addresses", stuck_dns)
    try:
        with pytest.raises(DownloadError) as exc:
            network._bounded_dns("example.gov", 443, 0.001)
        assert exc.value.code == "request_timeout"
    finally:
        finished.set()


def test_compressed_http_body_is_not_saved_as_original_document():
    with pytest.raises(DownloadError) as exc:
        fetch_with(
            lambda request: httpx.Response(
                200, headers={"content-encoding": "br"}, stream=httpx.ByteStream(b"compressed")
            )
        ).fetch(URL, HOSTS, 100)
    assert exc.value.code == "unsupported_content_encoding"
