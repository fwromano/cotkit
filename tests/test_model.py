"""Model: parsing, geometry extraction, classification, dedup signatures."""

from cotkit.model import CotEvent, classify_event, parse_event

FULL = (
    '<event version="2.0" uid="unit-1" type="a-f-G-U-C" how="m-g" '
    'time="2026-07-14T12:00:00.000Z" start="2026-07-14T12:00:00.000Z" '
    'stale="2026-07-14T12:05:00.000Z">'
    '<point lat="30.6187" lon="-96.3365" hae="96.5" ce="12.0" le="9999999.0"/>'
    '<detail><contact callsign="ALPHA 1"/><__group name="Cyan" role="HQ"/></detail>'
    "</event>"
)

ROUTE = (
    '<event version="2.0" uid="route-1" type="b-m-r" how="h-e" '
    'time="t" start="t" stale="t">'
    "<detail>"
    '<link point="30.1,-96.1" type="b-m-p-w" relation="c" seq="1"/>'
    '<link point="30.2,-96.2,15" type="b-m-p-w" relation="c" seq="2"/>'
    '<polyline closed="false"/>'
    "</detail></event>"
)


def test_parse_full_event():
    ev = parse_event(FULL)
    assert ev is not None
    assert ev.uid == "unit-1"
    assert ev.event_type == "a-f-G-U-C"
    assert ev.how == "m-g"
    assert ev.callsign == "ALPHA 1"
    assert abs(ev.lat - 30.6187) < 1e-9
    assert abs(ev.lon - -96.3365) < 1e-9
    assert ev.ce == 12.0
    assert ev.has_point
    assert ev.raw_xml == FULL
    assert "__group" in ev.detail_xml


def test_parse_rejects_non_event_and_garbage():
    assert parse_event("<notanevent/>") is None
    assert parse_event("complete garbage <<<") is None


def test_route_geometry_and_anchor():
    ev = parse_event(ROUTE)
    assert ev is not None
    assert not ev.has_point  # no top-level <point>
    assert ev.geometry_points == [(30.1, -96.1, 0.0), (30.2, -96.2, 15.0)]
    # anchor falls back to first vertex
    assert ev.anchor == (30.1, -96.1, 0.0)


def test_geometry_keeps_ring_closure_collapses_immediate_dupes():
    xml = (
        '<event uid="a" type="u-d-f" time="t" start="t" stale="t"><detail>'
        '<link point="1,1"/><link point="1,1"/>'  # immediate dupe → collapsed
        '<link point="2,2"/><link point="1,1"/>'  # ring closure → kept
        "</detail></event>"
    )
    ev = parse_event(xml)
    assert ev.geometry_points == [(1.0, 1.0, 0.0), (2.0, 2.0, 0.0), (1.0, 1.0, 0.0)]


def test_signature_ignores_wall_clock_fields():
    a = parse_event(FULL)
    moved_in_time = parse_event(
        FULL.replace("12:00:00", "13:30:30").replace("12:05:00", "13:35:30")
    )
    assert a.signature() == moved_in_time.signature()


def test_signature_changes_on_content():
    a = parse_event(FULL)
    b = parse_event(FULL.replace('lat="30.6187"', 'lat="30.7000"'))
    c = parse_event(FULL.replace("ALPHA 1", "BRAVO 2"))
    assert a.signature() != b.signature()
    assert a.signature() != c.signature()


def test_classify_event_taxonomy():
    assert classify_event("a-f-G-U-C", "h-g-i-g-o") == "positions"  # ATAK auto-SA
    assert classify_event("a-h-G", "m-g") == "positions"
    assert classify_event("a-h-G", "h-e") == "markers"
    assert classify_event("b-m-p-s-m", "h-e") == "markers"
    assert classify_event("b-m-r") == "routes"
    assert classify_event("u-d-f") == "routes"          # ambiguous, open by default
    assert classify_event("u-d-f", closed=True) == "areas"  # closure hint resolves it
    assert classify_event("u-d-c-c") == "areas"
    assert classify_event("t-x-c-t") == "other"


def test_doctype_rejected_entity_expansion_defense():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        '<event uid="x" type="a-f-G" time="t" start="t" stale="t">'
        "<detail><remarks>&lol2;</remarks></detail></event>"
    )
    assert parse_event(bomb) is None


def test_association_links_are_not_geometry():
    """relation="p-p" (parent) and relation="none" (delete tombstones)
    point AT other objects — they must not become this event's geometry."""
    xml = (
        '<event uid="m" type="a-f-G" time="t" start="t" stale="t">'
        '<point lat="30.0" lon="-96.0" hae="0" ce="10" le="10"/>'
        "<detail>"
        '<link relation="p-p" uid="parent-1" point="10.0,20.0,0.0"/>'
        '<link relation="none" uid="dead-1" point="11.0,21.0,0.0"/>'
        "</detail></event>"
    )
    ev = parse_event(xml)
    assert ev.geometry_points == []
    assert ev.has_point


def test_default_construction_is_cheap():
    ev = CotEvent(uid="x", event_type="a-f-G")
    assert ev.geometry_points == []
    assert ev.anchor is None
