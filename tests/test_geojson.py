"""GeoJSON bridge: both directions, with the lon/lat axis swap under test."""

import pytest

from cotkit.geojson import event_to_feature, feature_to_event, feature_to_events
from cotkit.model import parse_event

POINT_FEATURE = {
    "type": "Feature",
    "id": "hydrant-12",
    "properties": {"name": "Hydrant 12"},
    "geometry": {"type": "Point", "coordinates": [-96.3365, 30.6187, 96.5]},
}

POLYGON_FEATURE = {
    "type": "Feature",
    "properties": {"name": "Burn Area"},
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[-96.1, 30.1], [-96.2, 30.2], [-96.1, 30.3], [-96.1, 30.1]]],
    },
}


def test_point_feature_axis_order_swapped():
    ev = parse_event(feature_to_event(POINT_FEATURE))
    # GeoJSON was [lon, lat] — CoT must come out lat/lon
    assert abs(ev.lat - 30.6187) < 1e-9
    assert abs(ev.lon - -96.3365) < 1e-9
    assert abs(ev.hae - 96.5) < 1e-9
    assert ev.uid == "hydrant-12"
    assert ev.callsign == "Hydrant 12"


def test_polygon_feature_becomes_closed_shape():
    ev = parse_event(feature_to_event(POLYGON_FEATURE))
    assert ev.event_type == "u-d-f"
    assert len(ev.geometry_points) == 4
    assert ev.geometry_points[0] == ev.geometry_points[-1]  # ring kept closed
    assert ev.geometry_points[0] == (30.1, -96.1, 0.0)
    assert 'closed="true"' in ev.raw_xml


def test_linestring_feature_becomes_route():
    ev = parse_event(feature_to_event({
        "type": "Feature", "properties": {},
        "geometry": {"type": "LineString", "coordinates": [[-96.1, 30.1], [-96.2, 30.2]]},
    }))
    assert ev.event_type == "b-m-r"
    assert ev.geometry_points == [(30.1, -96.1, 0.0), (30.2, -96.2, 0.0)]


def test_bare_geometry_and_overrides():
    xml = feature_to_event({"type": "Point", "coordinates": [-96.0, 30.0]},
                           uid="x", event_type="a-f-G-E-S", callsign="Pump 3")
    ev = parse_event(xml)
    assert (ev.uid, ev.event_type, ev.callsign) == ("x", "a-f-G-E-S", "Pump 3")


def test_multi_geometry_requires_plural_and_expands():
    multi = {"type": "Feature", "properties": {"uid": "zones"},
             "geometry": {"type": "MultiPoint",
                          "coordinates": [[-96.0, 30.0], [-96.5, 30.5]]}}
    with pytest.raises(ValueError):
        feature_to_event(multi)
    events = [parse_event(x) for x in feature_to_events(multi)]
    assert [e.uid for e in events] == ["zones-1", "zones-2"]
    assert abs(events[1].lat - 30.5) < 1e-9


def test_unsupported_geometry_rejected():
    with pytest.raises(ValueError):
        feature_to_event({"type": "GeometryCollection", "geometries": []})


def test_event_to_feature_point_roundtrip():
    ev = parse_event(feature_to_event(POINT_FEATURE))
    feature = event_to_feature(ev)
    assert feature["geometry"]["type"] == "Point"
    lon, lat = feature["geometry"]["coordinates"][:2]
    assert abs(lon - -96.3365) < 1e-6 and abs(lat - 30.6187) < 1e-6
    assert feature["properties"]["callsign"] == "Hydrant 12"
    assert feature["id"] == "hydrant-12"


def test_roundtrip_preserves_cot_type():
    """TAK → GeoJSON → TAK must not downgrade types: event_to_feature
    stores cot_type and feature_to_event reads it back."""
    from cotkit.build import build_event
    original = parse_event(build_event("veh-1", "a-f-G-E-V-C", 30.0, -96.0))
    feature = event_to_feature(original)
    back = parse_event(feature_to_event(feature))
    assert back.event_type == "a-f-G-E-V-C"
    assert back.uid == "veh-1"


def test_event_to_feature_polygon_roundtrip():
    ev = parse_event(feature_to_event(POLYGON_FEATURE))
    feature = event_to_feature(ev)
    assert feature["geometry"]["type"] == "Polygon"
    ring = feature["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    assert ring[0][:2] == [-96.1, 30.1]  # back in lon/lat order
