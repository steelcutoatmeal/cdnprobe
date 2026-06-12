"""CLI entry point and orchestration for cdnprobe."""

from __future__ import annotations

import asyncio
import datetime
import os
import sys
import time as _time

import click

from cdnprobe import __version__
from cdnprobe.config import (
    DEFAULT_CONCURRENCY,
    DEFAULT_DELAY_MS,
    DEFAULT_MAX_HOPS,
    DEFAULT_SAMPLES,
    DEFAULT_TIMEOUT,
    DEFAULT_WARMUP,
)
from cdnprobe.models import FullResult, MeasurementConfig


@click.command()
@click.option("-p", "--providers", default="", help="Comma-separated providers [default: all]")
@click.option("-n", "--samples", default=DEFAULT_SAMPLES, type=click.IntRange(min=1), help="Samples per provider", show_default=True)
@click.option("-w", "--warmup", default=DEFAULT_WARMUP, type=click.IntRange(min=0), help="Warmup requests (discarded)", show_default=True)
@click.option("--no-warmup", is_flag=True, help="Disable warmup")
@click.option("-d", "--delay", default=DEFAULT_DELAY_MS, type=click.IntRange(min=0), help="Inter-sample delay in ms", show_default=True)
@click.option("-t", "--timeout", default=DEFAULT_TIMEOUT, type=click.FloatRange(min=0.1), help="Request timeout in seconds", show_default=True)
@click.option("--dns-server", default=None, help="Custom DNS server (e.g., 8.8.8.8)")
@click.option("-4", "--ipv4-only", is_flag=True, help="Force IPv4")
@click.option("-6", "--ipv6-only", is_flag=True, help="Force IPv6")
@click.option("--trace/--no-trace", default=True, help="Enable/disable network path tracing", show_default=True)
@click.option("--max-hops", default=DEFAULT_MAX_HOPS, type=click.IntRange(min=1, max=64), help="Max hops for traceroute", show_default=True)
@click.option("-c", "--concurrency", default=DEFAULT_CONCURRENCY, type=click.IntRange(min=1), help="Providers measured at the same time", show_default=True)
@click.option("--json", "json_output", is_flag=True, help="Output JSON to stdout")
@click.option("--csv", "csv_output", is_flag=True, help="Output CSV to stdout")
@click.option("-o", "--output", default=None, help="Write results to file")
@click.option("-q", "--quiet", is_flag=True, help="Suppress progress, show only results")
@click.option("-v", "--verbose", is_flag=True, help="Show per-sample details")
@click.option("--no-geo", is_flag=True, help="Skip geolocation lookup")
@click.option("--compare", is_flag=True, help="Show only summary comparison table")
@click.option("--url", default=None, help="Custom probe URL (creates a generic provider)")
@click.option("--repeat", default=1, type=click.IntRange(min=1), help="Number of measurement rounds", show_default=True)
@click.option("--interval", default=60, type=click.IntRange(min=0), help="Seconds between rounds (used with --repeat)", show_default=True)
@click.version_option(version=__version__)
def main(
    providers: str,
    samples: int,
    warmup: int,
    no_warmup: bool,
    delay: int,
    timeout: float,
    dns_server: str | None,
    ipv4_only: bool,
    ipv6_only: bool,
    trace: bool,
    max_hops: int,
    concurrency: int,
    json_output: bool,
    csv_output: bool,
    output: str | None,
    quiet: bool,
    verbose: bool,
    no_geo: bool,
    compare: bool,
    url: str | None,
    repeat: int,
    interval: int,
) -> None:
    """cdnprobe — CDN PoP Latency Measurement Tool.

    Measures latency to CDN Points of Presence with per-phase timing
    breakdown (DNS, TCP, TLS, TTFB, Transfer) and network path tracing with ASN info.
    """
    if ipv4_only and ipv6_only:
        raise click.UsageError("--ipv4-only and --ipv6-only are mutually exclusive")

    # Check for proxy warnings
    if not quiet and not json_output and not csv_output:
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            if os.environ.get(var):
                from cdnprobe.display import render_warning
                render_warning(f"Proxy detected ({var}={os.environ[var]}) — results may not reflect direct CDN routing")
                break

    config = MeasurementConfig(
        providers=[p.strip().lower() for p in providers.split(",") if p.strip()] if providers else [],
        samples=samples,
        warmup=0 if no_warmup else warmup,
        delay_ms=delay,
        timeout=timeout,
        dns_server=dns_server,
        ipv4_only=ipv4_only,
        ipv6_only=ipv6_only,
        trace_enabled=trace,
        max_hops=max_hops,
        concurrency=concurrency,
        verbose=verbose,
        quiet=quiet,
        no_geo=no_geo,
        compare_only=compare,
        json_output=json_output,
        csv_output=csv_output,
        output_file=output,
    )

    try:
        for round_num in range(1, repeat + 1):
            if repeat > 1 and not quiet and not json_output and not csv_output:
                from cdnprobe.display import console
                if round_num > 1:
                    console.print()
                console.print(f"[bold]━━━ Round {round_num}/{repeat} ━━━[/bold]")

            result = asyncio.run(_run(config, custom_url=url))
            _handle_output(result, config)

            # Sleep between rounds (not after the last one)
            if round_num < repeat:
                if not quiet and not json_output and not csv_output:
                    from cdnprobe.display import console
                    console.print(f"\n[dim]Next round in {interval}s...[/dim]")
                _time.sleep(interval)

    except KeyboardInterrupt:
        if not quiet and not json_output and not csv_output:
            from cdnprobe.display import console
            console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(130)


