"""Data models for cdnprobe."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TimingBreakdown:
    """Per-phase timing for a single sample."""

    dns_ms: float = 0.0
    tcp_ms: float = 0.0
    tls_ms: float = 0.0
    ttfb_ms: float = 0.0
    transfer_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return self.dns_ms + self.tcp_ms + self.tls_ms + self.ttfb_ms + self.transfer_ms


@dataclass
class LatencyStats:
    """Aggregated statistics for a timing phase."""

    min: float = 0.0
    max: float = 0.0
    avg: float = 0.0
    median: float = 0.0
    p95: float = 0.0
    stdev: float = 0.0
    jitter: float = 0.0


@dataclass
class PoPIdentity:
    """Identified CDN Point of Presence."""

    code: Optional[str] = None  # IATA code (e.g. "DFW")
    city: Optional[str] = None
    country: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    confidence: str = "confirmed"  # confirmed | inferred | best_effort | unknown
    raw_header: Optional[str] = None  # Raw value used for detection


@dataclass
class HopInfo:
    """A single hop in a network path trace."""

    hop_number: int
    ip: Optional[str] = None  # None if hop timed out (*)
    hostname: Optional[str] = None  # Reverse DNS, None if no PTR
    rtt_ms: list[float] = field(default_factory=list)  # RTT per probe
    asn: Optional[int] = None
    asn_name: Optional[str] = None  # e.g. "GOOGLE, US"
    prefix: Optional[str] = None  # e.g. "8.8.8.0/24"
    country: Optional[str] = None  # Country code from Cymru

    @property
    def avg_rtt(self) -> Optional[float]:
        valid = [r for r in self.rtt_ms if r is not None]
        return sum(valid) / len(valid) if valid else None

    @property
    def is_private(self) -> bool:
        if not self.ip:
            return False
        try:
            return ipaddress.ip_address(self.ip).is_private
        except ValueError:
            return False

    @property
    def is_timeout(self) -> bool:
        return self.ip is None


@dataclass
class NetworkPath:
    """Complete traceroute result for a provider."""

    provider_slug: str
    target_ip: str
    hops: list[HopInfo] = field(default_factory=list)
    total_hops: int = 0
    reached_target: bool = False

    @property
    def unique_asns(self) -> set[int]:
        return {h.asn for h in self.hops if h.asn is not None}


@dataclass
class SampleResult:
    """Result of a single measurement sample."""

    sample_index: int
    timing: TimingBreakdown
    resolved_ip: Optional[str] = None
    tls_version: Optional[str] = None
    http_version: Optional[str] = None
    status_code: Optional[int] = None
    error: Optional[str] = None
    cache_status: Optional[str] = None


@dataclass
class ProviderResult:
    """Complete measurement results for one CDN provider."""

    provider_name: str
    provider_slug: str
    probe_url: str
    pop: PoPIdentity = field(default_factory=PoPIdentity)
    samples: list[SampleResult] = field(default_factory=list)
    phase_stats: dict[str, LatencyStats] = field(default_factory=dict)
    network_path: Optional[NetworkPath] = None
    resolved_ip: Optional[str] = None
    tls_version: Optional[str] = None
    http_version: Optional[str] = None
    error: Optional[str] = None  # Fatal error for entire provider
    extra_metadata: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def successful_samples(self) -> list[SampleResult]:
        return [s for s in self.samples if s.error is None]

    @property
    def is_reachable(self) -> bool:
        return len(self.successful_samples) > 0


@dataclass
class GeoLocation:
    """User's geolocation info."""

    ip: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    isp: Optional[str] = None
    org: Optional[str] = None
    asn: Optional[int] = None
    error: Optional[str] = None


@dataclass
class MeasurementConfig:
    """Configuration for a measurement run."""

    providers: list[str] = field(default_factory=list)  # empty = all
    samples: int = 5
    warmup: int = 1
    delay_ms: int = 100
    timeout: float = 10.0
    concurrency: int = 4
    dns_server: Optional[str] = None
    ipv4_only: bool = False
    ipv6_only: bool = False
    trace_enabled: bool = True
    max_hops: int = 30
    verbose: bool = False
    quiet: bool = False
    no_geo: bool = False
    compare_only: bool = False
    json_output: bool = False
    csv_output: bool = False
    output_file: Optional[str] = None


@dataclass
class FullResult:
    """Complete measurement run results."""

    geo: Optional[GeoLocation] = None
    providers: list[ProviderResult] = field(default_factory=list)
    config: Optional[MeasurementConfig] = None
    timestamp: Optional[str] = None
