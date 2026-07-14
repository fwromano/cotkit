"""GeoJSON ↔ CoT type bridge.

Most agency data lives in GeoJSON (exports from ArcGIS, QGIS, open-data
portals, drone mappers). These converters move single Features in both
directions with plain dicts — ``json.load`` the file yourself, no
dependencies.

Axis order: GeoJSON positions are ``[lon, lat, (ele)]``; CoT is
``lat``/``lon``. The swap happens here so callers never hand-flip
coordinates (historically the #1 conversion bug).

Supported geometries: Point, LineString, Polygon (exterior ring;
holes are not representable in basic CoT drawings and are dropped).
MultiPoint / MultiLineString / MultiPolygon: convert per-part with
``feature_to_events``.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .build import build_event
from .model import CotEvent

__all__ = ["feature_to_event", "feature_to_events", "event_to_feature"]

# GeoJSON geometry type → default CoT event type
_DEFAULT_TYPES = {
    "Point": "a-u-G",        # unknown ground point
    "LineString": "b-m-r",   # route
    "Polygon": "u-d-f",      # drawn shape
}

_MULTI_TO_SINGLE = {
    "MultiPoint": "Point",
    "MultiLineString": "LineString",
    "MultiPolygon": "Polygon",
}


def _positions_to_latlon(positions: List) -> List:
    """[[lon, lat, ele?], ...] → [(lat, lon, hae), ...]"""
    out = []
    for pos in positions:
        lon, lat = float(pos[0]), float(pos[1])
        hae = float(pos[2]) if len(pos) > 2 else 0.0
        out.append((lat, lon, hae))
    return out


def feature_to_event(
    feature: Dict[str, Any],
    *,
    uid: Optional[str] = None,
    event_type: Optional[str] = None,
    stale_seconds: float = 3600.0,
    **build_kwargs,
) -> str:
    """Convert one GeoJSON Feature (or bare geometry) to a CoT event XML.

    ``uid`` defaults to the feature's ``id`` or ``properties.uid``, else a
    random one. ``callsign`` defaults to ``properties.name`` /
    ``properties.callsign`` / ``properties.title``. ``event_type``
    defaults by geometry type (point → ``a-u-G``, line → route, polygon
    → drawn shape). Extra ``build_kwargs`` pass through to
    :func:`cotkit.build.build_event` (group, color, remarks, ...).
    """
    geometry = feature.get("geometry", feature)  # accept bare geometry too
    if not geometry or "type" not in geometry:
        raise ValueError("not a GeoJSON Feature or geometry")
    gtype = geometry["type"]
    if gtype in _MULTI_TO_SINGLE:
        raise ValueError(f"{gtype} has multiple parts — use feature_to_events()")
    if gtype not in _DEFAULT_TYPES:
        raise ValueError(f"unsupported GeoJSON geometry type: {gtype}")

    props = feature.get("properties") or {}
    uid = uid or str(feature.get("id") or props.get("uid") or f"geojson-{uuid.uuid4().hex[:12]}")
    callsign = build_kwargs.pop(
        "callsign",
        str(props.get("name") or props.get("callsign") or props.get("title") or ""),
    )
    # Round-trip fidelity: event_to_feature stores the original CoT type
    # in properties.cot_type — honor it so TAK→GeoJSON→TAK keeps types.
    cot_type = event_type or props.get("cot_type") or _DEFAULT_TYPES[gtype]
    coords = geometry.get("coordinates")
    if coords is None:
        raise ValueError("geometry has no coordinates")

    if gtype == "Point":
        (lat, lon, hae) = _positions_to_latlon([coords])[0]
        return build_event(uid, cot_type, lat, lon, hae=hae, callsign=callsign,
                           stale_seconds=stale_seconds, **build_kwargs)

    if gtype == "LineString":
        points = _positions_to_latlon(coords)
        closed = False
    else:  # Polygon: exterior ring only (holes dropped — CoT can't draw them)
        points = _positions_to_latlon(coords[0])
        closed = True

    if not points:
        raise ValueError("geometry has no coordinates")
    anchor = points[0]
    return build_event(
        uid, cot_type, anchor[0], anchor[1], callsign=callsign,
        stale_seconds=stale_seconds, geometry_points=points, closed=closed,
        **build_kwargs,
    )


def feature_to_events(feature: Dict[str, Any], **kwargs) -> List[str]:
    """Like :func:`feature_to_event`, but expands Multi* geometries into one
    event per part (uid gets a ``-1``, ``-2``... suffix)."""
    geometry = feature.get("geometry", feature)
    gtype = geometry.get("type")
    single = _MULTI_TO_SINGLE.get(gtype)
    if single is None:
        return [feature_to_event(feature, **kwargs)]

    props = feature.get("properties") or {}
    base_uid = str(
        kwargs.pop("uid", None) or feature.get("id") or props.get("uid")
        or f"geojson-{uuid.uuid4().hex[:12]}"
    )
    events = []
    for i, part in enumerate(geometry.get("coordinates") or [], start=1):
        part_feature = {"type": "Feature", "properties": props,
                        "geometry": {"type": single, "coordinates": part}}
        events.append(feature_to_event(part_feature, uid=f"{base_uid}-{i}", **kwargs))
    return events


def event_to_feature(event: CotEvent) -> Dict[str, Any]:
    """Convert a parsed :class:`CotEvent` to a GeoJSON Feature dict.

    Geometry selection: 3+ vertices with a closing repeat → Polygon;
    2+ vertices → LineString; else the point → Point. Core CoT fields
    land in ``properties`` (uid also as feature ``id``).
    """
    pts = event.geometry_points
    geometry: Optional[Dict[str, Any]]
    if len(pts) >= 3 and pts[0] == pts[-1]:
        geometry = {"type": "Polygon",
                    "coordinates": [[[lon, lat, hae] for lat, lon, hae in pts]]}
    elif len(pts) >= 2:
        geometry = {"type": "LineString",
                    "coordinates": [[lon, lat, hae] for lat, lon, hae in pts]}
    elif event.has_point:
        geometry = {"type": "Point",
                    "coordinates": [event.lon, event.lat, event.hae or 0.0]}
    else:
        geometry = None

    properties: Dict[str, Any] = {
        "uid": event.uid,
        "cot_type": event.event_type,
        "callsign": event.callsign,
        "how": event.how,
        "time": event.time,
        "stale": event.stale,
    }
    if event.course_deg is not None:
        properties["course_deg"] = event.course_deg
    if event.speed_mps is not None:
        properties["speed_mps"] = event.speed_mps

    return {"type": "Feature", "id": event.uid, "geometry": geometry,
            "properties": properties}
