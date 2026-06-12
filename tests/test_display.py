"""Rendering tests using a captured Rich console (no terminal, no network)."""

import io

from rich.console import Console

from cdnprobe import display
from cdnprobe.models import (
    FullResult,
    LatencyStats,
    PoPIdentity,
    ProviderResult,
    SampleResult,
    TimingBreakdown,
)


def _fake_provider(name, slug, total_median, warnings=()):
    result = ProviderResult(
        provider_name=name,
        provider_slug=slug,
        probe_url=f"https://{slug}.example",
        pop=PoPIdentity(code="DFW", city="Dallas", country="US"),
        resolved_ip="192.0.2.1",
        tls_version="TLSv1.3",
        http_version="HTTP/2",
    )
    result.samples = [
        SampleResult(sample_index=0, timing=TimingBreakdown(dns_ms=1, tcp_ms=2, tls_ms=3, ttfb_ms=4, transfer_ms=5))
    ]
    stats = LatencyStats(min=total_median, max=total_median, avg=total_median,
                         median=total_median, p95=total_median, jitter=0.5)
    result.phase_stats = {p: stats for p in ("dns", "tcp", "tls", "ttfb", "transfer", "total")}
    result.warnings = list(warnings)
    return result


def _capture(fn, *args, **kwargs):
    buf = io.StringIO()
    original = display.console
    display.console = Console(file=buf, force_terminal=False, width=140)
    try:
        fn(*args, **kwargs)
    finally:
        display.console = original
    return buf.getvalue()


def test_render_comparison_table():
    providers = [
        _fake_provider("Alpha", "alpha", 20.0),
        _fake_provider("Beta", "beta", 80.0),
    ]
    out = _capture(display.render_comparison, providers)
    assert "CDN Comparison" in out
    assert "Alpha" in out and "Beta" in out
    # Latency bar glyphs render for both rows.
    assert display._BAR_FILLED in out


def test_render_full_includes_warnings():
    provider = _fake_provider(
        "Alpha", "alpha", 20.0,
        warnings=["Probe URL returned HTTP 301 redirect — timing reflects the redirect response, not the final destination"],
    )
    out = _capture(display.render_full, FullResult(providers=[provider]))
    assert "301 redirect" in out
    assert "PoP: DFW (Dallas, US)" in out


def test_render_unreachable_provider():
    result = ProviderResult(
        provider_name="Down", provider_slug="down", probe_url="https://down.example",
        error="Fatal measurement error: boom",
    )
    out = _capture(display.render_full, FullResult(providers=[result]))
    assert "Fatal measurement error" in out
