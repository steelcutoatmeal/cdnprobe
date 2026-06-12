"""Rich terminal output for cdnprobe."""

from __future__ import annotations

from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from cdnprobe.config import PHASE_LABELS, PHASE_NAMES, PHASE_THRESHOLDS
from cdnprobe.models import (
    FullResult,
    GeoLocation,
    LatencyStats,
    NetworkPath,
    ProviderResult,
)

console = Console()

# Bar-chart glyphs (full block / light shade)
_BAR_FILLED = "█"
_BAR_EMPTY = "░"


def _color_for_ms(value: float, phase: str = "total") -> str:
    """Return a Rich color name based on latency value and phase thresholds."""
    thresholds = PHASE_THRESHOLDS.get(phase, PHASE_THRESHOLDS["total"])
    if value <= thresholds["fast"]:
        return "green"
    elif value <= thresholds["medium"]:
        return "yellow"
    return "red"


def _fmt_ms(value: float, phase: str = "total", colorize: bool = True) -> Text:
    """Format a millisecond value with optional color."""
    text = f"{value:.1f}ms"
    if colorize:
        color = _color_for_ms(value, phase)
        return Text(text, style=color)
    return Text(text)


# ── User connection info ──────────────────────────────────────────────


def render_geo(geo: GeoLocation) -> None:
    """Display user geolocation info."""
    if geo.error:
        console.print(f"[dim]Geolocation: {geo.error}[/dim]")
        return

    parts = []
    if geo.ip:
        parts.append(f"[bold]{geo.ip}[/bold]")
    location_parts = [p for p in [geo.city, geo.region, geo.country] if p]
    if location_parts:
        parts.append(", ".join(location_parts))
    if geo.isp:
        parts.append(f"[dim]{geo.isp}[/dim]")
    if geo.lat is not None and geo.lon is not None:
        parts.append(f"[dim]({geo.lat:.2f}, {geo.lon:.2f})[/dim]")

    console.print(f"[bold]Your Connection:[/bold] {' | '.join(parts)}")


# ── Progress tracking ─────────────────────────────────────────────────


class ProgressTracker:
    """Live progress display for measurement sampling."""

    def __init__(self, provider_slugs: list[str], total_samples: int):
        self.provider_slugs = provider_slugs
        self.total_samples = total_samples
        self.progress: dict[str, int] = {s: 0 for s in provider_slugs}
        self.status: dict[str, str] = {s: "waiting" for s in provider_slugs}
        self.live: Optional[Live] = None

    def _build_table(self) -> Table:
        table = Table(show_header=True, expand=False, border_style="dim")
        table.add_column("Provider", style="bold")
        table.add_column("Progress", min_width=20)
        table.add_column("Status")

        for slug in self.provider_slugs:
            completed = self.progress[slug]
            status = self.status[slug]
            bar_width = 15
            filled = int((completed / self.total_samples) * bar_width) if self.total_samples > 0 else 0
            bar = "[green]" + _BAR_FILLED * filled + "[/green]" + f"[dim]{_BAR_EMPTY}[/dim]" * (bar_width - filled)
            progress_text = f"{bar} {completed}/{self.total_samples}"

            style = "green" if status == "done" else ("red" if status == "error" else "yellow")
            status_text = f"[{style}]{status}[/{style}]"

            table.add_row(slug, progress_text, status_text)

        return table

    def start(self) -> None:
        self.live = Live(self._build_table(), console=console, refresh_per_second=4)
        self.live.start()

    def update(self, provider_slug: str, sample_index: int, status: str = "sampling") -> None:
        self.progress[provider_slug] = sample_index
        self.status[provider_slug] = status
        if self.live:
            self.live.update(self._build_table())

    def finish(self) -> None:
        if self.live:
            self.live.stop()


# ── Provider rendering ────────────────────────────────────────────────


