"""Tests for provider PoP detection (header parsing only, no network)."""

import httpx

from cdnprobe.providers.azure import AzureProvider
from cdnprobe.providers.base import find_iata_token
from cdnprobe.providers.bunny import BunnyProvider
from cdnprobe.providers.cachefly import CacheFlyProvider
from cdnprobe.providers.cdn77 import CDN77Provider
from cdnprobe.providers.cloudflare import CloudflareProvider
from cdnprobe.providers.cloudfront import CloudFrontProvider
from cdnprobe.providers.fastly import FastlyProvider
from cdnprobe.providers.kingsoft import KingsoftProvider


def _response(headers=None, text=""):
    return httpx.Response(200, headers=headers or {}, text=text)


# ── find_iata_token ───────────────────────────────────────────────────


def test_find_iata_token_skips_cache_words():
    assert find_iata_token("HIT") is None
    assert find_iata_token("TCP MEM HIT") is None
    assert find_iata_token("HIT from DFW") == "DFW"


def test_find_iata_token_no_match():
    assert find_iata_token("MISS") is None  # 4 letters
    assert find_iata_token("") is None
    assert find_iata_token("lowercase dfw") is None


# ── Regression: x-cache "HIT" must not become a PoP code ─────────────


def test_cdn77_x_cache_hit_is_not_a_pop():
    pop = CDN77Provider().detect_pop(_response({"x-cache": "HIT"}))
    assert pop.code is None


def test_cdn77_x_77_pop_header():
    pop = CDN77Provider().detect_pop(_response({"x-77-pop": "pragueCZ"}))
    assert pop.code == "PRA"
    assert pop.confidence == "confirmed"


def test_cachefly_x_served_by_hit_is_not_a_pop():
    pop = CacheFlyProvider().detect_pop(_response({"x-served-by": "HIT"}))
    assert pop.code is None


# ── Header-format parsing per provider ────────────────────────────────


def test_cloudflare_colo_from_trace_body():
    body = "fl=123\nip=1.2.3.4\ncolo=SLC\nhttp=h2\n"
    pop = CloudflareProvider().detect_pop(_response(text=body))
    assert pop.code == "SLC"
    assert pop.confidence == "confirmed"


def test_cloudflare_no_colo():
    pop = CloudflareProvider().detect_pop(_response(text="fl=123\n"))
    assert pop.code is None
    assert pop.confidence == "unknown"


def test_cloudfront_pop_header():
    pop = CloudFrontProvider().detect_pop(_response({"x-amz-cf-pop": "DFW55-C1"}))
    assert pop.code == "DFW"
    assert pop.confidence == "confirmed"


def test_fastly_takes_last_shield_entry():
    raw = "cache-iad-kiad7000021-IAD, cache-dfw-kdfw8210000-DFW"
    pop = FastlyProvider().detect_pop(_response({"x-served-by": raw}))
    assert pop.code == "DFW"


def test_azure_msedge_ref():
    raw = "Ref A: ABC Ref B: CO1EDGE2922 Ref C: 2026"
    pop = AzureProvider().detect_pop(_response({"x-msedge-ref": raw}))
    assert pop.code == "CO"


def test_bunny_requestid_prefix():
    pop = BunnyProvider().detect_pop(_response({"cdn-requestid": "DE-FRA-12345"}))
    assert pop.code == "FRA"


def test_kingsoft_node_name_is_not_a_code():
    pop = KingsoftProvider().detect_pop(
        _response({"x-cache-status": "MISS from KS-CLOUD-XG-FOREIGN-12-01"})
    )
    assert pop.code is None
    assert pop.confidence == "inferred"
    assert "KS-CLOUD" in pop.raw_header
