"""IATA airport-code lookup and PoP enrichment.

The bundled ``data/iata_codes.json`` maps 3-letter IATA codes to
city/country/lat/lon.  Enrichment happens in the measurement pipeline
(not at render time) so that terminal output and JSON/CSV exports see
the same data.
"""

from __future__ import annotations

import json
from pathlib import Path

from cdnprobe.models import PoPIdentity

_iata_db: dict | None = None


def load_iata_db() -> dict:
    """Load the IATA code database, caching it after the first read."""
    global _iata_db
    if _iata_db is None:
        path = Path(__file__).parent / "data" / "iata_codes.json"
        try:
            with open(path) as f:
                _iata_db = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _iata_db = {}
    return _iata_db


def enrich_pop(pop: PoPIdentity) -> None:
    """Fill in city/country/lat/lon from the IATA db where missing.

    Only fields that are unset are populated; provider-supplied values
    take precedence.  No-op if the code is unknown to the database.
    """
    if not pop.code:
        return
    info = load_iata_db().get(pop.code.upper())
    if not info:
        return
    if not pop.city:
        pop.city = info.get("city")
    if not pop.country:
        pop.country = info.get("country")
    if pop.lat is None:
        pop.lat = info.get("lat")
    if pop.lon is None:
        pop.lon = info.get("lon")