def _render_provider_header(result: ProviderResult, geo: Optional[GeoLocation] = None) -> None:
    """Print the provider header line."""
    from cdnprobe.location import haversine_km

    parts = [f"[bold]{result.provider_name}[/bold]"]

    pop = result.pop
    if pop.code:
        # PoP city/country/lat/lon are enriched from the IATA database in
        # the measurement pipeline (engine.measure_provider), not here.
        loc_str = ", ".join(p for p in [pop.city, pop.country] if p)
        pop_text = f"PoP: [bold]{pop.code}[/bold]"
        if loc_str:
            pop_text += f" ({loc_str})"
        parts.append(pop_text)

        if (
            geo
            and geo.lat is not None
            and geo.lon is not None
            and pop.lat is not None
            and pop.lon is not None
        ):
            dist = haversine_km(geo.lat, geo.lon, pop.lat, pop.lon)
            parts.append(f"[dim]{dist:.0f} km away[/dim]")
    elif pop.confidence == "unknown":
        parts.append("[dim]PoP: unknown[/dim]")
    elif pop.confidence in ("inferred", "best_effort"):
        parts.append(f"[dim]PoP: {pop.confidence}[/dim]")

    console.print(" \u2014 ".join(parts))


def _build_phase_table(result: ProviderResult) -> Table:
    """Build the per-phase statistics table."""
    table = Table(
        show_header=True,
        border_style="bright_black",
        expand=False,
        pad_edge=True,
        header_style="bold",
    )
    table.add_column("Phase", style="bold", min_width=7)
    table.add_column("Min", justify="right", min_width=7)
    table.add_column("Avg", justify="right", min_width=7)
    table.add_column("Median", justify="right", min_width=7)
    table.add_column("P95", justify="right", min_width=7)
    table.add_column("Max", justify="right", min_width=7)
    table.add_column("Jitter", justify="right", min_width=7)

    for phase in PHASE_NAMES:
        stats = result.phase_stats.get(phase)
        if not stats:
            continue
        label = PHASE_LABELS.get(phase, phase.upper())

        # Add a visual separator before Total
        end_section = phase == "transfer"
        table.add_row(
            label,
            _fmt_ms(stats.min, phase),
            _fmt_ms(stats.avg, phase),
            _fmt_ms(stats.median, phase),
            _fmt_ms(stats.p95, phase),
            _fmt_ms(stats.max, phase),
            _fmt_ms(stats.jitter, phase),
            end_section=end_section,
        )

    return table


def _render_connection_info(result: ProviderResult) -> None:
    """Print the connection info line below the table."""
    info_parts = []
    if result.resolved_ip:
        info_parts.append(f"Edge IP: {result.resolved_ip}")
    if result.tls_version:
        info_parts.append(f"TLS: {result.tls_version}")
    if result.http_version:
        info_parts.append(result.http_version)
    if result.extra_metadata:
        for k, v in result.extra_metadata.items():
            info_parts.append(f"{k}: {v}")
    if info_parts:
        console.print(f"  [dim]{' | '.join(info_parts)}[/dim]")


