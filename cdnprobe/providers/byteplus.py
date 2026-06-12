"""BytePlus CDN provider."""

from __future__ import annotations

import httpx

from cdnprobe.models import PoPIdentity
from cdnprobe.providers.base import CDNProvider


class BytePlusProvider(CDNProvider):
    """BytePlus CDN detection via response headers.

    BytePlus (ByteDance/TikTok's cloud CDN) uses ``x-bdcdn-cache-status``
    and ``x-response-cache`` headers to indicate its CDN layer.  The
    ``server: TLB`` header identifies BytePlus's own load balancer.

    Note: BytePlus uses Akamai as an edge layer in front of their own
    CDN infrastructure, so latency measurements include Akamai's edge.
    """

    @property
    def name(self) -> str:
        return "BytePlus"

    @property
    def slug(self) -> str:
        return "byteplus"

    @property
    def probe_url(self) -> str:
        return "https://lf16-tiktok-web.ttwstatic.com/obj/tiktok-web-common-sg/mtact/static/pwa/icon_128x128.png"

    def detect_pop(self, response: httpx.Response) -> PoPIdentity:
        # x-tt-trace-tag contains CDN cache info like "id=16;cdn-cache=hit;type=static"
        trace_tag = response.headers.get("x-tt-trace-tag", "")
        if trace_tag:
            return PoPIdentity(
                confidence="inferred",
                raw_header=trace_tag,
            )

        bdcdn = response.headers.get("x-bdcdn-cache-status", "")
        if bdcdn:
            return PoPIdentity(
                confidence="inferred",
                raw_header=bdcdn,
            )

        via = response.headers.get("via", "")
        if via:
            return PoPIdentity(
                confidence="inferred",
                raw_header=via,
            )
        return PoPIdentity(confidence="unknown")

    def extract_metadata(self, response: httpx.Response) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for header in (
            "server", "x-bdcdn-cache-status", "x-response-cache",
            "x-cache", "x-tt-trace-tag", "server-timing",
        ):
            value = response.headers.get(header)
            if value:
                metadata[header] = value
        return metadata
