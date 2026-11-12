"""Bounded HTTPS downloads with approved hosts and public-IP-pinned connections."""

import ipaddress
import socket
import time
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Thread
from urllib.parse import urljoin, urlsplit

from dietary_rag.settings import FetchSettings


class DownloadError(ValueError):
    def __init__(
        self, code: str, message: str, *, status_code=None, resolved_url=None, content_type=None
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.resolved_url = resolved_url
        self.content_type = content_type


def validate_url(url: str, approved_hosts: frozenset[str]) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise DownloadError("unsafe_destination", "Malformed destination URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in approved_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise DownloadError(
            "unsafe_destination", "Only approved HTTPS hosts on port 443 may be fetched"
        )


def public_addresses(host: str, port: int, resolver=socket.getaddrinfo) -> tuple[str, ...]:
    addresses = tuple(
        dict.fromkeys(info[4][0] for info in resolver(host, port, type=socket.SOCK_STREAM))
    )
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise DownloadError("unsafe_destination", "Destination DNS includes a nonpublic address")
    return addresses


def _bounded_dns(host: str, port: int, timeout: float | None) -> tuple[str, ...]:
    if timeout is None:
        return public_addresses(host, port)
    result = Queue(maxsize=1)

    def resolve():
        try:
            result.put((public_addresses(host, port), None))
        except Exception as exc:
            result.put((None, exc))

    # The OS resolver has no portable per-call timeout. A daemon worker lets the
    # caller honor its deadline without waiting for an unresponsive resolver.
    Thread(target=resolve, daemon=True).start()
    try:
        addresses, error = result.get(timeout=max(0, timeout))
    except Empty as exc:
        raise DownloadError("request_timeout", "DNS resolution exceeded its deadline") from exc
    if error is not None:
        raise error
    return addresses


def _transport():
    # Imports are lazy: settings/registry/core-only imports do not load network dependencies.
    try:
        import httpcore
        import httpx
    except ImportError as exc:
        raise RuntimeError("Install requirements/acquisition.lock to fetch sources") from exc

    class PublicBackend:
        def __init__(self):
            self.backend = httpcore.SyncBackend()

        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            # Resolve once, reject mixed public/private DNS, connect to that exact public IP.
            # httpcore still uses the original host for the Host header and TLS SNI/verification.
            deadline = None if timeout is None else time.monotonic() + timeout
            addresses = _bounded_dns(host, port, timeout)
            last_error = None
            for address in addresses:
                try:
                    remaining = None if deadline is None else max(0, deadline - time.monotonic())
                    return self.backend.connect_tcp(
                        address, port, remaining, local_address, socket_options
                    )
                except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                    last_error = exc
            raise last_error

        def connect_unix_socket(self, *args, **kwargs):
            raise DownloadError("unsafe_destination", "Unix socket destinations are not supported")

        def sleep(self, seconds):
            time.sleep(seconds)

    class Stream(httpx.SyncByteStream):
        def __init__(self, response):
            self.response = response

        def __iter__(self):
            yield from self.response.iter_stream()

        def close(self):
            self.response.close()

    class PublicTransport(httpx.BaseTransport):
        def __init__(self):
            self.pool = httpcore.ConnectionPool(
                ssl_context=httpx.create_ssl_context(),
                network_backend=PublicBackend(),
                max_connections=2,
            )

        def handle_request(self, request):
            response = self.pool.handle_request(
                httpcore.Request(
                    method=request.method,
                    url=httpcore.URL(
                        scheme=request.url.raw_scheme,
                        host=request.url.raw_host,
                        port=request.url.port,
                        target=request.url.raw_path,
                    ),
                    headers=request.headers.raw,
                    content=request.stream,
                    extensions=request.extensions,
                )
            )
            return httpx.Response(
                response.status,
                headers=response.headers,
                stream=Stream(response),
                extensions=response.extensions,
            )

        def close(self):
            self.pool.close()

    return PublicTransport()


@dataclass(frozen=True)
class Download:
    content: bytes
    resolved_url: str
    content_type: str | None
    attempts: int
    status_code: int = 200
    etag: str | None = None
    last_modified: str | None = None


class Fetcher:
    def __init__(self, settings: FetchSettings, *, transport=None, sleep=time.sleep):
        self.settings = settings
        self.transport = transport  # Test injection only; the default pins safe DNS connections.
        self.sleep = sleep

    def fetch(
        self,
        url: str,
        approved_hosts: frozenset[str],
        max_bytes: int,
        *,
        validators: dict[str, str] | None = None,
    ) -> Download:
        try:
            import httpcore
            import httpx
        except ImportError as exc:
            raise RuntimeError("Install requirements/acquisition.lock to fetch sources") from exc
        validate_url(url, approved_hosts)
        if validators and any(
            k not in {"If-None-Match", "If-Modified-Since"}
            or not isinstance(v, str)
            or not 0 < len(v) <= 1024
            or any(ord(c) < 32 or ord(c) >= 127 for c in v)
            for k, v in validators.items()
        ):
            raise DownloadError(
                "invalid_validators", "Only bounded conditional request headers are accepted"
            )
        with httpx.Client(
            transport=self.transport or _transport(),
            timeout=self.settings.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "dietary-guidance-rag-chatbot/0.1 (educational source review)",
                "Accept-Encoding": "identity",
            },
        ) as client:
            for attempt in range(self.settings.max_retries + 1):
                current = url
                deadline = time.monotonic() + self.settings.timeout_seconds
                try:
                    for redirect in range(self.settings.max_redirects + 1):
                        validate_url(current, approved_hosts)
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise DownloadError(
                                "request_timeout",
                                "Download exceeded its attempt deadline",
                                resolved_url=current,
                            )
                        # Validators belong to the exact URL; never forward them to redirects.
                        headers = validators if current == url else {}
                        with client.stream(
                            "GET", current, timeout=remaining, headers=headers
                        ) as response:
                            mime = response.headers.get("content-type")
                            if response.status_code in (301, 302, 303, 307, 308):
                                if (
                                    redirect == self.settings.max_redirects
                                    or "location" not in response.headers
                                ):
                                    raise DownloadError(
                                        "redirect_limit",
                                        "Redirect chain is incomplete or too long",
                                        status_code=response.status_code,
                                        resolved_url=str(response.url),
                                    )
                                current = urljoin(str(response.url), response.headers["location"])
                                validate_url(current, approved_hosts)
                                continue
                            if response.status_code == 304:
                                if not headers:
                                    raise DownloadError(
                                        "unexpected_not_modified", "Unconditional response was 304"
                                    )
                                return Download(
                                    b"",
                                    str(response.url),
                                    mime,
                                    attempt + 1,
                                    304,
                                    response.headers.get("etag"),
                                    response.headers.get("last-modified"),
                                )
                            if response.status_code != 200:
                                raise DownloadError(
                                    "http_error",
                                    f"HTTP {response.status_code}",
                                    status_code=response.status_code,
                                    resolved_url=str(response.url),
                                    content_type=mime,
                                )
                            encoding = (
                                response.headers.get("content-encoding", "identity").lower().strip()
                            )
                            if encoding != "identity":
                                raise DownloadError(
                                    "unsupported_content_encoding",
                                    "Server ignored the identity-encoding request; compressed HTTP bodies are not accepted",
                                    status_code=200,
                                    resolved_url=str(response.url),
                                    content_type=mime,
                                )
                            length = response.headers.get("content-length")
                            if length and length.isdigit() and int(length) > max_bytes:
                                raise DownloadError(
                                    "size_limit",
                                    "Response exceeds configured byte limit",
                                    status_code=200,
                                    resolved_url=str(response.url),
                                    content_type=mime,
                                )
                            chunks = []
                            size = 0
                            for chunk in response.iter_raw():
                                size += len(chunk)
                                if size > max_bytes:
                                    raise DownloadError(
                                        "size_limit",
                                        "Stream exceeds configured byte limit",
                                        status_code=200,
                                        resolved_url=str(response.url),
                                        content_type=mime,
                                    )
                                if time.monotonic() > deadline:
                                    raise DownloadError(
                                        "request_timeout",
                                        "Download exceeded its attempt deadline",
                                        resolved_url=str(response.url),
                                    )
                                chunks.append(chunk)
                            if time.monotonic() > deadline:
                                raise DownloadError(
                                    "request_timeout",
                                    "Download exceeded its attempt deadline",
                                    resolved_url=str(response.url),
                                )
                            return Download(
                                b"".join(chunks),
                                str(response.url),
                                mime,
                                attempt + 1,
                                200,
                                response.headers.get("etag"),
                                response.headers.get("last-modified"),
                            )
                except DownloadError as exc:
                    transient = exc.code == "request_timeout" or (
                        exc.code == "http_error"
                        and (exc.status_code == 429 or exc.status_code in (500, 502, 503, 504))
                    )
                    if not transient or attempt == self.settings.max_retries:
                        raise
                except (
                    httpx.TransportError,
                    httpcore.NetworkError,
                    httpcore.TimeoutException,
                    httpcore.ProtocolError,
                    OSError,
                ) as exc:
                    if attempt == self.settings.max_retries:
                        raise DownloadError(
                            "network_error",
                            f"Download failed: {type(exc).__name__}",
                            resolved_url=current,
                        ) from exc
                self.sleep(min(0.25 * 2**attempt, 1))
        raise DownloadError("network_error", "Download did not complete")
