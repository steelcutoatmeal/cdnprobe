"""CacheFly CDN provider."""

from __future__ import annotations

import httpx

from cdnprobe.models import PoPIdentity
from cdnprobe.providers.base import CDNProvider, find_iata_token


class CacheFlyProvider(CDNProvider):
    """CacheFly CDN detection via response headers.

    CacheFly edge servers identify themselves via the ``server`` header
    (often containing ``CacheFly``) and may include cache node info
    in the ``x-cf-cachestatus`` or ``x-cache`` headers.
    """

    @property
    def name(self) -> str:
        return "CacheFly"

    @property
    def slug(self) -> str:
        return "cachefly"

    @property
    def probe_url(self) -> str:
        return "https://www.cachefly.com/wp-includes/images/w-logo-blue-white-bg.png"

    def detect_pop(self, response: httpx.Response) -> PoPIdentity:
        # CacheFly may embed PoP info in server or x-served-by headers
        server = response.headers.get("server", "")
        x_served = response.headers.get("x-served-by", "")
        raw = x_served or server or None

        if x_served:
            code = find_iata_token(x_served)
            if code:
                return PoPIdentity(
                    code=code,
                    confidence="inferred",
                    raw_header=raw,
                )

        return PoPIdentity(confidence="unknown", raw_header=raw)

    def extract_metadata(self, response: httpx.Response) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for header in ("server", "x-cache", "x-served-by", "x-cf-cachestatus", "via"):
            value = response.headers.get(header)
            if value:
                metadata[header] = value
        return metadata