def _build_verbose_table(result: ProviderResult) -> Table:
    """Build per-sample detail table."""
    table = Table(
        show_header=True,
        border_style="bright_black",
        expand=False,
        header_style="bold",
    )
    table.add_column("#", justify="right", width=3)
    table.add_column("DNS", justify="right")
    table.add_column("TCP", justify="right")
    table.add_column("TLS", justify="right")
    table.add_column("TTFB", justify="right")
    table.add_column("Transfer", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Status", justify="right")

    for s in result.samples:
        if s.error:
            table.add_row(
                str(s.sample_index),
                "\u2014", "\u2014", "\u2014", "\u2014", "\u2014", "\u2014",
                Text(s.error, style="red"),
            )
        else:
            table.add_row(
                str(s.sample_index),
                _fmt_ms(s.timing.dns_ms, "dns"),
                _fmt_ms(s.timing.tcp_ms, "tcp"),
                _fmt_ms(s.timing.tls_ms, "tls"),
                _fmt_ms(s.timing.ttfb_ms, "ttfb"),
                _fmt_ms(s.timing.transfer_ms, "transfer"),
                _fmt_ms(s.timing.total_ms, "total"),
                Text(str(s.status_code or "\u2014"), style="dim"),
            )

    return table


def _render_network_path(path: NetworkPath) -> None:
    """Print the hop-by-hop traceroute table with ASN info."""
    if not path.hops:
        console.print("[dim]  No traceroute data available[/dim]")
        return

    asn_count = len(path.unique_asns)
    console.print()
    console.print(
        f"[bold]Network Path[/bold] [dim]({path.total_hops} hops, "
        f"{asn_count} ASN{'s' if asn_count != 1 else ''} traversed)[/dim]"
    )

    table = Table(
        show_header=True,
        border_style="bright_black",
        expand=False,
        header_style="bold",
    )
    table.add_column("Hop", justify="center", width=4)
    table.add_column("IP", min_width=16)
    table.add_column("Hostname", min_width=24, max_width=36, overflow="ellipsis")
    table.add_column("RTT", justify="right", min_width=8)
    table.add_column("ASN", min_width=22)

    prev_asn = None
    for hop in path.hops:
        if hop.is_timeout:
            table.add_row(
                str(hop.hop_number),
                Text("*", style="red dim"),
                Text("", style="dim"),
                Text("*", style="red dim"),
                Text("", style="dim"),
            )
            prev_asn = None
            continue

        ip_style = "dim" if hop.is_private else ""
        ip_text = Text(hop.ip or "", style=ip_style)
        if hop.ip == path.target_ip:
            ip_text = Text(hop.ip, style="bold green")

        hostname = hop.hostname or "\u2014"
        hn_text = Text(hostname, style="dim" if hostname == "\u2014" else "")

        avg = hop.avg_rtt
        rtt_text = Text(f"{avg:.1f}ms" if avg is not None else "*", style="dim" if avg is None else "")

        if hop.is_private:
            asn_text = Text("(private)", style="dim italic")
        elif hop.asn:
            asn_str = f"AS{hop.asn}"
            if hop.asn_name:
                asn_str += f" {hop.asn_name}"
            style = "bold" if prev_asn is not None and hop.asn != prev_asn else ""
            asn_text = Text(asn_str, style=style)
            prev_asn = hop.asn
        else:
            asn_text = Text("\u2014", style="dim")

        table.add_row(str(hop.hop_number), ip_text, hn_text, rtt_text, asn_text)

    console.print(table)

    if path.reached_target:
        console.print("  [green]target reached \u2713[/green]")
    else:
        console.print("  [red]target not reached \u2717[/red]")


def _render_provider(
    result: ProviderResult,
    geo: Optional[GeoLocation] = None,
    verbose: bool = False,
) -> None:
    """Print the complete output for one provider."""
    _render_provider_header(result, geo)

    for warning in result.warnings:
        console.print(f"  [yellow]⚠ {warning}[/yellow]")

    if not result.phase_stats:
        console.print("[dim italic]  No successful samples[/dim italic]")
    else:
        console.print(_build_phase_table(result))
        _render_connection_info(result)

    if verbose and result.samples:
        console.print()
        console.print(_build_verbose_table(result))

    if result.network_path and result.network_path.hops:
        _render_network_path(result.network_path)


# ── Summary comparison table ──────────────────────────────────────────


def render_comparison(results: list[ProviderResult]) -> None:
    """Render side-by-side comparison of all providers sorted by median total."""
    if not results:
        console.print("[dim]No results to compare.[/dim]")
        return

    def sort_key(r: ProviderResult) -> float:
        stats = r.phase_stats.get("total")
        return stats.median if stats else float("inf")

    sorted_results = sorted(results, key=sort_key)

    table = Table(
        show_header=True,
        border_style="bright_black",
        expand=False,
        pad_edge=True,
        header_style="bold",
        title="[bold]CDN Comparison[/bold] [dim](sorted by median total latency)[/dim]",
        title_style="",
    )
    table.add_column("#", justify="right", width=3, style="dim")
    table.add_column("Provider", style="bold", min_width=12)
    table.add_column("PoP", min_width=5)
    table.add_column("DNS", justify="right")
    table.add_column("TCP", justify="right")
    table.add_column("TLS", justify="right")
    table.add_column("TTFB", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Jitter", justify="right")
    table.add_column("Hops", justify="right")
    table.add_column("Latency", min_width=20)

    max_total = max(
        (r.phase_stats.get("total", LatencyStats()).median for r in sorted_results),
        default=1.0,
    )
    if max_total <= 0:
        max_total = 1.0

    for rank, r in enumerate(sorted_results, 1):
        if r.error and not r.is_reachable:
            table.add_row(
                str(rank),
                r.provider_name, "\u2014", "\u2014", "\u2014", "\u2014", "\u2014", "\u2014", "\u2014", "\u2014",
                Text("unreachable", style="red"),
            )
            continue

        stats = r.phase_stats
        pop_code = r.pop.code or "\u2014"

        def _stat_val(phase: str) -> Text:
            s = stats.get(phase)
            return _fmt_ms(s.median, phase) if s else Text("\u2014", style="dim")

        total_stats = stats.get("total")
        total_median = total_stats.median if total_stats else 0
        jitter = total_stats.jitter if total_stats else 0

        hops = "\u2014"
        if r.network_path and r.network_path.total_hops > 0:
            hops = str(r.network_path.total_hops)

        bar_width = 20
        filled = int((total_median / max_total) * bar_width) if max_total > 0 else 0
        filled = min(filled, bar_width)
        color = _color_for_ms(total_median, "total")
        # NOTE: keep the glyphs out of the f-string expressions \u2014 backslash
        # escapes inside f-string expressions are a SyntaxError before 3.12.
        bar = (
            f"[{color}]{_BAR_FILLED * filled}[/{color}]"
            f"[bright_black]{_BAR_EMPTY * (bar_width - filled)}[/bright_black]"
        )

        table.add_row(
            str(rank),
            r.provider_name,
            pop_code,
            _stat_val("dns"),
            _stat_val("tcp"),
            _stat_val("tls"),
            _stat_val("ttfb"),
            _stat_val("total"),
            _fmt_ms(jitter, "total"),
            hops,
            bar,
        )

    console.print()
    console.print(table)
    console.print()


# ── Full result rendering ─────────────────────────────────────────────


def _render_provider_separator(name: str) -> None:
    """Print a large ASCII banner to visually separate providers."""
    width = max(len(name) + 8, 50)
    bar = "═" * width
    pad = (width - len(name) - 4) // 2
    console.print()
    console.print(f"[bold cyan]╔{bar}╗[/bold cyan]")
    console.print(f"[bold cyan]║{' ' * pad}  {name}  {' ' * (width - pad - len(name) - 4)}║[/bold cyan]")
    console.print(f"[bold cyan]╚{bar}╝[/bold cyan]")


def render_full(result: FullResult, verbose: bool = False) -> None:
    """Render the complete measurement results."""
    if result.geo and not result.geo.error:
        render_geo(result.geo)

    for pr in result.providers:
        _render_provider_separator(pr.provider_name)
        if pr.error and not pr.is_reachable:
            console.print(f"  [red]{pr.error}[/red]")
            continue

        _render_provider(pr, result.geo, verbose)

    reachable = [p for p in result.providers if p.is_reachable]
    if len(reachable) > 1:
        render_comparison(reachable)


def render_error(message: str) -> None:
    """Display an error message."""
    console.print(f"[bold red]Error:[/bold red] {message}")


def render_warning(message: str) -> None:
    """Display a warning message."""
    console.print(f"[yellow]Warning:[/yellow] {message}")
