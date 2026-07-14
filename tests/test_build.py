"""Builders: valid XML, correct structure, and — critically — escaping."""

import pytest
import xml.etree.ElementTree as ET

from cotkit.build import (
    argb_to_signed_int_string,
    build_delete_event,
    build_event,
    build_ping,
    build_sa_event,
    resolve_event_type,
)
from cotkit.model import parse_event


def test_build_parse_roundtrip():
    xml = build_event(
        "unit-9", "a-f-G-U-C", 30.6187, -96.3365,
        hae=96.5, callsign="ALPHA 9", group_name="Cyan", group_role="HQ",
        remarks="on station",
    )
    ev = parse_event(xml)
    assert ev.uid == "unit-9"
    assert ev.callsign == "ALPHA 9"
    assert abs(ev.lat - 30.6187) < 1e-6
    assert "remarks" in ev.detail_xml


def test_hostile_callsign_is_escaped_not_injected():
    """The defect class that motivated ET-only building: quotes and angle
    brackets in operator-supplied strings must never break the document."""
    evil = 'B"AD <guy> & "friends"'
    xml = build_event("u", "friendly", 0.0, 0.0, callsign=evil, remarks=evil)
    ev = parse_event(xml)  # would be None if the XML were malformed
    assert ev is not None
    assert ev.callsign == evil


def test_stale_is_after_time():
    ev = parse_event(build_event("u", "a-f-G", 1.0, 2.0, stale_seconds=75))
    assert ev.stale > ev.time  # ISO-8601 Z strings compare chronologically


def test_icon_shortcuts_and_passthrough():
    assert resolve_event_type("hostile") == "a-h-G-U-C"
    assert resolve_event_type("a-u-A") == "a-u-A"  # explicit types pass through
    assert resolve_event_type(None) == "a-f-G-U-C"
    with pytest.raises(ValueError):
        resolve_event_type("nonsense")  # typos must be loud, not blue-force


def test_coordinates_validated_at_build_time():
    for lat, lon in [(float("nan"), 0.0), (0.0, float("inf")), (91.0, 0.0), (0.0, -181.0)]:
        with pytest.raises(ValueError):
            build_event("u", "a-f-G", lat, lon)
    with pytest.raises(ValueError):  # geometry vertices validated too
        build_event("u", "b-m-r", 0.0, 0.0,
                    geometry_points=[(0.0, 0.0, 0.0), (float("nan"), 1.0, 0.0)])


def test_marker_color_is_signed_int_for_atak():
    root = ET.fromstring(build_event("u", "a-f-G", 0.0, 0.0, color_argb="FF0000FF"))
    argb = root.find("detail").find("color").get("argb")
    int(argb)  # ATAK does Integer.parseInt — must not be raw hex
    assert argb == str(0xFF0000FF - 0x100000000)


def test_polygon_ring_closed_on_wire():
    """ATAK infers closure from a repeated first/last vertex — the builder
    must close an unclosed ring itself."""
    pts = [(30.1, -96.1, 0.0), (30.2, -96.2, 0.0), (30.3, -96.1, 0.0)]  # open
    root = ET.fromstring(build_event("p", "u-d-f", 30.1, -96.1,
                                     geometry_points=pts, closed=True))
    links = root.find("detail").findall("link")
    assert len(links) == 4
    assert links[0].get("point") == links[-1].get("point")


def test_geometry_vertex_hae_preserved():
    pts = [(30.1, -96.1, 150.0), (30.2, -96.2, 160.0)]
    ev = parse_event(build_event("r", "b-m-r", 30.1, -96.1, geometry_points=pts))
    assert [h for _, _, h in ev.geometry_points] == [150.0, 160.0]


def test_route_and_polygon_geometry():
    pts = [(30.1, -96.1, 0.0), (30.2, -96.2, 0.0), (30.3, -96.1, 0.0)]
    route = ET.fromstring(build_event("r", "b-m-r", 30.1, -96.1, geometry_points=pts))
    polygon = ET.fromstring(
        build_event("p", "u-d-f", 30.1, -96.1, geometry_points=pts, closed=True,
                    color_argb="FF00FF00")
    )
    route_detail = route.find("detail")
    assert len(route_detail.findall("link")) == 3
    assert route_detail.find("polyline").get("closed") == "false"
    poly_detail = polygon.find("detail")
    assert poly_detail.find("polyline").get("closed") == "true"
    assert poly_detail.find("strokeColor") is not None
    assert poly_detail.find("fillColor") is not None


def test_parse_geometry_from_our_own_builder():
    pts = [(30.1, -96.1, 0.0), (30.2, -96.2, 0.0)]
    ev = parse_event(build_event("r", "b-m-r", 30.1, -96.1, geometry_points=pts))
    assert [(round(a, 4), round(b, 4)) for a, b, _ in ev.geometry_points] == [
        (30.1, -96.1), (30.2, -96.2)]


def test_sa_event_registers_identity():
    ev = parse_event(build_sa_event("uid-1", "HB-REC", group_name="Blue", group_role="HQ"))
    assert ev.event_type == "a-f-G-U-C"
    assert ev.callsign == "HB-REC"
    assert "takv" in ev.detail_xml
    assert 'name="Blue"' in ev.detail_xml


def test_track_course_speed_roundtrip():
    ev = parse_event(build_event("v", "a-f-G-E-V-C", 30.0, -96.0,
                                 course_deg=372.5, speed_mps=13.4))
    assert ev.course_deg == 12.5  # normalized into [0, 360)
    assert ev.speed_mps == 13.4
    no_track = parse_event(build_event("v", "a-f-G", 0.0, 0.0))
    assert no_track.course_deg is None and no_track.speed_mps is None


def test_ping_shape():
    ev = parse_event(build_ping("me"))
    assert ev.event_type == "t-x-c-t"
    assert ev.uid == "me-ping"


def test_delete_event_is_forcedelete_tombstone():
    xml = build_delete_event("engine-41", "a-f-G-E-V-C")
    root = ET.fromstring(xml)
    assert root.get("type") == "t-x-d-d"
    assert root.get("uid") == "engine-41"
    detail = root.find("detail")
    link = detail.find("link")
    assert link.get("uid") == "engine-41"
    assert link.get("type") == "a-f-G-E-V-C"
    assert link.get("relation") == "none"
    assert detail.find("__forcedelete") is not None
    ev = parse_event(xml)  # well-formed and parseable
    assert ev.stale > ev.time


def test_argb_conversion():
    assert argb_to_signed_int_string("FFFFFFFF") == "-1"
    assert argb_to_signed_int_string("00000000") == "0"
    assert argb_to_signed_int_string("FF0000FF") == str(0xFF0000FF - 0x100000000)
    assert argb_to_signed_int_string("00FF00") == str(0xFF00FF00 - 0x100000000)  # RGB → opaque
    assert argb_to_signed_int_string("zzz") is None
    assert argb_to_signed_int_string(None) is None
