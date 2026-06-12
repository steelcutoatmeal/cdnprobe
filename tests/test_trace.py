"""Tests for traceroute parsing and Cymru query-name construction."""

from cdnprobe.trace import _cymru_origin_qname, _parse_traceroute_output


def test_cymru_qname_ipv4():
    assert _cymru_origin_qname("8.8.8.8") == "8.8.8.8.origin.asn.cymru.com"
    assert _cymru_origin_qname("1.2.3.4") == "4.3.2.1.origin.asn.cymru.com"


def test_cymru_qname_ipv6():
    qname = _cymru_origin_qname("2001:db8::1")
    # Exploded: 2001:0db8:0000:...:0001 -> 32 reversed nibbles
    assert qname.endswith(".origin6.asn.cymru.com")
    nibbles = qname.removesuffix(".origin6.asn.cymru.com").split(".")
    assert len(nibbles) == 32
    assert nibbles[0] == "1"  # last nibble of the address comes first
    assert nibbles[-1] == "2"  # first nibble of "2001"


def test_parse_normal_hops():
    output = """traceroute to 1.1.1.1 (1.1.1.1), 30 hops max, 52 byte packets
 1  192.168.1.1  1.234 ms  1.111 ms  1.222 ms
 2  96.120.68.137  8.432 ms  7.891 ms  8.123 ms
 3  1.1.1.1  9.000 ms  9.100 ms  9.200 ms
"""
    hops = _parse_traceroute_output(output)
    assert len(hops) == 3
    assert hops[0].ip == "192.168.1.1"
    assert hops[0].rtt_ms == [1.234, 1.111, 1.222]
    assert hops[2].ip == "1.1.1.1"


def test_parse_timeout_hop():
    output = " 1  192.168.1.1  1.0 ms  1.0 ms  1.0 ms\n 2  * * *\n 3  9.9.9.9  5.0 ms  5.0 ms  5.0 ms\n"
    hops = _parse_traceroute_output(output)
    assert len(hops) == 3
    assert hops[1].ip is None
    assert hops[1].is_timeout


def test_parse_skips_garbage_lines():
    output = "traceroute: Warning: blah\nnot a hop line\n 1  10.0.0.1  2.0 ms  2.1 ms  2.2 ms\n"
    hops = _parse_traceroute_output(output)
    assert len(hops) == 1
    assert hops[0].hop_number == 1
