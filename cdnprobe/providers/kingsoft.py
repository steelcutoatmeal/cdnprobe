"""Kingsoft Cloud CDN provider."""

from __future__ import annotations

import httpx

from cdnprobe.models import PoPIdentity
from cdnprobe.providers.base import CDNProvider


class KingsoftProvider(CDNProvider):
    """Kingsoft Cloud CDN detection via response headers.

    Kingsoft Cloud (ksyun.com) is a major Chinese cloud CDN backed
    by Xiaomi.  CDN-served content can be identified by domains
    in the ``*.ksyuncdn.com`` format and via standard cache headers.
    """

    @property
    def name(self) -> str:
        return "Kingsoft Cloud"

    @property
    def slug(self) -> str:
        return "kingsoft"

    @property
    def probe_url(self) -> str:
        return "https://fe.ksyun.com/favicon.ico"

    def detect_pop(self, response: httpx.Response) -> PoPIdentity:
        # X-Cache-Status contains PoP node: "MISS from KS-CLOUD-XG-FOREIGN-12-01".
        # The node name is not an IATA code, so it is kept in raw_header
        # rather than reported as a PoP code.
        cache_status = response.headers.get("x-cache-status", "")
        if cache_status:
            return PoPIdentity(confidence="inferred", raw_header=cache_status)
        cdn_req = response.headers.get("x-cdn-request-id", "")
        if cdn_req:
            return PoPIdentity(confidence="inferred", raw_header=cdn_req)
        return PoPIdentity(confidence="unknown")

    def extract_metadata(self, response: httpx.Response) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for header in ("server", "x-cache-status", "x-cdn-request-id", "x-link-via"):
            value = response.headers.get(header)
            if value:
                metadata[header] = value
        return metadata
