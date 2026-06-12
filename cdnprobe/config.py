"""Constants and configuration for cdnprobe."""

# Latency color thresholds (milliseconds)
FAST_THRESHOLD_MS = 20.0    # Green: <= 20ms
MEDIUM_THRESHOLD_MS = 50.0  # Yellow: <= 50ms
# Red: > 50ms

# Phase-specific thresholds for color coding
PHASE_THRESHOLDS = {
    "dns": {"fast": 5.0, "medium": 20.0},
    "tcp": {"fast": 10.0, "medium": 30.0},
    "tls": {"fast": 20.0, "medium": 50.0},
    "ttfb": {"fast": 30.0, "medium": 80.0},
    "transfer": {"fast": 10.0, "medium": 50.0},
    "total": {"fast": 50.0, "medium": 150.0},
}

# Default measurement settings
DEFAULT_SAMPLES = 5
DEFAULT_WARMUP = 1
DEFAULT_DELAY_MS = 100
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_HOPS = 30
# Providers measured at the same time.  Kept low on purpose: fully
# parallel probes contend for the uplink and skew the latency being
# measured.  Raise with --concurrency to trade accuracy for speed.
DEFAULT_CONCURRENCY = 4

# Traceroute settings
TRACE_PROBES_PER_HOP = 3
TRACE_HOP_TIMEOUT = 2.0

# ASN lookup DNS zones
CYMRU_ORIGIN_ZONE = "origin.asn.cymru.com"
CYMRU_ORIGIN6_ZONE = "origin6.asn.cymru.com"
CYMRU_PEER_ZONE = "peer.asn.cymru.com"

# Geolocation API fallback chain
GEO_APIS = [
    "https://ipinfo.io/json",
    "https://ipapi.co/json/",
    # ip-api.com free tier only supports HTTP; this is the last fallback
    "http://ip-api.com/json/?fields=status,message,query,city,regionName,country,lat,lon,isp,org,as",
]

# User agent for HTTP requests
from cdnprobe import __version__ as _version

USER_AGENT = f"cdnprobe/{_version}"

# Phase display names
PHASE_NAMES = ["dns", "tcp", "tls", "ttfb", "transfer", "total"]
PHASE_LABELS = {
    "dns": "DNS",
    "tcp": "TCP",
    "tls": "TLS",
    "ttfb": "TTFB",
    "transfer": "Transfer",
    "total": "Total",
}
