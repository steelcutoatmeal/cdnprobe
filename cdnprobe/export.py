"""JSON and CSV export for measurement results."""

from __future__ import annotations

import csv
import io
import json

from cdnprobe.models import FullResult, ProviderResult


def export_json(result: FullResult, indent: int = 2) -> str:
    """Export full results as JSON string."""
    data = _build_export_dict(result)
    return json.dumps(data, indent=indent, default=str)


def export_csv(result: FullResult) -> str:
    """Export results as CSV string (one row per provider with phase stats)."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "timestamp",
        "provider",
        "pop_code",
        "pop_city",
        "pop_country",
        "pop_confidence",
        "resolved_ip",
        "tls_version",
        "http_version",
        "samples_ok",
        "samples_total",
        "dns_min",
        "dns_avg",
        "dns_median",
        "dns_p95",
        "dns_max",
        "dns_jitter",
        "tcp_min",
        "tcp_avg",
        "tcp_median",
        "tcp_p95",
        "tcp_max",
        "tcp_jitter",
        "tls_min",
        "tls_avg",
        "tls_median",
        "tls_p95",
        "tls_max",
        "tls_jitter",
        "ttfb_min",
        "ttfb_avg",
        "ttfb_median",
        "ttfb_p95",
        "ttfb_max",
        "ttfb_jitter",
        "transfer_min",
        "transfer_avg",
        "transfer_median",
        "transfer_p95",
        "transfer_max",
        "transfer_jitter",
        "total_min",
        "total_avg",
        "total_median",
        "total_p95",
        "total_max",
        "total_jitter",
        "trace_hops",
        "trace_asns",
        "trace_reached",
    ])

    for pr in result.providers:
        row = [
            result.timestamp or "",
            pr.provider_name,
            pr.pop.code or "",
            pr.pop.city or "",
            pr.pop.country or "",
            pr.pop.confidence,
            pr.resolved_ip or "",
            pr.tls_version or "",
            pr.http_version or "",
            len(pr.successful_samples),
            len(pr.samples),
        ]

        for phase in ["dns", "tcp", "tls", "ttfb", "transfer", "total"]:
            stats = pr.phase_stats.get(phase)
            if stats:
                row.extend([
                    stats.min, stats.avg, stats.median,
                    stats.p95, stats.max, stats.jitter,
                ])
            else:
                row.extend([""] * 6)

        if pr.network_path:
            row.extend([
                pr.network_path.total_hops,
                len(pr.network_path.unique_asns),
                pr.network_path.reached_target,
            ])
        else:
            row.extend(["", "", ""])

        writer.writerow(row)

    return output.getvalue()


def write_to_file(content: str, filepath: str) -> None:
    """Write export content to a file."""
    with open(filepath, "w") as f:
        f.write(content)


def _build_export_dict(result: FullResult) -> dict:
    """Build a serializable dictionary from FullResult."""
    data: dict = {}

    if result.timestamp:
        data["timestamp"] = result.timestamp

    if result.geo:
        data["user"] = {
            "ip": result.geo.ip,
            "city": result.geo.city,
            "region": result.geo.region,
            "country": result.geo.country,
            "lat": result.geo.lat,
            "lon": result.geo.lon,
            "isp": result.geo.isp,
            "asn": result.geo.asn,
        }

    if result.config:
        data["config"] = {
            "samples": result.config.samples,
            "warmup": result.config.warmup,
            "delay_ms": result.config.delay_ms,
            "timeout": result.config.timeout,
            "dns_server": result.config.dns_server,
            "ipv4_only": result.config.ipv4_only,
            "ipv6_only": result.config.ipv6_only,
            "trace_enabled": result.config.trace_enabled,
        }

    data["providers"] = []
    for pr in result.providers:
        pdata = _provider_to_dict(pr)
        data["providers"].append(pdata)

    return data


def _provider_to_dict(pr: ProviderResult) -> dict:
    """Convert a ProviderResult to a serializable dict."""
    pdata: dict = {
        "name": pr.provider_name,
        "slug": pr.provider_slug,
        "probe_url": pr.probe_url,
        "resolved_ip": pr.resolved_ip,
        "tls_version": pr.tls_version,
        "http_version": pr.http_version,
        "error": pr.error,
        "warnings": pr.warnings,
        "pop": {
            "code": pr.pop.code,
            "city": pr.pop.city,
            "country": pr.pop.country,
            "lat": pr.pop.lat,
            "lon": pr.pop.lon,
            "confidence": pr.pop.confidence,
            "raw_header": pr.pop.raw_header,
        },
        "extra_metadata": pr.extra_metadata,
    }

    # Phase stats
    pdata["stats"] = {}
    for phase, stats in pr.phase_stats.items():
        pdata["stats"][phase] = {
            "min": stats.min,
            "max": stats.max,
            "avg": stats.avg,
            "median": stats.median,
            "p95": stats.p95,
            "stdev": stats.stdev,
            "jitter": stats.jitter,
        }

    # Individual samples
    pdata["samples"] = []
    for s in pr.samples:
        pdata["samples"].append({
            "index": s.sample_index,
            "error": s.error,
            "dns_ms": s.timing.dns_ms,
            "tcp_ms": s.timing.tcp_ms,
            "tls_ms": s.timing.tls_ms,
            "ttfb_ms": s.timing.ttfb_ms,
            "transfer_ms": s.timing.transfer_ms,
            "total_ms": s.timing.total_ms,
            "status_code": s.status_code,
        })

    # Network path
    if pr.network_path:
        np = pr.network_path
        pdata["network_path"] = {
            "target_ip": np.target_ip,
            "total_hops": np.total_hops,
            "reached_target": np.reached_target,
            "unique_asns": sorted(np.unique_asns),
            "hops": [],
        }
        for h in np.hops:
            pdata["network_path"]["hops"].append({
                "hop": h.hop_number,
                "ip": h.ip,
                "hostname": h.hostname,
                "rtt_ms": h.rtt_ms,
                "avg_rtt_ms": h.avg_rtt,
                "asn": h.asn,
                "asn_name": h.asn_name,
                "prefix": h.prefix,
                "country": h.country,
            })
    else:
        pdata["network_path"] = None

    return pdata
