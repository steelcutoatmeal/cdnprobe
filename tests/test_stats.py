"""Tests for statistical aggregation."""

import math

from cdnprobe.models import ProviderResult, SampleResult, TimingBreakdown
from cdnprobe.stats import _compute_jitter, _percentile, aggregate_provider_stats, compute_stats


def test_empty_values():
    stats = compute_stats([])
    assert stats.min == 0.0
    assert stats.max == 0.0
    assert stats.avg == 0.0


def test_single_value():
    stats = compute_stats([42.5])
    assert stats.min == 42.5
    assert stats.max == 42.5
    assert stats.avg == 42.5
    assert stats.median == 42.5
    assert stats.p95 == 42.5
    assert stats.stdev == 0.0
    assert stats.jitter == 0.0


def test_basic_stats():
    stats = compute_stats([10.0, 20.0, 30.0, 40.0, 50.0])
    assert stats.min == 10.0
    assert stats.max == 50.0
    assert stats.avg == 30.0
    assert stats.median == 30.0


def test_min_max_rounded_consistently():
    stats = compute_stats([5.782, 7.991])
    # All fields use the same 2-decimal rounding.
    assert stats.min == 5.78
    assert stats.max == 7.99


def test_stdev_uses_bessel_correction():
    values = [10.0, 20.0, 30.0]
    stats = compute_stats(values)
    expected = math.sqrt(sum((v - 20.0) ** 2 for v in values) / 2)
    assert stats.stdev == round(expected, 2)


def test_percentile_interpolation():
    vals = [10.0, 20.0, 30.0, 40.0]
    assert _percentile(vals, 50) == 25.0
    assert _percentile(vals, 0) == 10.0
    assert _percentile(vals, 100) == 40.0


def test_jitter_consecutive_differences():
    assert _compute_jitter([10.0, 20.0, 15.0]) == 7.5  # (10 + 5) / 2
    assert _compute_jitter([10.0]) == 0.0
    assert _compute_jitter([]) == 0.0


def _sample(idx, dns=1.0, error=None):
    return SampleResult(
        sample_index=idx,
        timing=TimingBreakdown(dns_ms=dns, tcp_ms=2.0, tls_ms=3.0, ttfb_ms=4.0, transfer_ms=5.0),
        error=error,
    )


def test_aggregate_skips_failed_samples():
    result = ProviderResult(provider_name="X", provider_slug="x", probe_url="https://x")
    result.samples = [_sample(0, dns=10.0), _sample(1, dns=20.0), _sample(2, error="boom")]
    aggregate_provider_stats(result)
    assert result.phase_stats["dns"].avg == 15.0
    assert result.phase_stats["total"].min > 0


def test_aggregate_no_successful_samples():
    result = ProviderResult(provider_name="X", provider_slug="x", probe_url="https://x")
    result.samples = [_sample(0, error="boom")]
    aggregate_provider_stats(result)
    assert result.phase_stats == {}
