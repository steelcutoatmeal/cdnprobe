"""Abstract base class for CDN providers."""

from __future__ import annotations

import abc
import re
from typing import Optional

import httpx

from cdnprobe.models import PoPIdentity

# Three-letter uppercase tokens that show up in cache/status headers but
# are never PoP airport codes.  Without this filter, "x-cache: HIT" would
# be reported as the IATA code "HIT".
_NON_POP_TOKENS = {
    "HIT", "TCP", "UDP", "MEM", "RAM", "SSL", "TLS",
    "GET", "PUT", "VIA", "AGE", "CDN", "WAF", "BOT", "OFF",
}

_IATA_TOKEN_RE = re.compile(r"\b([A-Z]{3})\b")


def find_iata_token(text: str) -> Optional[str]:
    """Return the first 3-letter uppercase token that looks like a PoP code.

    Skips well-known cache-status words (HIT, TCP, ...) so generic header
    scraping doesn't produce false-positive PoP codes.
    """
    for match in _IATA_TOKEN_RE.finditer(text):
        token = match.group(1)
        if token not in _NON_POP_TOKENS:
            return token
    return None


class CDNProvider(abc.ABC):
    """Base class that each CDN provider must implement."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'Cloudflare')."""

    @property
    @abc.abstractmethod
    def slug(self) -> str:
        """Short identifier (e.g. 'cloudflare')."""

    @property
    @abc.abstractmethod
    def probe_url(self) -> str:
        """URL used for latency probing and PoP detection."""

    @property
    def extra_headers(self) -> dict[str, str]:
        """Extra headers to send with the probe request."""
        return {}

    @abc.abstractmethod
    def detect_pop(self, response: httpx.Response) -> PoPIdentity:
        """Extract PoP identity from the probe response.

        Implementations should parse headers/body and return a PoPIdentity
        with at least the `code` field set if detection succeeds.
        """

    def extract_metadata(self, response: httpx.Response) -> dict[str, str]:
        """Extract additional metadata from the response (cache status, etc.)."""
        return {}

    async def detect_pop_by_ip(self, ip: str) -> Optional[PoPIdentity]:
        """Attempt to detect the PoP via the resolved IP (e.g. rDNS).

        Subclasses may override this for IP-based PoP detection.
        Returns ``None`` if no detection is possible.
        """
        return None
