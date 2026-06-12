"""Gcore CDN provider."""

from __future__ import annotations

import httpx

from cdnprobe.models import PoPIdentity
from cdnprobe.providers.base import CDNProvider, find_iata_token


class GcoreProvider(CDNProvider):
    """Gcore CDN detection via response headers.

    Gcore exposes edge location information in the ``x-id`` response
    header, which may contain a PoP identifier.  The ``server`` header
    typically reads ``Gcore CDN``.
    """

    @property
    def name(self) -> str:
        return "Gcore"

    @property
    def slug(self) -> str:
        return "gcore"

    @property
    def probe_url(self) -> str:
        return "https://gcore.com/favicon.ico"

    def detect_pop(self, response: httpx.Response) -> PoPIdentity:
        # Gcore may expose PoP info in x-id or similar headers
        x_id = response.headers.get("x-id", "")
        if x_id:
            # Try to extract a 3-letter code from the x-id value
            code = find_iata_token(x_id)
            if code:
                return PoPIdentity(
                    code=code,
                    confidence="inferred",
                    raw_header=x_id,
                )
        return PoPIdentity(confidence="unknown", raw_header=x_id or None)

    def extract_metadata(self, response: httpx.Response) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for header in ("server", "x-id", "x-cache", "x-cdn-cache", "via"):
            value = response.headers.get(header)
            if value:
                metadata[header] = value
        return metadata
