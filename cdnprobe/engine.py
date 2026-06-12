"""Core measurement engine for cdnprobe.

Measures five connection phases independently:
  DNS -> TCP -> TLS -> TTFB -> Transfer

Each phase is timed with time.perf_counter() for monotonic,
high-resolution measurements.  After TLS, the existing socket is
reused for the HTTP request (h2 or HTTP/1.1) so that TTFB reflects
only application-level latency, not a redundant TCP+TLS handshake.

Public API:
    measure_provider  -- run all samples for a single CDN provider
    measure_all       -- run measurements for all providers concurrently
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse

import dns.asyncresolver
import dns.rdatatype
import h2.config
import h2.connection
import h2.events
import httpx

from cdnprobe.config import USER_AGENT
from cdnprobe.iata import enrich_pop
from cdnprobe.models import (
    MeasurementConfig,
    PoPIdentity,
    ProviderResult,
    SampleResult,
    TimingBreakdown,
)
from cdnprobe.providers import get_provider, get_provider_map
from cdnprobe.providers.base import CDNProvider
from cdnprobe.stats import aggregate_provider_stats

logger = logging.getLogger(__name__)

# Type alias for the progress callback.
# Signature: (provider_slug, sample_index, total_samples, sample_result_or_none)
ProgressCallback = Callable[[str, int, int, Optional[SampleResult]], None]


# ---------------------------------------------------------------------------
# Engine-internal HTTP result (replaces httpx.Response on the timing path)
# ---------------------------------------------------------------------------

@dataclass
class HttpResult:
    """Lightweight container for the HTTP response collected on the raw socket."""

    status_code: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    http_version: str = ""


# ---------------------------------------------------------------------------
# DNS resolution
# ---------------------------------------------------------------------------

async def _resolve_dns(
    hostname: str,
    config: MeasurementConfig,
) -> tuple[str, float]:
    """Resolve *hostname* via dnspython and return (ip, elapsed_ms).

    Respects ``config.dns_server``, ``config.ipv4_only`` and
    ``config.ipv6_only``.  Falls back from AAAA to A (or vice-versa) when
    the preferred record type yields no results.

    Note: On macOS, the system DNS cache (mDNSResponder) may cache
    responses, making DNS timing for samples 2+ artificially fast.
    Use ``--dns-server`` to bypass the OS cache for more accurate
    per-sample DNS measurements.

    Raises
    ------
    dns.exception.DNSException
        On resolution failure (caller catches and records the error).
    """
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = config.timeout

    if config.dns_server:
        resolver.nameservers = [config.dns_server]

    # Choose record type based on address-family preference.
    if config.ipv6_only:
        rdtypes = [dns.rdatatype.AAAA]
    elif config.ipv4_only:
        rdtypes = [dns.rdatatype.A]
    else:
        # Prefer A, fall back to AAAA.
        rdtypes = [dns.rdatatype.A, dns.rdatatype.AAAA]

    last_error: Exception | None = None
    for rdtype in rdtypes:
        try:
            t0 = time.perf_counter()
            answer = await resolver.resolve(hostname, rdtype)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            ip = str(answer[0])
            return ip, round(elapsed_ms, 3)
        except Exception as exc:
            last_error = exc
            continue

    # All record types failed -- propagate the last error.
    raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TCP connect
# ---------------------------------------------------------------------------

async def _measure_tcp(
    ip: str,
    port: int,
    timeout: float,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, float]:
    """Open a raw TCP connection to *ip*:*port* and return (reader, writer, ms).

    The caller is responsible for closing the writer when done.
    """
    t0 = time.perf_counter()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ip, port),
        timeout=timeout,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return reader, writer, round(elapsed_ms, 3)


# ---------------------------------------------------------------------------
# TLS upgrade
# ---------------------------------------------------------------------------

def _build_ssl_context() -> ssl.SSLContext:
    """Build a standard SSL context that validates the server certificate."""
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["h2", "http/1.1"])
    return ctx


async def _measure_tls(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    hostname: str,
    timeout: float,
) -> tuple[float, Optional[str]]:
    """Upgrade an existing TCP connection to TLS via ``start_tls``.

    Returns (elapsed_ms, tls_version_string | None).

    If ``start_tls`` is not available on the writer's transport, the
    caller should fall back to the combined TCP+TLS measurement path.
    """
    ctx = _build_ssl_context()

    t0 = time.perf_counter()
    transport = writer.transport

    # asyncio.StreamWriter.start_tls was added in Python 3.11.
    if hasattr(writer, "start_tls"):
        await asyncio.wait_for(
            writer.start_tls(ctx, server_hostname=hostname),
            timeout=timeout,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        tls_version = _extract_tls_version(writer)
        return round(elapsed_ms, 3), tls_version

    # Fallback: use the loop-level start_tls (Python 3.10 compatible).
    loop = asyncio.get_running_loop()
    new_transport = await asyncio.wait_for(
        loop.start_tls(transport, ctx, server_hostname=hostname),
        timeout=timeout,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Re-bind the writer to the new transport.
    writer._transport = new_transport  # type: ignore[attr-defined]
    tls_version = _extract_tls_version_from_transport(new_transport)
    return round(elapsed_ms, 3), tls_version


def _extract_tls_version(writer: asyncio.StreamWriter) -> Optional[str]:
    """Best-effort extraction of TLS version from writer transport."""
    return _extract_tls_version_from_transport(writer.transport)


def _extract_tls_version_from_transport(transport: object) -> Optional[str]:
    """Extract TLS version string from a transport object."""
    ssl_obj = getattr(transport, "get_extra_info", lambda _: None)("ssl_object")
    if ssl_obj is not None:
        return ssl_obj.version()
    return None


# ---------------------------------------------------------------------------
# ALPN detection
# ---------------------------------------------------------------------------

def _detect_alpn(writer: asyncio.StreamWriter) -> Optional[str]:
    """Extract the negotiated ALPN protocol from the TLS socket.

    Returns ``"h2"``, ``"http/1.1"``, or ``None``.
    """
    ssl_obj = writer.transport.get_extra_info("ssl_object")
    if ssl_obj is not None:
        return ssl_obj.selected_alpn_protocol()
    return None


# ---------------------------------------------------------------------------
# Combined TCP + TLS (fallback when start_tls is unavailable / fails)
# ---------------------------------------------------------------------------

async def _measure_tcp_tls_combined(
    ip: str,
    port: int,
    hostname: str,
    tcp_ms: float,
    timeout: float,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, float, Optional[str]]:
    """Open a single SSL connection to measure TCP+TLS together.

    TLS time is estimated by subtracting a previously measured *tcp_ms*.
    Returns (reader, writer, tls_ms, tls_version).  The caller owns the
    connection and is responsible for closing it.
    """
    ctx = _build_ssl_context()

    t0 = time.perf_counter()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ip, port, ssl=ctx, server_hostname=hostname),
        timeout=timeout,
    )
    combined_ms = (time.perf_counter() - t0) * 1000.0
    tls_ms = max(combined_ms - tcp_ms, 0.0)

    tls_version = _extract_tls_version(writer)

    return reader, writer, round(tls_ms, 3), tls_version


# ---------------------------------------------------------------------------
# HTTP/2 measurement on existing TLS socket
# ---------------------------------------------------------------------------

async def _measure_http_h2(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    hostname: str,
    path: str,
    extra_headers: dict[str, str],
    timeout: float,
) -> tuple[float, float, HttpResult]:
    """Send an HTTP/2 request on an existing TLS connection via the h2 library.

    Returns (ttfb_ms, transfer_ms, HttpResult).
    """
    config = h2.config.H2Configuration(client_side=True)
    conn = h2.connection.H2Connection(config=config)

    # Send connection preface
    conn.initiate_connection()
    writer.write(conn.data_to_send())
    await writer.drain()

    # Wait for server SETTINGS (not timed as TTFB)
    preface_data = await asyncio.wait_for(reader.read(65535), timeout=timeout)
    events = conn.receive_data(preface_data)
    writer.write(conn.data_to_send())
    await writer.drain()

    # Build headers for the GET request
    headers = [
        (":method", "GET"),
        (":path", path),
        (":scheme", "https"),
        (":authority", hostname),
        ("user-agent", USER_AGENT),
    ]
    for k, v in extra_headers.items():
        headers.append((k.lower(), v))

    # Send request — start TTFB timer
    stream_id = conn.get_next_available_stream_id()
    conn.send_headers(stream_id, headers, end_stream=True)
    t_send = time.perf_counter()
    writer.write(conn.data_to_send())
    await writer.drain()

    # Read response
    response_headers: dict[str, str] = {}
    status_code = 0
    body_chunks: list[bytes] = []
    t_first_byte: float | None = None
    stream_ended = False

    while not stream_ended:
        data = await asyncio.wait_for(reader.read(65535), timeout=timeout)
        if not data:
            break

        events = conn.receive_data(data)
        for event in events:
            if isinstance(event, h2.events.ResponseReceived):
                if t_first_byte is None:
                    t_first_byte = time.perf_counter()
                for header_name, header_value in event.headers:
                    name = header_name.decode() if isinstance(header_name, bytes) else header_name
                    value = header_value.decode() if isinstance(header_value, bytes) else header_value
                    if name == ":status":
                        status_code = int(value)
                    else:
                        response_headers[name] = value

            elif isinstance(event, h2.events.DataReceived):
                if t_first_byte is None:
                    t_first_byte = time.perf_counter()
                body_chunks.append(event.data)
                conn.acknowledge_received_data(
                    event.flow_controlled_length, event.stream_id,
                )

            elif isinstance(event, h2.events.StreamEnded):
                stream_ended = True

            elif isinstance(event, h2.events.StreamReset):
                raise ConnectionError(
                    f"HTTP/2 stream reset: error code {event.error_code}"
                )

        # Flush any frames h2 queued while processing events (flow-control
        # WINDOW_UPDATEs, PING/SETTINGS ACKs) — even when no DATA arrived,
        # otherwise unacknowledged control frames can stall the connection.
        outbound = conn.data_to_send()
        if outbound:
            writer.write(outbound)
            await writer.drain()

    if t_first_byte is None:
        t_first_byte = time.perf_counter()

    t_done = time.perf_counter()
    ttfb_ms = (t_first_byte - t_send) * 1000.0
    transfer_ms = (t_done - t_first_byte) * 1000.0

    result = HttpResult(
        status_code=status_code,
        headers=response_headers,
        body=b"".join(body_chunks),
        http_version="HTTP/2",
    )
    return round(ttfb_ms, 3), round(transfer_ms, 3), result


# ---------------------------------------------------------------------------
# HTTP/1.1 measurement on existing TLS socket
# ---------------------------------------------------------------------------

async def _measure_http_h1(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    hostname: str,
    path: str,
    extra_headers: dict[str, str],
    timeout: float,
) -> tuple[float, float, HttpResult]:
    """Send a raw HTTP/1.1 request on an existing TLS connection.

    Returns (ttfb_ms, transfer_ms, HttpResult).
    """
    # Build request
    request_lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {hostname}",
        f"User-Agent: {USER_AGENT}",
        "Accept: */*",
        "Connection: close",
    ]
    for k, v in extra_headers.items():
        request_lines.append(f"{k}: {v}")
    request_lines.append("")
    request_lines.append("")
    request_bytes = "\r\n".join(request_lines).encode()

    # Send request — start TTFB timer
    t_send = time.perf_counter()
    writer.write(request_bytes)
    await writer.drain()

    # Read until we get the full header block (\r\n\r\n)
    header_buf = b""
    t_first_byte: float | None = None
    while b"\r\n\r\n" not in header_buf:
        chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
        if not chunk:
            break
        if t_first_byte is None:
            t_first_byte = time.perf_counter()
        header_buf += chunk

    if t_first_byte is None:
        t_first_byte = time.perf_counter()
    ttfb_ms = (t_first_byte - t_send) * 1000.0

    # Split header from any body data received so far
    header_end = header_buf.index(b"\r\n\r\n")
    header_block = header_buf[:header_end].decode(errors="replace")
    body_so_far = header_buf[header_end + 4:]

    # Parse status line
    lines = header_block.split("\r\n")
    status_line = lines[0]
    parts = status_line.split(" ", 2)
    http_version_str = parts[0] if len(parts) >= 1 else "HTTP/1.1"
    status_code = int(parts[1]) if len(parts) >= 2 else 0

    # Parse headers
    response_headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            response_headers[name.strip().lower()] = value.strip()

    # Read body
    content_length = response_headers.get("content-length")
    transfer_encoding = response_headers.get("transfer-encoding", "").lower()

    t_done: float | None = None
    if transfer_encoding == "chunked":
        body = await _read_chunked_body(reader, body_so_far, timeout)
    elif content_length is not None:
        remaining = int(content_length) - len(body_so_far)
        body_parts = [body_so_far]
        while remaining > 0:
            chunk = await asyncio.wait_for(reader.read(min(remaining, 65535)), timeout=timeout)
            if not chunk:
                break
            body_parts.append(chunk)
            remaining -= len(chunk)
        body = b"".join(body_parts)
    else:
        # Read until EOF (Connection: close).  A server that ignores
        # "Connection: close" leaves the socket open until our read times
        # out — clock transfer at the last byte received so that idle wait
        # is not counted as transfer time.
        body_parts = [body_so_far]
        t_last_data = t_first_byte
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(65535), timeout=timeout)
                if not chunk:
                    t_last_data = time.perf_counter()
                    break
                body_parts.append(chunk)
                t_last_data = time.perf_counter()
            except (asyncio.TimeoutError, ConnectionError):
                break
        body = b"".join(body_parts)
        t_done = t_last_data

    if t_done is None:
        t_done = time.perf_counter()
    transfer_ms = (t_done - t_first_byte) * 1000.0

    result = HttpResult(
        status_code=status_code,
        headers=response_headers,
        body=body,
        http_version=http_version_str,
    )
    return round(ttfb_ms, 3), round(transfer_ms, 3), result


async def _read_chunked_body(
    reader: asyncio.StreamReader,
    initial_data: bytes,
    timeout: float,
) -> bytes:
    """Read a chunked transfer-encoded body."""
    buf = initial_data
    body_parts: list[bytes] = []

    while True:
        # Ensure we have a chunk size line
        while b"\r\n" not in buf:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            if not chunk:
                return b"".join(body_parts)
            buf += chunk

        line_end = buf.index(b"\r\n")
        size_str = buf[:line_end].decode(errors="replace").strip()
        buf = buf[line_end + 2:]

        # Parse chunk size (ignore extensions after semicolon)
        if ";" in size_str:
            size_str = size_str.split(";")[0]
        chunk_size = int(size_str, 16)

        if chunk_size == 0:
            break

        # Read chunk_size bytes + trailing \r\n
        needed = chunk_size + 2  # data + \r\n
        while len(buf) < needed:
            data = await asyncio.wait_for(reader.read(min(needed - len(buf), 65535)), timeout=timeout)
            if not data:
                break
            buf += data

        body_parts.append(buf[:chunk_size])
        buf = buf[chunk_size + 2:]  # skip trailing \r\n

    return b"".join(body_parts)


# ---------------------------------------------------------------------------
# HTTP dispatcher (replaces old _measure_http)
# ---------------------------------------------------------------------------

async def _measure_http(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    hostname: str,
    path: str,
    extra_headers: dict[str, str],
    timeout: float,
    alpn_protocol: Optional[str],
) -> tuple[float, float, HttpResult]:
    """Dispatch to h2 or h1 based on ALPN negotiation.

    Returns (ttfb_ms, transfer_ms, HttpResult).
    """
    if alpn_protocol == "h2":
        return await _measure_http_h2(
            reader, writer, hostname, path, extra_headers, timeout,
        )
    else:
        return await _measure_http_h1(
            reader, writer, hostname, path, extra_headers, timeout,
        )


# ---------------------------------------------------------------------------
# PoP detection transport (still uses httpx — not on the timing path)
# ---------------------------------------------------------------------------

class _PinnedTransport(httpx.AsyncHTTPTransport):
    """Transport that pins DNS resolution to a specific IP.

    Rewrites the request URL to target the pre-resolved IP while
    preserving the original hostname via the ``sni_hostname`` extension
    so that TLS SNI and certificate validation work correctly.
    """

    def __init__(self, target_ip: str, **kwargs):
        self._target_ip = target_ip
        super().__init__(**kwargs)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        pinned_url = url.copy_with(host=self._target_ip)
        request = httpx.Request(
            method=request.method,
            url=pinned_url,
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": url.host.encode()},
        )
        return await super().handle_async_request(request)


# ---------------------------------------------------------------------------
# Single sample
# ---------------------------------------------------------------------------

async def _run_single_sample(
    provider: CDNProvider,
    sample_index: int,
    config: MeasurementConfig,
) -> SampleResult:
    """Execute one complete measurement sample for *provider*.

    Phases are measured independently in sequence:
        DNS -> TCP -> TLS -> TTFB -> Transfer

    The TLS socket is kept open and reused for the HTTP request so that
    TTFB measures only the application-level request/response latency,
    not a redundant TCP+TLS handshake.

    If an early phase fails the remaining phases are skipped and the
    sample is marked with an error string.
    """
    timing = TimingBreakdown()
    parsed = urlparse(provider.probe_url)
    hostname = parsed.hostname or ""
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    resolved_ip: Optional[str] = None
    tls_version: Optional[str] = None
    http_version: Optional[str] = None
    status_code: Optional[int] = None
    cache_status: Optional[str] = None
    error: Optional[str] = None

    # Track the active reader/writer for the socket reused across phases
    active_reader: asyncio.StreamReader | None = None
    active_writer: asyncio.StreamWriter | None = None
    alpn_protocol: Optional[str] = None

    try:
        # ---- Phase 1: DNS ----
        try:
            resolved_ip, dns_ms = await _resolve_dns(hostname, config)
            timing.dns_ms = dns_ms
        except Exception as exc:
            error = f"DNS resolution failed: {exc}"
            logger.debug("DNS failed for %s: %s", hostname, exc)
            return SampleResult(
                sample_index=sample_index,
                timing=timing,
                error=error,
            )

        # ---- Phase 2: TCP ----
        try:
            active_reader, active_writer, tcp_ms = await _measure_tcp(
                resolved_ip, port, config.timeout,
            )
            timing.tcp_ms = tcp_ms
        except Exception as exc:
            error = f"TCP connect failed: {exc}"
            logger.debug("TCP failed for %s:%d: %s", resolved_ip, port, exc)
            return SampleResult(
                sample_index=sample_index,
                timing=timing,
                resolved_ip=resolved_ip,
                error=error,
            )

        # ---- Phase 3: TLS ----
        if parsed.scheme == "https":
            try:
                tls_ms, tls_version = await _measure_tls(
                    active_reader, active_writer, hostname, config.timeout,
                )
                timing.tls_ms = tls_ms
                alpn_protocol = _detect_alpn(active_writer)
            except Exception:
                # Fallback: combined TCP+TLS measurement (new connection).
                logger.debug(
                    "start_tls failed for %s, falling back to combined measurement",
                    hostname,
                )
                # Close the broken original socket
                _safe_close_writer(active_writer)
                active_reader = None
                active_writer = None

                try:
                    active_reader, active_writer, tls_ms, tls_version = (
                        await _measure_tcp_tls_combined(
                            resolved_ip, port, hostname, timing.tcp_ms, config.timeout,
                        )
                    )
                    timing.tls_ms = tls_ms
                    alpn_protocol = _detect_alpn(active_writer)
                except Exception as exc:
                    error = f"TLS handshake failed: {exc}"
                    logger.debug("TLS failed for %s: %s", hostname, exc)
                    return SampleResult(
                        sample_index=sample_index,
                        timing=timing,
                        resolved_ip=resolved_ip,
                        error=error,
                    )

        # ---- Phases 4 & 5: TTFB + Transfer ----
        try:
            ttfb_ms, transfer_ms, http_result = await _measure_http(
                active_reader,
                active_writer,
                hostname,
                path,
                provider.extra_headers,
                config.timeout,
                alpn_protocol,
            )
            timing.ttfb_ms = ttfb_ms
            timing.transfer_ms = transfer_ms

            status_code = http_result.status_code
            http_version = http_result.http_version

            # Redirects mean timing reflects the redirect response, not the
            # final destination.  A user-visible warning is attached to the
            # provider result in measure_provider; log the target here.
            if status_code and 300 <= status_code < 400:
                location = http_result.headers.get("location") or "unknown"
                logger.debug(
                    "%s probe URL returned %d redirect to %s",
                    provider.slug, status_code, location,
                )

            # Extract cache status from common CDN headers.
            for hdr in ("x-cache", "cf-cache-status", "x-cache-status", "x-cdn-cache"):
                val = http_result.headers.get(hdr)
                if val:
                    cache_status = val
                    break

        except asyncio.TimeoutError as exc:
            error = f"HTTP timeout: {exc}"
            logger.debug("HTTP timeout for %s: %s", hostname, exc)
        except Exception as exc:
            error = f"HTTP request failed: {exc}"
            logger.debug("HTTP failed for %s: %s", hostname, exc)

    finally:
        # Always close the socket when we're done
        await _async_close_writer(active_writer)

    return SampleResult(
        sample_index=sample_index,
        timing=timing,
        resolved_ip=resolved_ip,
        tls_version=tls_version,
        http_version=http_version,
        status_code=status_code,
        error=error,
        cache_status=cache_status,
    )


async def _async_close_writer(writer: asyncio.StreamWriter | None) -> None:
    """Close a stream writer and await full shutdown."""
    if writer is None:
        return
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


def _safe_close_writer(writer: asyncio.StreamWriter | None) -> None:
    """Close a stream writer, scheduling async cleanup if possible."""
    if writer is None:
        return
    try:
        writer.close()
        # Schedule wait_closed() if an event loop is running.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(writer.wait_closed())
        except RuntimeError:
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Rate-limit backoff
# ---------------------------------------------------------------------------

# Wall-clock backstop per sample: a sample runs up to four sequential
# phases (DNS, TCP, TLS, HTTP) that are each individually bounded by
# config.timeout, so the budget scales with the number of phases plus
# fixed slack.  Sized so it cannot fire on a legitimately
# slow-but-progressing sample.
_SAMPLE_BUDGET_PHASES = 4
_SAMPLE_BUDGET_SLACK_S = 5.0


async def _run_capped_sample(
    provider: CDNProvider,
    sample_index: int,
    config: MeasurementConfig,
) -> SampleResult:
    """Run a single sample with an overall wall-clock budget.

    The budget is a backstop against pathological servers that trickle
    data forever (the HTTP phase may span several reads, each bounded by
    ``config.timeout`` but unbounded in aggregate).
    """
    budget = config.timeout * _SAMPLE_BUDGET_PHASES + _SAMPLE_BUDGET_SLACK_S
    try:
        return await asyncio.wait_for(
            _run_single_sample(provider, sample_index, config),
            timeout=budget,
        )
    except asyncio.TimeoutError:
        return SampleResult(
            sample_index=sample_index,
            timing=TimingBreakdown(),
            error="Overall sample timeout exceeded",
        )


async def _run_sample_with_backoff(
    provider: CDNProvider,
    sample_index: int,
    config: MeasurementConfig,
) -> SampleResult:
    """Run a single sample, retrying once on HTTP 429 with backoff."""
    result = await _run_capped_sample(provider, sample_index, config)
    if result.status_code == 429:
        backoff_s = 2.0
        logger.debug(
            "Rate-limited (429) by %s, backing off %.1fs before retry",
            provider.slug,
            backoff_s,
        )
        await asyncio.sleep(backoff_s)
        result = await _run_capped_sample(provider, sample_index, config)
    return result


# ---------------------------------------------------------------------------
# Provider-level measurement
# ---------------------------------------------------------------------------

async def measure_provider(
    provider: CDNProvider,
    config: MeasurementConfig,
    progress_callback: ProgressCallback | None = None,
) -> ProviderResult:
    """Run all measurement samples for a single provider.

    Warmup samples are executed and discarded first, followed by the
    configured number of recorded samples.  Each sample uses a fresh
    connection to avoid connection-reuse bias.

    Parameters
    ----------
    provider:
        The CDN provider to measure.
    config:
        Measurement parameters (sample count, warmup, delays, etc.).
    progress_callback:
        Optional callable invoked after each sample completes.
        Signature: ``(provider_slug, sample_index, total_samples, result)``
    """
    total_samples = config.warmup + config.samples
    delay_s = config.delay_ms / 1000.0

    result = ProviderResult(
        provider_name=provider.name,
        provider_slug=provider.slug,
        probe_url=provider.probe_url,
    )

    for i in range(total_samples):
        is_warmup = i < config.warmup
        sample_idx = i - config.warmup  # negative during warmup

        if progress_callback and not is_warmup:
            progress_callback(provider.slug, sample_idx, config.samples, None)

        sample = await _run_sample_with_backoff(provider, sample_idx, config)

        # Surface redirect responses as a user-visible warning (once).
        if sample.status_code and 300 <= sample.status_code < 400:
            msg = (
                f"Probe URL returned HTTP {sample.status_code} redirect — "
                "timing reflects the redirect response, not the final destination"
            )
            if msg not in result.warnings:
                result.warnings.append(msg)

        if not is_warmup:
            result.samples.append(sample)

            if progress_callback:
                progress_callback(
                    provider.slug, sample_idx, config.samples, sample,
                )

        # Keep first good sample for metadata extraction.
        if sample.error is None and result.resolved_ip is None:
            result.resolved_ip = sample.resolved_ip
            result.tls_version = sample.tls_version
            result.http_version = sample.http_version

        # Inter-sample delay (skip after last sample).
        if i < total_samples - 1 and delay_s > 0:
            await asyncio.sleep(delay_s)

    # ---- PoP detection and metadata via a lightweight extra request ----
    # We make one additional request specifically for PoP detection so the
    # provider's detect_pop / extract_metadata methods can inspect a real
    # httpx.Response object.  This request is *not* included in timing stats.
    try:
        pop, metadata = await _detect_pop_and_metadata(provider, result, config)
        result.pop = pop
        result.extra_metadata = metadata
    except Exception as exc:
        logger.debug("PoP detection failed for %s: %s", provider.slug, exc)

    # Fill in city/country/lat/lon from the IATA database so that both
    # terminal rendering and JSON/CSV exports see the same enriched PoP.
    enrich_pop(result.pop)

    # Aggregate phase statistics.
    aggregate_provider_stats(result)

    return result


async def _detect_pop_and_metadata(
    provider: CDNProvider,
    result: ProviderResult,
    config: MeasurementConfig,
) -> tuple[PoPIdentity, dict[str, str]]:
    """Make a lightweight request to detect PoP identity and extract metadata.

    Uses the resolved IP from prior samples when available so DNS cost
    is not incurred again.
    """
    parsed = urlparse(provider.probe_url)
    hostname = parsed.hostname or ""
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    scheme = parsed.scheme or "https"

    # Prefer pre-resolved IP to skip DNS via pinned transport.
    ip = result.resolved_ip
    url = f"{scheme}://{hostname}{path}" if hostname else provider.probe_url
    headers = {
        "User-Agent": USER_AGENT,
        **provider.extra_headers,
    }

    if ip:
        transport = _PinnedTransport(target_ip=ip, http2=True, verify=True)
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(config.timeout),
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers=headers)
    else:
        async with httpx.AsyncClient(
            http2=True,
            verify=True,
            timeout=httpx.Timeout(config.timeout),
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers=headers)

    pop = provider.detect_pop(response)

    # If PoP detection was weak, try IP-based detection
    if pop.confidence in ("best_effort", "unknown") and result.resolved_ip:
        ip_pop = await provider.detect_pop_by_ip(result.resolved_ip)
        if ip_pop is not None and ip_pop.code:
            pop = ip_pop

    metadata = provider.extract_metadata(response)

    return pop, metadata


# ---------------------------------------------------------------------------
# Multi-provider orchestration
# ---------------------------------------------------------------------------

async def measure_all(
    config: MeasurementConfig,
    progress_callback: ProgressCallback | None = None,
) -> list[ProviderResult]:
    """Run measurements for all configured providers concurrently.

    If ``config.providers`` is empty, every registered provider is measured.
    Providers are measured concurrently, capped at ``config.concurrency``
    at a time so simultaneous probes don't contend for bandwidth and skew
    the latency being measured; within each provider, samples run
    sequentially.

    Parameters
    ----------
    config:
        Measurement configuration.
    progress_callback:
        Optional callable forwarded to each ``measure_provider`` call.

    Returns
    -------
    list[ProviderResult]
        One result per provider, in the same order as the provider list.
    """
    provider_map = get_provider_map()

    if config.providers:
        slugs = config.providers
    else:
        slugs = sorted(provider_map.keys())

    providers: list[CDNProvider] = []
    errors: list[ProviderResult] = []

    for slug in slugs:
        try:
            providers.append(get_provider(slug))
        except ValueError as exc:
            errors.append(
                ProviderResult(
                    provider_name=slug,
                    provider_slug=slug,
                    probe_url="",
                    error=str(exc),
                )
            )

    semaphore = asyncio.Semaphore(max(1, config.concurrency))

    async def _safe_measure(p: CDNProvider) -> ProviderResult:
        """Wrapper that catches unexpected fatal errors per provider."""
        try:
            async with semaphore:
                return await measure_provider(p, config, progress_callback)
        except Exception as exc:
            logger.exception("Fatal error measuring %s", p.slug)
            return ProviderResult(
                provider_name=p.name,
                provider_slug=p.slug,
                probe_url=p.probe_url,
                error=f"Fatal measurement error: {exc}",
            )

    tasks = [_safe_measure(p) for p in providers]
    results = await asyncio.gather(*tasks)

    return list(results) + errors
