# cdnprobe

A Python CLI tool that measures latency to CDN Points of Presence with granular per-phase timing breakdown (DNS, TCP, TLS, TTFB, Transfer) and traces the network path to each CDN showing every hop with ASN information.

## Features

- **Per-phase latency breakdown** — DNS resolution, TCP connect, TLS handshake, TTFB, and transfer measured independently
- **Accurate TTFB** — HTTP requests are sent directly on the existing TLS socket (h2 or HTTP/1.1), so TTFB reflects pure application latency with no hidden TCP+TLS double-counting
- **PoP detection** — Automatically identifies which CDN edge location you're routed to (e.g., DFW, DEN, LAX), including rDNS-based detection for Google
- **Custom URL probing** — Measure any endpoint with `--url`, not just built-in CDN providers
- **Repeat/watch mode** — Run measurements repeatedly with `--repeat` and `--interval` for monitoring
- **23 CDN providers** — Cloudflare, CloudFront, Fastly, Akamai, Azure CDN, Google, Gcore, Imperva, CacheFly, KeyCDN, CDN77, Sucuri, Bunny.net, Alibaba Cloud, Blazing CDN, Beluga CDN, CDNetworks, Tencent Cloud, BytePlus, Kingsoft Cloud, Medianova, EdgeNext, QUIC.cloud, plus any custom URL
- **Statistical aggregation** — Min, avg, median, P95, max, stdev, jitter across samples (with Bessel's correction for sample variance)
- **User geolocation** — Shows your IP, location, ISP, and distance to each detected PoP
- **Multiple output formats** — Rich terminal tables, JSON (with timestamp), CSV (with timestamp and transfer stats)
- **Network path tracing** — Traceroute to each CDN with ASN ownership via Team Cymru DNS lookups
- **Capped concurrency** — Providers are measured a few at a time (default 4, tunable via `--concurrency`) so simultaneous probes don't contend for bandwidth and skew the latency being measured; samples run sequentially per provider with fresh connections

## Installation

```bash
cd cdnprobe
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quick Start

```bash
# Measure all 23 CDN providers (default: 5 samples each)
cdnprobe

# Single provider, 3 samples, verbose per-sample detail
cdnprobe -p cloudflare -n 3 -v

# Multiple providers, comparison table only
cdnprobe -p cloudflare,cloudfront,google --compare

# Measure a custom URL
cdnprobe --url https://example.com -n 3

# Repeat measurements every 30 seconds, 5 rounds
cdnprobe -p cloudflare --repeat 5 --interval 30

# Skip traceroute for faster results
cdnprobe --no-trace

# Export to JSON (includes timestamp)
cdnprobe --json -o results.json

# Export to CSV (includes timestamp and transfer stats)
cdnprobe --csv -o results.csv

# Use a custom DNS server (bypasses OS DNS cache)
cdnprobe --dns-server 8.8.8.8

# Quiet mode (no progress bar)
cdnprobe -q
```

You can also run it as a module:

```bash
python -m cdnprobe -p cloudflare -n 3
```

## CLI Options

```
Usage: cdnprobe [OPTIONS]

Options:
  -p, --providers TEXT   Comma-separated providers [default: all]
                         Available: akamai, alibaba, azure, belugacdn, blazingcdn,
                         bunny, byteplus, cachefly, cdn77, cdnetworks,
                         cloudflare, cloudfront, edgenext, fastly, gcore, google,
                         imperva, keycdn, kingsoft, medianova, quiccloud, sucuri,
                         tencent
  -n, --samples INTEGER  Samples per provider [default: 5]
  -w, --warmup INTEGER   Warmup requests (discarded) [default: 1]
  --no-warmup            Disable warmup
  -d, --delay INTEGER    Inter-sample delay in ms [default: 100]
  -t, --timeout FLOAT    Request timeout in seconds [default: 10.0]
  --dns-server TEXT      Custom DNS server (e.g., 8.8.8.8)
  -4, --ipv4-only        Force IPv4
  -6, --ipv6-only        Force IPv6
  --trace / --no-trace   Enable/disable network path tracing [default: trace]
  --max-hops INTEGER     Max hops for traceroute [default: 30]
  -c, --concurrency INT  Providers measured at the same time [default: 4]
  --json                 Output JSON to stdout
  --csv                  Output CSV to stdout
  -o, --output FILE      Write results to file
  -q, --quiet            Suppress progress, show only results
  -v, --verbose          Show per-sample details
  --no-geo               Skip geolocation lookup
  --compare              Show only summary comparison table
  --url TEXT             Custom probe URL (creates a generic provider)
  --repeat INTEGER       Number of measurement rounds [default: 1]
  --interval INTEGER     Seconds between rounds (used with --repeat) [default: 60]
  --version              Show the version and exit.
  --help                 Show this message and exit.
```

## Output

### Per-Provider Detail

Each provider shows a phase breakdown stats table with all 5 phases plus total:

```
Cloudflare — PoP: DEN (Denver, US) — 623 km away
┌──────────┬────────┬────────┬────────┬────────┬────────┬────────┐
│ Phase    │    Min │    Avg │ Median │    P95 │    Max │ Jitter │
├──────────┼────────┼────────┼────────┼────────┼────────┼────────┤
│ DNS      │ 15.2ms │ 15.6ms │ 15.7ms │ 16.0ms │ 16.1ms │  0.7ms │
│ TCP      │ 13.0ms │ 14.0ms │ 14.1ms │ 14.9ms │ 15.0ms │  1.0ms │
│ TLS      │ 18.0ms │ 19.3ms │ 18.9ms │ 20.9ms │ 21.1ms │  1.9ms │
│ TTFB     │ 15.8ms │ 16.4ms │ 16.3ms │ 17.1ms │ 17.2ms │  1.1ms │
│ Transfer │  0.0ms │  0.0ms │  0.0ms │  0.1ms │  0.1ms │  0.1ms │
├──────────┼────────┼────────┼────────┼────────┼────────┼────────┤
│ Total    │ 63.4ms │ 65.5ms │ 64.6ms │ 68.1ms │ 68.5ms │  2.5ms │
└──────────┴────────┴────────┴────────┴────────┴────────┴────────┘
  Edge IP: 162.159.140.220 | TLS: TLSv1.3 | HTTP/1.1
```

Note: TTFB reflects only the time from sending the HTTP request to receiving the first response byte on the already-established TLS connection — it does not include a redundant TCP+TLS handshake.

### Network Path

Hop-by-hop traceroute with reverse DNS and ASN ownership:

```
Network Path (16 hops, 4 ASNs traversed)
┌─────┬─────────────────┬──────────────────────────────────────┬────────┬──────────────────────────────┐
│ Hop │ IP              │ Hostname                             │    RTT │ ASN                          │
├─────┼─────────────────┼──────────────────────────────────────┼────────┼──────────────────────────────┤
│  1  │ X.X.X.X         │ —                                    │  1.8ms │ (private)                    │
│  2  │ X.X.X.X         │ —                                    │  2.1ms │ AS13614 ALL-WEST, US         │
│  3  │ 135.129.252.170 │ —                                    │  2.4ms │ AS13614 ALL-WEST, US         │
│  4  │ 154.54.85.37    │ be4484.ccr32.slc01.atlas.cogentco.c… │  5.1ms │ AS174 COGENT-174, US         │
│  5  │ 154.54.42.97    │ be3036.ccr22.den01.atlas.cogentco.c… │ 11.2ms │ AS174 COGENT-174, US         │
│  6  │ 154.54.5.146    │ be8969.ccr32.slc01.atlas.cogentco.c… │  4.3ms │ AS174 COGENT-174, US         │
│  7  │ 154.54.31.34    │ be3382.ccr21.den01.atlas.cogentco.c… │ 12.8ms │ AS174 COGENT-174, US         │
│  8  │ 154.54.7.129    │ be3109.ccr21.mci01.atlas.cogentco.c… │ 13.1ms │ AS174 COGENT-174, US         │
│  9  │ *               │                                      │      * │                              │
│ 10  │ *               │                                      │      * │                              │
│ 11  │ 198.51.100.2    │ ix-ae-12-0.tcore2.DEN.net.telstra.c… │ 13.5ms │ AS4637 TELSTRA-AS, AU        │
│ 12  │ *               │                                      │      * │                              │
│ 13  │ 172.68.32.10    │ —                                    │ 14.5ms │ AS13335 CLOUDFLARENET, US     │
│ 14  │ 162.159.140.220 │ —                                    │ 13.9ms │ AS13335 CLOUDFLARENET, US     │
└─────┴─────────────────┴──────────────────────────────────────┴────────┴──────────────────────────────┘
  target reached ✓
```

### Summary Comparison

When measuring multiple providers, a comparison table is shown sorted by median total latency with color-coded bars:

```
CDN Comparison (sorted by median total latency)
┌──────────────┬──────┬───────┬───────┬───────┬───────┬───────┬────────┬──────┬──────────────────────┐
│ Provider     │ PoP  │   DNS │   TCP │   TLS │  TTFB │ Total │ Jitter │ Hops │ Latency Bar          │
├──────────────┼──────┼───────┼───────┼───────┼───────┼───────┼────────┼──────┼──────────────────────┤
│ Cloudflare   │ DEN  │ 17ms  │ 14ms  │ 17ms  │ 16ms  │  65ms │  2ms   │   16 │ ████████████░░░░░░░░ │
│ Google       │ DEN  │ 16ms  │ 15ms  │ 27ms  │ 18ms  │  76ms │  3ms   │   15 │ ██████████████░░░░░░ │
│ CloudFront   │ DEN  │ 35ms  │ 16ms  │ 19ms  │ 22ms  │  93ms │ 24ms   │    — │ ████████████████░░░░ │
│ Akamai       │ —    │ 19ms  │ 29ms  │ 31ms  │ 30ms  │ 110ms │ 26ms   │   18 │ ████████████████████ │
└──────────────┴──────┴───────┴───────┴───────┴───────┴───────┴────────┴──────┴──────────────────────┘
```

## How It Works

### Measurement Phases

Each sample measures 5 phases independently using monotonic high-resolution timing (`time.perf_counter()`):

| Phase | Technique |
|---|---|
| **DNS** | `dnspython` async resolver, supports custom DNS server and IPv4/IPv6 filtering |
| **TCP** | `asyncio.open_connection()` to the resolved IP |
| **TLS** | `start_tls()` upgrade on the TCP connection (fallback to combined TCP+TLS) |
| **TTFB** | HTTP request sent directly on the existing TLS socket via `h2` (HTTP/2) or raw HTTP/1.1 — time from request send to first response bytes |
| **Transfer** | Time from first byte to last byte of response body |

The TLS socket is kept open and reused for the HTTP request. ALPN negotiation during TLS determines whether HTTP/2 (`h2` library) or HTTP/1.1 (raw socket) is used. This ensures TTFB measures only application-level latency, not a redundant second TCP+TLS handshake.

Each sample creates a **fresh connection** (no keepalive/reuse) to ensure independent measurements.

> **Note on DNS caching:** On macOS, the system DNS cache (`mDNSResponder`) may cache DNS responses, making DNS timing for samples 2+ artificially fast. Use `--dns-server 8.8.8.8` to bypass the OS cache for more accurate per-sample DNS measurements.

### PoP Detection

Each CDN provider uses a different method to identify the serving edge location:

| Provider | Method | Confidence |
|---|---|---|
| **Cloudflare** | `colo=XXX` in `/cdn-cgi/trace` response body | Confirmed |
| **CloudFront** | `x-amz-cf-pop` response header (e.g., `DFW55-C1`) | Confirmed |
| **Fastly** | `X-Served-By` header, trailing IATA code (e.g., `cache-dfw18681-DFW`) | Confirmed |
| **CDN77** | `x-77-pop` response header | Confirmed |
| **QUIC.cloud** | `x-qc-pop` response header | Confirmed |
| **Akamai** | `X-Cache` edge hostname or `server-timing` header with debug Pragma headers | Best effort |
| **Azure CDN** | `x-msedge-ref` header `Ref B:` field (e.g., `CO1EDGE2922` → `CO1`) | Inferred |
| **Google** | Reverse DNS of resolved IP (e.g., `dfw25s42-in-f4.1e100.net` → `DFW`) | Inferred |
| **Gcore** | `x-id` response header | Inferred |
| **Imperva** | `x-iinfo` / `x-cdn` response headers | Inferred |
| **CacheFly** | `x-served-by` response header | Inferred |
| **KeyCDN** | `x-edge-location` response header (e.g., `fran`, `lond`) | Inferred |
| **Sucuri** | `x-sucuri-id` response header | Inferred |
| **Bunny.net** | `cdn-requestid` header (e.g., `DE-FRA-...`) | Inferred |
| **Alibaba Cloud** | `via` header with `ens-cache` node identifiers / `eagleid` header | Inferred |
| **CDNetworks** | `via` response header | Inferred |
| **Tencent Cloud** | `x-nws-log-uuid` / `x-cache-lookup` headers | Inferred |
| **BytePlus** | `x-tt-trace-tag` / `x-bdcdn-cache-status` headers | Inferred |
| **Beluga CDN** | `x-beluga-node` response header | Inferred |
| **Kingsoft Cloud** | `x-cache-status` header with `KS-CLOUD-*` node identity | Inferred |
| **Medianova** | `x-cdn` response header | Inferred |
| **EdgeNext** | `via` response header | Inferred |
| **Blazing CDN** | `x-cdn-host-id` response header | Inferred |
| **Custom** (`--url`) | No provider-specific detection | Unknown |

### Probe URL Selection

Each provider's probe URL is chosen to ensure the response actually traverses that CDN's edge network. Key considerations:

- **Small responses**: Most providers use `/favicon.ico` (1–5 KB) to minimize transfer time variance
- **Purpose-built endpoints**: Cloudflare uses `/cdn-cgi/trace` (diagnostic endpoint), Google uses `/generate_204` (zero-body response)
- **CDN-verified**: Each URL has been verified to return headers from the target CDN. Some CDN vendor websites are fronted by *other* CDNs (e.g., `alibabacloud.com` is behind Akamai, `microsoft.com` is behind Fastly), so alternative domains that use the vendor's own CDN are used instead
- **Redirect-free**: Probe URLs avoid 3xx redirects; the engine logs a warning if a redirect is detected since timing would reflect the redirect response, not actual CDN edge latency

> **Note on BytePlus**: BytePlus uses Akamai as an edge layer in front of their own CDN infrastructure. Latency measurements include Akamai's edge, but BytePlus CDN-specific headers (`x-bdcdn-cache-status`, `server: TLB`) are present to confirm the BytePlus layer is active.

### Network Path Tracing

Traces the route to each CDN's resolved IP address:

1. **Primary**: `icmplib.traceroute()` — pure Python, cross-platform, individual per-probe RTTs
2. **Fallback**: System `/usr/sbin/traceroute -n` if ICMP permissions fail

For each hop IP, two concurrent DNS lookups are performed:
- **ASN info**: Team Cymru DNS (`<reversed-ip>.origin.asn.cymru.com` TXT record)
- **Reverse DNS**: PTR record lookup for hostname

Results are cached per-IP to avoid duplicate lookups when multiple providers share intermediate hops. The cache is cleared at the start of each `trace_all()` call to prevent unbounded growth.

### Concurrency Model

- **Inter-provider**: Providers run concurrently, capped at `--concurrency` (default 4) at a time. The cap is deliberate: fully parallel probes contend for the uplink and skew the latency being measured. Raise it to trade accuracy for speed, or use `--concurrency 1` for fully serial measurement
- **Intra-provider**: Samples run sequentially with configurable delay (default 100ms)
- **Geolocation**: Resolved before sampling starts (capped at 8s) so its HTTP traffic can't interfere with latency samples
- **Traceroute**: Runs concurrently for all providers after latency sampling completes
- **ASN/rDNS**: All hop lookups run concurrently within each trace

## Project Structure

```
cdnprobe/
├── pyproject.toml          # Package config and dependencies
├── cdnprobe/
│   ├── __init__.py         # Package version
│   ├── __main__.py         # python -m cdnprobe entry point
│   ├── cli.py              # Click CLI, async orchestration, repeat/watch mode
│   ├── config.py           # Constants, color thresholds, phase definitions
│   ├── models.py           # Dataclasses (TimingBreakdown, HopInfo, NetworkPath, etc.)
│   ├── engine.py           # Core async measurement (DNS/TCP/TLS/TTFB/Transfer via h2 or h1)
│   ├── trace.py            # Traceroute + Team Cymru ASN lookup + reverse DNS
│   ├── stats.py            # Statistical aggregation (min/avg/median/p95/stdev/jitter)
│   ├── location.py         # User IP geolocation via free APIs
│   ├── display.py          # Rich terminal output (tables, progress bars, path diagrams)
│   ├── export.py           # JSON/CSV export (with timestamp and transfer stats)
│   ├── providers/
│   │   ├── __init__.py     # Provider registry + generic provider factory
│   │   ├── base.py         # Abstract CDNProvider base class (with optional IP-based PoP detection)
│   │   ├── generic.py      # Generic provider for custom --url endpoints
│   │   ├── cloudflare.py
│   │   ├── cloudfront.py
│   │   ├── fastly.py
│   │   ├── akamai.py
│   │   ├── azure.py
│   │   ├── google.py       # Includes rDNS-based PoP detection
│   │   ├── gcore.py
│   │   ├── imperva.py
│   │   ├── cachefly.py
│   │   ├── keycdn.py
│   │   ├── cdn77.py
│   │   ├── sucuri.py
│   │   ├── bunny.py
│   │   ├── alibaba.py
│   │   ├── blazingcdn.py
│   │   ├── belugacdn.py
│   │   ├── cdnetworks.py
│   │   ├── tencent.py
│   │   ├── byteplus.py
│   │   ├── kingsoft.py
│   │   ├── medianova.py
│   │   ├── edgenext.py
│   │   └── quiccloud.py
│   └── data/
│       └── iata_codes.json # 319 IATA codes with city/country/coordinates
```

## Dependencies

| Package | Purpose |
|---|---|
| [httpx](https://www.python-httpx.org/) (with HTTP/2) | Async HTTP client for PoP detection requests |
| [h2](https://python-hyper.org/projects/h2/) | HTTP/2 protocol implementation for sending requests on existing TLS sockets |
| [dnspython](https://www.dnspython.org/) | Async DNS resolution, PTR lookups, Team Cymru ASN queries |
| [rich](https://rich.readthedocs.io/) | Terminal tables, progress bars, live display, color coding |
| [click](https://click.palletsprojects.com/) | CLI argument parsing |
| [icmplib](https://github.com/ValentinBELYN/icmplib) | Cross-platform traceroute with unprivileged ICMP fallback |

## Error Handling

cdnprobe is designed to degrade gracefully:

- **Timeouts**: Excluded from stats; provider marked "unreachable" if all samples fail
- **DNS failures**: Remaining phases skipped for that sample
- **Rate limiting (429)**: Backs off once (2s), then marks as rate-limited
- **TLS issues**: Falls back to combined TCP+TLS timing on a new connection
- **HTTP/2 stream reset**: Raises an error for the sample, other samples continue
- **Traceroute permissions**: Tries `icmplib` first, falls back to system `traceroute`
- **ASN lookup failures**: Hop shown without ASN info
- **Proxy detection**: Warns if `HTTP_PROXY`/`HTTPS_PROXY` env vars are set
- A single provider failure never crashes the entire run