async def _run(config: MeasurementConfig, custom_url: str | None = None) -> FullResult:
    """Main async orchestration."""
    from cdnprobe.display import ProgressTracker, console, render_warning
    from cdnprobe.engine import measure_all, measure_provider
    from cdnprobe.location import get_geolocation
    from cdnprobe.providers import create_generic_provider, get_provider_map, list_providers
    from cdnprobe.trace import trace_all

    # Validate providers
    available = list_providers()
    if config.providers:
        unknown = [p for p in config.providers if p not in available]
        if unknown:
            from cdnprobe.display import render_error
            render_error(f"Unknown providers: {', '.join(unknown)}. Available: {', '.join(available)}")
            sys.exit(1)
        slugs = config.providers
    else:
        slugs = available

    # Geolocate before measuring so the lookup's own HTTP traffic can't
    # contend with latency samples.  Capped so a slow geo API chain never
    # stalls the run.
    geo = None
    if not config.no_geo:
        try:
            geo = await asyncio.wait_for(get_geolocation(), timeout=8.0)
        except Exception:
            geo = None

    # Set up progress tracking (only track actual samples, not warmup)
    all_slugs = list(slugs)
    if custom_url:
        all_slugs.append("custom")

    progress = None
    if not config.quiet and not config.json_output and not config.csv_output:
        progress = ProgressTracker(all_slugs, config.samples)

    # Progress callback
    def on_progress(provider_slug: str, sample_index: int, total: int, sample_result):
        if progress:
            if sample_result is not None:
                # Sample completed
                completed = sample_index + 1
                if sample_result.error:
                    status = "error" if completed >= total else "sampling"
                elif completed >= total:
                    status = "done"
                else:
                    status = "sampling"
                progress.update(provider_slug, completed, status)
            else:
                # Sample starting
                progress.update(provider_slug, sample_index, "sampling")

    # Run measurements
    if progress:
        provider_count = len(all_slugs)
        console.print(f"[bold]Measuring {provider_count} CDN provider{'s' if provider_count != 1 else ''}, {config.samples} samples each...[/bold]\n")
        progress.start()

    try:
        provider_results = await measure_all(config, progress_callback=on_progress)

        # Measure custom URL if provided
        if custom_url:
            generic = create_generic_provider(custom_url)
            custom_result = await measure_provider(generic, config, progress_callback=on_progress)
            provider_results.append(custom_result)
    finally:
        if progress:
            progress.finish()

    # Traceroute (concurrent for all providers)
    if config.trace_enabled:
        if not config.quiet and not config.json_output and not config.csv_output:
            console.print(f"\n[bold]Tracing network paths...[/bold]")

        targets = {}
        for pr in provider_results:
            if pr.resolved_ip:
                targets[pr.provider_slug] = pr.resolved_ip

        if targets:
            paths = await trace_all(targets, max_hops=config.max_hops)
            for pr in provider_results:
                if pr.provider_slug in paths:
                    pr.network_path = paths[pr.provider_slug]

    return FullResult(
        geo=geo,
        providers=provider_results,
        config=config,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


def _handle_output(result: FullResult, config: MeasurementConfig) -> None:
    """Handle output rendering and export."""
    from cdnprobe.display import console, render_comparison, render_full
    from cdnprobe.export import export_csv, export_json, write_to_file

    # JSON output
    if config.json_output:
        json_str = export_json(result)
        if config.output_file:
            write_to_file(json_str, config.output_file)
            if not config.quiet:
                console.print(f"[dim]Results written to {config.output_file}[/dim]")
        else:
            click.echo(json_str)
        return

    # CSV output
    if config.csv_output:
        csv_str = export_csv(result)
        if config.output_file:
            write_to_file(csv_str, config.output_file)
            if not config.quiet:
                console.print(f"[dim]Results written to {config.output_file}[/dim]")
        else:
            click.echo(csv_str)
        return

    # Rich terminal output
    if config.compare_only:
        reachable = [p for p in result.providers if p.is_reachable]
        render_comparison(reachable)
    else:
        render_full(result, verbose=config.verbose)

    # Also write to file if -o specified (non-json/csv mode writes JSON)
    if config.output_file:
        json_str = export_json(result)
        write_to_file(json_str, config.output_file)
        console.print(f"\n[dim]Results written to {config.output_file}[/dim]")


if __name__ == "__main__":
    main()
