"""Framing: the parser must survive every ugly thing TCP can do."""

from cotkit.framing import CotStreamParser

EV = '<event version="2.0" uid="x" type="a-f-G"><point lat="1" lon="2"/></event>'


def test_single_event_one_chunk():
    p = CotStreamParser()
    assert p.feed(EV) == [EV]
    assert p.buffer == ""


def test_multiple_events_one_chunk():
    p = CotStreamParser()
    assert p.feed(EV + "\n" + EV + EV) == [EV, EV, EV]


def test_event_split_across_chunks_mid_document():
    p = CotStreamParser()
    assert p.feed(EV[:40]) == []
    assert p.feed(EV[40:]) == [EV]


def test_open_tag_split_across_chunks():
    # chunk1 ends inside the literal "<event" tag itself
    p = CotStreamParser()
    assert p.feed("garbage<ev") == []
    assert p.feed("ent uid='a'></event>") == ["<event uid='a'></event>"]


def test_garbage_between_events_discarded():
    p = CotStreamParser()
    out = p.feed('<?xml version="1.0"?>\nnoise' + EV + "trailing junk")
    assert out == [EV]
    # trailing junk that can't start an event is dropped
    assert "junk" not in p.buffer


def test_bytes_input_decoded():
    p = CotStreamParser()
    assert p.feed(EV.encode("utf-8")) == [EV]


def test_invalid_utf8_replaced_not_fatal():
    p = CotStreamParser()
    data = EV.encode("utf-8") + b"\xff\xfe"
    assert p.feed(data) == [EV]


def test_oversize_unterminated_event_dropped():
    p = CotStreamParser(max_buffer=1024)
    p.feed("<event " + "a" * 5000)  # never terminates
    assert p.dropped_oversize == 1
    assert len(p.buffer) <= 1024 + 10
    # parser still works afterwards
    assert p.feed(EV) == [EV]


def test_unterminated_fragment_does_not_swallow_next_event():
    """A garbage '<event' with no end tag must not consume the next valid
    event's end tag and destroy it."""
    p = CotStreamParser()
    p.feed("<event uid='broken' xxx no end tag here ")
    out = p.feed(EV)
    assert out == [EV]
    assert p.dropped_fragments == 1


def test_multibyte_utf8_split_across_chunks():
    """A UTF-8 char split at the recv boundary must decode, not become
    two replacement characters."""
    ev = EV.replace('uid="x"', 'uid="café"')
    raw = ev.encode("utf-8")
    split = raw.index(b"\xc3") + 1  # cut inside the é sequence
    p = CotStreamParser()
    out = p.feed(raw[:split])
    out += p.feed(raw[split:])
    assert out == [ev]
    assert "�" not in out[0]


def test_interleaved_feed_returns_in_order():
    p = CotStreamParser()
    e1 = EV.replace('uid="x"', 'uid="1"')
    e2 = EV.replace('uid="x"', 'uid="2"')
    got = []
    for chunk in (e1[:10], e1[10:] + e2[:25], e2[25:]):
        got.extend(p.feed(chunk))
    assert [g.split('uid="')[1][0] for g in got] == ["1", "2"]
