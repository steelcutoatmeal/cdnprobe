"""Tests for IATA PoP enrichment."""

from cdnprobe.iata import enrich_pop, load_iata_db
from cdnprobe.models import PoPIdentity


def test_db_loads():
    db = load_iata_db()
    assert len(db) > 0
    assert "DFW" in db


def test_enrich_fills_missing_fields():
    pop = PoPIdentity(code="DFW")
    enrich_pop(pop)
    assert pop.city == "Dallas"
    assert pop.country == "US"
    assert pop.lat is not None
    assert pop.lon is not None


def test_enrich_preserves_provider_values():
    pop = PoPIdentity(code="DFW", city="Dallas-Fort Worth", lat=1.0)
    enrich_pop(pop)
    assert pop.city == "Dallas-Fort Worth"  # not overwritten
    assert pop.lat == 1.0
    assert pop.country == "US"  # filled in


def test_enrich_unknown_code_is_noop():
    pop = PoPIdentity(code="ZZZ" * 2)
    enrich_pop(pop)
    assert pop.city is None


def test_enrich_no_code_is_noop():
    pop = PoPIdentity()
    enrich_pop(pop)
    assert pop.city is None
