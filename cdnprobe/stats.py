"""Statistical aggregation for latency measurements."""

from __future__ import annotations

import math
from typing import Sequence

from cdnprobe.models import LatencyStats, ProviderResult, SampleResult, TimingBreakdown


def compute_stats(values: Sequence[float]) -> LatencyStats:
    """Compute statistical summary from a list of values."""
    if not values:
        return LatencyStats()

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    avg = sum(sorted_vals) / n
    median = _percentile(sorted_vals, 50)
    p95 = _percentile(sorted_vals, 95)

    variance = sum((v - avg) ** 2 for v in sorted_vals) / (n - 1) if n > 1 else 0.0
    stdev = math.sqrt(variance)

    jitter = _compute_jitter(values)

    return LatencyStats(
        min=round(sorted_vals[0], 2),
        max=round(sorted_vals[-1], 2),
        avg=round(avg, 2),
        median=round(median, 2),
        p95=round(p95, 2),
        stdev=round(stdev, 2),
        jitter=round(jitter, 2),
    )


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Compute the given percentile from pre-sorted values."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    k = (pct / 100) * (n - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def _compute_jitter(values: Sequence[float]) -> float:
    """Compute jitter as average absolute difference between consecutive samples."""
    if len(values) < 2:
        return 0.0
    diffs = [abs(values[i + 1] - values[i]) for i in range(len(values) - 1)]
    return sum(diffs) / len(diffs)


def aggregate_provider_stats(result: ProviderResult) -> None:
    """Compute per-phase stats for a provider result, modifying it in place."""
    samples = result.successful_samples
    if not samples:
        return

    phases = {
        "dns": [s.timing.dns_ms for s in samples],
        "tcp": [s.timing.tcp_ms for s in samples],
        "tls": [s.timing.tls_ms for s in samples],
        "ttfb": [s.timing.ttfb_ms for s in samples],
        "transfer": [s.timing.transfer_ms for s in samples],
        "total": [s.timing.total_ms for s in samples],
    }

    for phase_name, values in phases.items():
        result.phase_stats[phase_name] = compute_stats(values)
