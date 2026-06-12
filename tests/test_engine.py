"""Tests for engine internals that don't require the network.

The HTTP exchange tests run against in-process asyncio servers on
localhost; ``_measure_http_h1``/``_measure_http_h2`` operate on any
stream pair, so no TLS is needed.
"""

import asyncio

import h2.config
import h2.connection
import h2.events
import pytest

from cdnprobe import engine
from cdnprobe.engine import (
    _measure_http_h1,
    _measure_http_h2,
    _read_chunked_body,
    _run_capped_sample,
)
from cdnprobe.models import MeasurementConfig, SampleResult, TimingBreakdown


def _reader_with(data: bytes, eof: bool = True) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    if eof:
        reader.feed_eof()
    return reader


def test_chunked_body_simple():
    async def run():
        reader = _reader_with(b"5\r\nhello\r\n0\r\n\r\n")
        return await _read_chunked_body(reader, b"", timeout=1.0)

    assert asyncio.run(run()) == b"hello"


def test_chunked_body_multiple_chunks_and_initial_data():
    async def run():
        # First chunk already partially buffered from the header read.
        reader = _reader_with(b"llo\r\n6\r\n world\r\n0\r\n\r\n")
        return await _read_chunked_body(reader, b"5\r\nhe", timeout=1.0)

    assert asyncio.run(run()) == b"hello world"


def test_chunked_body_ignores_chunk_extensions():
    async def run():
        reader = _reader_with(b"5;ext=1\r\nhello\r\n0\r\n\r\n")
        return await _read_chunked_body(reader, b"", timeout=1.0)

    assert asyncio.run(run()) == b"hello"


def test_chunked_body_truncated_stream():
    async def run():
        # Stream ends before the terminating 0-chunk.
        reader = _reader_with(b"5\r\nhel")
        return await _read_chunked_body(reader, b"", timeout=1.0)

    # Partial data is returned rather than raising.
    assert asyncio.run(run()) == b"hel"


def test_capped_sample_budget_allows_slow_phases(monkeypatch):
    """The per-sample budget must exceed the sum of per-phase timeouts.

    Regression test: the old budget (timeout + 5s) could kill a sample
    whose individual phases were each within their own timeout.
    """
    config = MeasurementConfig(timeout=0.1)

    async def slow_sample(provider, sample_index, cfg):
        # Slower than cfg.timeout but well within the 4x+5s budget.
        await asyncio.sleep(0.2)
        return SampleResult(sample_index=sample_index, timing=TimingBreakdown())

    monkeypatch.setattr(engine, "_run_single_sample", slow_sample)
    result = asyncio.run(_run_capped_sample(None, 0, config))
    assert result.error is None


def test_capped_sample_budget_kills_runaway(monkeypatch):
    config = MeasurementConfig(timeout=0.01)
    monkeypatch.setattr(engine, "_SAMPLE_BUDGET_SLACK_S", 0.05)

    async def runaway(provider, sample_index, cfg):
        await asyncio.sleep(30)
        pytest.fail("should have been cancelled")

    monkeypatch.setattr(engine, "_run_single_sample", runaway)
    result = asyncio.run(_run_capped_sample(None, 0, config))
    assert result.error == "Overall sample timeout exceeded"


# ── HTTP/1.1 against a local server ───────────────────────────────────


def test_h1_lingering_server_does_not_inflate_transfer():
    """Regression: a server that ignores 'Connection: close' must not add
    the read-timeout wait to transfer_ms."""

    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nX-Test: 1\r\n\r\nhello")
        await writer.drain()
        # Keep the connection open well past the client's read timeout.
        await asyncio.sleep(5)
        writer.close()

    async def run():
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            ttfb_ms, transfer_ms, result = await _measure_http_h1(
                reader, writer, "localhost", "/", {}, timeout=1.0,
            )
            writer.close()
            return ttfb_ms, transfer_ms, result
        finally:
            server.close()
            await server.wait_closed()

    ttfb_ms, transfer_ms, result = asyncio.run(run())
    assert result.status_code == 200
    assert result.body == b"hello"
    # Transfer is clocked at the last byte received; the ~1s idle wait for
    # EOF must not be included.
    assert transfer_ms < 500


def test_h1_content_length_body():
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello")
        await writer.drain()
        writer.close()

    async def run():
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            _, _, result = await _measure_http_h1(
                reader, writer, "localhost", "/", {}, timeout=2.0,
            )
            writer.close()
            return result
        finally:
            server.close()
            await server.wait_closed()

    result = asyncio.run(run())
    assert result.status_code == 200
    assert result.body == b"hello"


# ── HTTP/2 against a local server ─────────────────────────────────────


def test_h2_exchange_with_ping_before_response():
    """Regression for the control-frame flush fix.

    The server sends a PING after receiving the request and waits for the
    PING ACK before sending the response.  The old client only flushed
    queued frames when DATA arrived, so this exchange deadlocked.
    """

    async def handler(reader, writer):
        conn = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False)
        )
        conn.initiate_connection()
        writer.write(conn.data_to_send())
        await writer.drain()

        request_stream = None
        ping_acked = False
        ping_sent = False

        while True:
            data = await asyncio.wait_for(reader.read(65535), timeout=5.0)
            if not data:
                return
            for event in conn.receive_data(data):
                if isinstance(event, h2.events.RequestReceived):
                    request_stream = event.stream_id
                elif isinstance(event, h2.events.PingAckReceived):
                    ping_acked = True
            writer.write(conn.data_to_send())
            await writer.drain()

            if request_stream is not None and not ping_sent:
                conn.ping(b"12345678")
                writer.write(conn.data_to_send())
                await writer.drain()
                ping_sent = True

            if ping_acked:
                conn.send_headers(
                    request_stream,
                    [(":status", "200"), ("x-test", "1")],
                )
                conn.send_data(request_stream, b"hello h2", end_stream=True)
                writer.write(conn.data_to_send())
                await writer.drain()
                return

    async def run():
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            result = await asyncio.wait_for(
                _measure_http_h2(reader, writer, "localhost", "/", {}, timeout=2.0),
                timeout=4.0,
            )
            writer.close()
            return result
        finally:
            server.close()
            await server.wait_closed()

    ttfb_ms, transfer_ms, result = asyncio.run(run())
    assert result.status_code == 200
    assert result.body == b"hello h2"
    assert result.http_version == "HTTP/2"
