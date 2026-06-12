"""Network path tracing module.

Traces the route from the user to each CDN edge, showing every hop
with ASN ownership resolved via Team Cymru DNS.

Primary strategy uses icmplib's async_traceroute (pure Python, supports
unprivileged ICMP sockets on macOS). Falls back to shelling out to the
system traceroute binary when ICMP permissions are unavailable.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import shutil
from typing import Optional

import dns.asyncresolver
import dns.reversename

from cdnprobe.config import (
    CYMRU_ORIGIN6_ZONE,
    CYMRU_ORIGIN_ZONE,
    CYMRU_PEER_ZONE,
    TRACE_HOP_TIMEOUT,
    TRACE_PROBES_PER_HOP,
)
from cdnprobe.models import HopInfo, NetworkPath

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level ASN cache: shared across providers so hops that appear in
# multiple paths are only looked up once.
# ---------------------------------------------------------------------------
_asn_cache: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# ASN lookup helpers (Team Cymru DNS)
# ---------------------------------------------------------------------------

def _cymru_origin_qname(ip: str) -> str:
    """Build the Team Cymru origin query name for an IPv4 or IPv6 address.

    IPv4: reversed octets under origin.asn.cymru.com
          ('8.8.8.8' -> '8.8.8.8.origin.asn.cymru.com')
    IPv6: reversed nibbles of the exploded address under
          origin6.asn.cymru.com (same format as ip6.arpa).
    """
    addr = ipaddress.ip_address(ip)
    if addr.version == 6:
        nibbles = addr.exploded.replace(":", "")
        return ".".join(reversed(nibbles)) + f".{CYMRU_ORIGIN6_ZONE}"
    reversed_ip = ".".join(reversed(ip.split(".")))
    return f"{reversed_ip}.{CYMRU_ORIGIN_ZONE}"


def clear_asn_cache() -> None:
    """Clear the module-level ASN cache to prevent unbounded growth."""
    _asn_cache.clear()


async def _cymru_origin_lookup(
    ip: str,
    resolver: dns.asyncresolver.Resolver,
) -> dict:
    """Query Team Cymru for ASN origin info about *ip*.

    Returns a dict with keys: asn, prefix, country (or empty on failure).
    """
    qname = _cymru_origin_qname(ip)
    try:
        answers = await resolver.resolve(qname, "TXT")
        # First TXT record, strip surrounding quotes
        txt = str(answers[0]).strip('"')
        # Format: "15169 | 8.8.8.0/24 | US | arin | 2000-03-30"
        parts = [p.strip() for p in txt.split("|")]
        return {
            "asn": int(parts[0]) if parts[0] else None,
            "prefix": parts[1] if len(parts) > 1 else None,
            "country": parts[2] if len(parts) > 2 else None,
        }
    except Exception:
        logger.debug("Cymru origin lookup failed for %s", ip)
        return {}


async def _cymru_asn_name_lookup(
    asn: int,
    resolver: dns.asyncresolver.Resolver,
) -> Optional[str]:
    """Query Team Cymru for the human-readable name of *asn*.

    Returns a string like ``'GOOGLE, US'`` or None.
    """
    qname = f"AS{asn}.{CYMRU_PEER_ZONE}"
    try:
        answers = await resolver.resolve(qname, "TXT")
        txt = str(answers[0]).strip('"')
        # Format: "15169 | US | arin | 2000-03-30 | GOOGLE, US"
        parts = [p.strip() for p in txt.split("|")]
        if len(parts) >= 5:
            return parts[4]
        return None
    except Exception:
        logger.debug("Cymru ASN name lookup failed for AS%s", asn)
        return None


async def _lookup_asn(
    ip: str,
    resolver: dns.asyncresolver.Resolver,
) -> dict:
    """Full ASN lookup for a single IP.  Uses the module-level cache.

    Returns a dict with keys: asn, asn_name, prefix, country.
    """
    if ip in _asn_cache:
        return _asn_cache[ip]

    result: dict = {"asn": None, "asn_name": None, "prefix": None, "country": None}

    origin = await _cymru_origin_lookup(ip, resolver)
    if not origin:
        _asn_cache[ip] = result
        return result

    result["asn"] = origin.get("asn")
    result["prefix"] = origin.get("prefix")
    result["country"] = origin.get("country")

    if result["asn"] is not None:
        name = await _cymru_asn_name_lookup(result["asn"], resolver)
        result["asn_name"] = name

    _asn_cache[ip] = result
    return result


# ---------------------------------------------------------------------------
# Reverse DNS (PTR) lookup
# ---------------------------------------------------------------------------

async def _reverse_dns(
    ip: str,
    resolver: dns.asyncresolver.Resolver,
) -> Optional[str]:
    """Return the PTR hostname for *ip*, or None on failure."""
    try:
        rev_name = dns.reversename.from_address(ip)
        answers = await resolver.resolve(rev_name, "PTR")
        # Return the first PTR, strip trailing dot
        hostname = str(answers[0]).rstrip(".")
        return hostname
    except Exception:
        logger.debug("Reverse DNS lookup failed for %s", ip)
        return None


# ---------------------------------------------------------------------------
# Enrich hops with ASN + rDNS concurrently
# ---------------------------------------------------------------------------

async def _enrich_hops(hops: list[HopInfo]) -> None:
    """Enrich a list of HopInfo in-place with ASN and reverse-DNS data.

    All lookups are fired concurrently; failures are silently absorbed.
    """
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = TRACE_HOP_TIMEOUT

    # Build coroutine lists for hops that have a routable IP.
    asn_tasks: list[tuple[int, asyncio.Task]] = []
    rdns_tasks: list[tuple[int, asyncio.Task]] = []

    for idx, hop in enumerate(hops):
        if hop.ip is None or hop.is_private:
            continue
        asn_tasks.append((idx, asyncio.ensure_future(_lookup_asn(hop.ip, resolver))))
        rdns_tasks.append((idx, asyncio.ensure_future(_reverse_dns(hop.ip, resolver))))

    # Gather all concurrently — ASN and rDNS are independent.
    all_tasks = [t for _, t in asn_tasks] + [t for _, t in rdns_tasks]
    if all_tasks:
        await asyncio.gather(*all_tasks, return_exceptions=True)

    # Apply ASN results
    for idx, task in asn_tasks:
        try:
            info = task.result()
            hops[idx].asn = info.get("asn")
            hops[idx].asn_name = info.get("asn_name")
            hops[idx].prefix = info.get("prefix")
            hops[idx].country = info.get("country")
        except Exception:
            pass

    # Apply rDNS results
    for idx, task in rdns_tasks:
        try:
            hops[idx].hostname = task.result()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Primary traceroute: icmplib
# ---------------------------------------------------------------------------

async def _traceroute_icmplib(
    target_ip: str,
    max_hops: int,
) -> list[HopInfo]:
    """Run traceroute via icmplib's traceroute in a thread executor.

    icmplib provides a synchronous ``traceroute()`` — we run it in an
    executor to avoid blocking the event loop.  Raises on permission
    errors so the caller can fall back to the system binary.
    """
    from functools import partial

    from icmplib import traceroute  # local import to allow fallback

    loop = asyncio.get_running_loop()
    icmp_hops = await loop.run_in_executor(
        None,
        partial(
            traceroute,
            target_ip,
            count=TRACE_PROBES_PER_HOP,
            timeout=TRACE_HOP_TIMEOUT,
            max_hops=max_hops,
        ),
    )

    hops: list[HopInfo] = []
    for hop in icmp_hops:
        if not hop.is_alive or hop.packets_received == 0:
            # Timed-out hop
            hops.append(HopInfo(hop_number=hop.distance))
        else:
            hops.append(
                HopInfo(
                    hop_number=hop.distance,
                    ip=hop.address,
                    rtt_ms=hop.rtts,
                )
            )

    return hops


# ---------------------------------------------------------------------------
# Fallback traceroute: shell out to /usr/sbin/traceroute
# ---------------------------------------------------------------------------

# Regex for parsing a normal hop line:
#   " 3  96.120.68.137  8.432 ms  7.891 ms  8.123 ms"
_HOP_LINE_RE = re.compile(
    r"^\s*(\d+)\s+"           # hop number
    r"(\S+)"                  # IP or first *
    r"(.*)"                   # remainder (RTTs or more stars)
)
_RTT_RE = re.compile(r"([\d.]+)\s*ms")


def _parse_traceroute_output(output: str) -> list[HopInfo]:
    """Parse the textual output of ``traceroute -n``."""
    hops: list[HopInfo] = []
    for line in output.splitlines():
        line = line.strip()
        match = _HOP_LINE_RE.match(line)
        if not match:
            continue

        hop_num = int(match.group(1))
        first_token = match.group(2)
        remainder = match.group(3)

        # Detect full timeout line: "* * *"
        if first_token == "*" and remainder.replace("*", "").replace(" ", "") == "":
            hops.append(HopInfo(hop_number=hop_num))
            continue

        # Extract all RTTs from the full line (after hop number)
        full_rest = first_token + remainder
        rtts = [float(m.group(1)) for m in _RTT_RE.finditer(full_rest)]

        # The IP is the first non-* non-RTT token
        ip_addr: Optional[str] = None
        try:
            ipaddress.ip_address(first_token)
            ip_addr = first_token
        except ValueError:
            pass

        if ip_addr:
            hops.append(HopInfo(hop_number=hop_num, ip=ip_addr, rtt_ms=rtts))
        else:
            hops.append(HopInfo(hop_number=hop_num, rtt_ms=rtts))

    return hops


async def _traceroute_fallback(
    target_ip: str,
    max_hops: int,
) -> list[HopInfo]:
    """Run traceroute by shelling out to the system binary."""
    traceroute_bin = shutil.which("traceroute")
    if traceroute_bin is None:
        # Common macOS / Linux paths
        for candidate in ("/usr/sbin/traceroute", "/usr/bin/traceroute"):
            if shutil.which(candidate) is not None:
                traceroute_bin = candidate
                break
        if traceroute_bin is None:
            # Last-ditch: assume it is on PATH.
            traceroute_bin = "traceroute"

    cmd = [
        traceroute_bin,
        "-n",                    # numeric, no DNS (we do our own)
        "-m", str(max_hops),
        "-q", str(TRACE_PROBES_PER_HOP),
        "-w", str(int(TRACE_HOP_TIMEOUT)),
        target_ip,
    ]

    logger.debug("Fallback traceroute command: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(),
        timeout=max_hops * TRACE_HOP_TIMEOUT + 10,
    )

    if proc.returncode != 0:
        err_text = stderr.decode(errors="replace").strip()
        logger.warning("Fallback traceroute exited %d: %s", proc.returncode, err_text)

    return _parse_traceroute_output(stdout.decode(errors="replace"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def trace_path(
    target_ip: str,
    provider_slug: str,
    max_hops: int = 30,
) -> NetworkPath:
    """Trace the network path to *target_ip*.

    Tries icmplib first; falls back to the system ``traceroute`` binary if
    ICMP socket creation fails (e.g., insufficient privileges).
    """
    path = NetworkPath(provider_slug=provider_slug, target_ip=target_ip)

    # --- Step 1: obtain raw hops ------------------------------------------
    hops: list[HopInfo] = []
    try:
        hops = await _traceroute_icmplib(target_ip, max_hops)
        logger.debug("icmplib traceroute to %s succeeded (%d hops)", target_ip, len(hops))
    except Exception as exc:
        logger.debug(
            "icmplib traceroute failed (%s), falling back to system traceroute",
            exc,
        )
        try:
            hops = await _traceroute_fallback(target_ip, max_hops)
            logger.debug(
                "Fallback traceroute to %s succeeded (%d hops)", target_ip, len(hops)
            )
        except Exception as fallback_exc:
            logger.warning(
                "Both traceroute methods failed for %s: %s / %s",
                target_ip,
                exc,
                fallback_exc,
            )
            return path  # empty hops

    if not hops:
        return path

    # --- Step 2: enrich with ASN + rDNS -----------------------------------
    try:
        await _enrich_hops(hops)
    except Exception as exc:
        logger.warning("Hop enrichment failed for %s: %s", target_ip, exc)

    # --- Step 3: populate NetworkPath -------------------------------------
    path.hops = hops
    path.total_hops = len(hops)
    path.reached_target = any(
        h.ip == target_ip for h in hops
    )

    return path


async def trace_all(
    targets: dict[str, str],
    max_hops: int = 30,
) -> dict[str, NetworkPath]:
    """Trace paths to multiple targets concurrently.

    Parameters
    ----------
    targets:
        Mapping of ``{provider_slug: ip_address}``.
    max_hops:
        Maximum number of hops per trace.

    Returns
    -------
    dict[str, NetworkPath]
        Results keyed by provider slug.
    """
    clear_asn_cache()

    tasks = {
        slug: asyncio.ensure_future(trace_path(ip, slug, max_hops))
        for slug, ip in targets.items()
    }

    results: dict[str, NetworkPath] = {}
    gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

    for slug, result in zip(tasks.keys(), gathered):
        if isinstance(result, Exception):
            logger.warning("trace_path failed for %s: %s", slug, result)
            results[slug] = NetworkPath(
                provider_slug=slug,
                target_ip=targets[slug],
            )
        else:
            results[slug] = result

    return results
