"""CoT event builders.

All XML is constructed with ElementTree — never string interpolation —
so attribute escaping is structural. (Two prior implementations built SA
events with f-strings; a callsign containing ``"`` or ``<`` produced
malformed XML on the wire. That defect class is impossible here.)

Lineage: core shape from SWM ``MOTE/TAK.build_cot_event``; SA/keepalive
identity fields from heartbeat's recorder; route/polygon detail encoding
from SWM bifrost's renderer, with SWM-specific elements removed.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Sequence, Tuple

from .timestamps import cot_time, cot_time_offset

__all__ = [
    "ICON_SHORTCUTS",
    "resolve_event_type",
    "build_event",
    "build_sa_event",
    "build_delete_event",
    "build_ping",
    "argb_to_signed_int_string",
]

# Friendly aliases → 2525 atom types, for callers that don't speak CoT taxonomy.
ICON_SHORTCUTS = {
    "friendly": "a-f-G-U-C",
    "blue_force": "a-f-G-U-C",
    "hostile": "a-h-G-U-C",
    "red_force": "a-h-G-U-C",
    "neutral": "a-n-G-U-C",
    "green_force": "a-n-G-U-C",
    "unknown": "a-u-G-U-C",
    "yellow_force": "a-u-G-U-C",
}

_DEFAULT_TYPE = "a-f-G-U-C"
_UNKNOWN_ERROR = "9999999.0"  # CoT convention for "no error estimate"


def resolve_event_type(icon_or_type: Optional[str]) -> str:
    """Map a friendly alias or pass through an explicit CoT type string.

    Unrecognized bare words raise ``ValueError`` rather than silently
    becoming a friendly blue-force marker — a typo must be loud, not a
    misclassified track on someone's map.
    """
    if not icon_or_type or not icon_or_type.strip():
        return _DEFAULT_TYPE
    value = icon_or_type.strip()
    shortcut = ICON_SHORTCUTS.get(value.lower())
    if shortcut:
        return shortcut
    if "-" in value or "/" in value:
        return value
    raise ValueError(
        f"unknown event type/alias {value!r} — use a CoT type (e.g. 'a-f-G-U-C') "
        f"or one of: {', '.join(sorted(ICON_SHORTCUTS))}"
    )


def _validate_latlon(lat: float, lon: float, hae: float) -> None:
    """Refuse to put non-finite or out-of-range coordinates on the wire.

    Java TAK clients reject events whose numeric fields fail
    ``Double.parseDouble`` (lowercase ``nan``/``inf`` do), and a GPS
    dropout upstream shouldn't become poison CoT downstream — fail loud
    at build time instead.
    """
    if not (math.isfinite(lat) and math.isfinite(lon) and math.isfinite(hae)):
        raise ValueError(f"non-finite coordinates: lat={lat} lon={lon} hae={hae}")
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"latitude out of range: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"longitude out of range: {lon}")


def argb_to_signed_int_string(argb_hex: Optional[str]) -> Optional[str]:
    """Convert ``AARRGGBB`` hex to the signed 32-bit int string ATAK's
    stroke/fill color elements expect. Returns None for empty/invalid input."""
    if not argb_hex:
        return None
    value = argb_hex.strip().lstrip("#")
    if len(value) == 6:  # RRGGBB → opaque
        value = "FF" + value
    if len(value) != 8:
        return None
    try:
        unsigned = int(value, 16)
    except ValueError:
        return None
    return str(unsigned - 0x100000000 if unsigned >= 0x80000000 else unsigned)


def build_event(
    uid: str,
    event_type: str,
    lat: float,
    lon: float,
    *,
    hae: float = 0.0,
    ce: Optional[float] = None,
    le: Optional[float] = None,
    how: str = "m-g",
    stale_seconds: float = 75.0,
    callsign: str = "",
    group_name: Optional[str] = None,
    group_role: str = "Team Member",
    remarks: Optional[str] = None,
    color_argb: Optional[str] = None,
    course_deg: Optional[float] = None,
    speed_mps: Optional[float] = None,
    geometry_points: Optional[Sequence[Tuple[float, float, float]]] = None,
    closed: bool = False,
    detail_elements: Optional[List[Tuple[str, Dict[str, str]]]] = None,
) -> str:
    """Build a complete CoT ``<event>`` document, returned as an XML string.

    ``event_type`` accepts a raw CoT type ("a-f-G-U-C", "u-d-f", ...) or a
    friendly alias from ``ICON_SHORTCUTS``.

    ``course_deg`` / ``speed_mps`` — emitted as a ``<track>`` detail so TAK
    clients render the object as a moving track (heading arrow + speed)
    instead of a static dot. Course is degrees true; speed meters/second.

    ``geometry_points`` — (lat, lon, hae) vertices for routes/polygons,
    encoded as ATAK ``<link point="...">`` elements plus a ``<polyline>``
    whose ``closed`` flag distinguishes route from area. Stroke/fill
    colors are emitted when ``color_argb`` is set.

    ``detail_elements`` — extra ``(tag, attributes)`` pairs appended to
    ``<detail>`` verbatim, for consumer-specific extensions.
    """
    _validate_latlon(lat, lon, hae)
    for glat, glon, ghae in geometry_points or []:
        _validate_latlon(glat, glon, ghae)

    now = cot_time()
    event = ET.Element(
        "event",
        {
            "version": "2.0",
            "uid": uid,
            "type": resolve_event_type(event_type),
            "time": now,
            "start": now,
            "stale": cot_time_offset(max(1.0, stale_seconds)),
            "how": how or "m-g",
        },
    )
    ET.SubElement(
        event,
        "point",
        {
            "lat": f"{lat:.7f}",
            "lon": f"{lon:.7f}",
            "hae": f"{hae:.2f}",
            "ce": _UNKNOWN_ERROR if ce is None else f"{ce:.1f}",
            "le": _UNKNOWN_ERROR if le is None else f"{le:.1f}",
        },
    )

    detail = ET.SubElement(event, "detail")
    if callsign:
        ET.SubElement(detail, "contact", {"callsign": callsign})
    if group_name:
        attrs = {"name": group_name}
        if group_role:
            attrs["role"] = group_role
        ET.SubElement(detail, "__group", attrs)

    if course_deg is not None or speed_mps is not None:
        track_attrs = {}
        if course_deg is not None:
            track_attrs["course"] = f"{course_deg % 360.0:.1f}"
        if speed_mps is not None:
            track_attrs["speed"] = f"{max(0.0, speed_mps):.2f}"
        ET.SubElement(detail, "track", track_attrs)

    if geometry_points:
        vertices = list(geometry_points)
        # ATAK determines shape closure from a repeated first/last vertex,
        # not from the polyline element alone — close the ring explicitly.
        if closed and vertices[0] != vertices[-1]:
            vertices.append(vertices[0])
        for seq, (glat, glon, ghae) in enumerate(vertices, start=1):
            ET.SubElement(
                detail,
                "link",
                {
                    "point": f"{float(glat):.7f},{float(glon):.7f},{float(ghae):.2f}",
                    "type": "b-m-p-w",
                    "relation": "c",
                    "seq": str(seq),
                },
            )
        ET.SubElement(detail, "polyline", {"closed": "true" if closed else "false"})
        stroke = argb_to_signed_int_string(color_argb)
        if stroke:
            ET.SubElement(detail, "strokeColor", {"value": stroke})
            if closed:
                ET.SubElement(detail, "fillColor", {"value": stroke})
    elif color_argb:
        # ATAK parses <color>@argb with Integer.parseInt — signed int, not hex.
        signed = argb_to_signed_int_string(color_argb)
        if signed:
            ET.SubElement(detail, "color", {"argb": signed})

    if remarks:
        remarks_el = ET.SubElement(detail, "remarks")
        remarks_el.text = remarks

    for tag, attrs in detail_elements or []:
        ET.SubElement(detail, tag, dict(attrs))

    return ET.tostring(event, encoding="unicode")


def build_sa_event(
    uid: str,
    callsign: str,
    *,
    lat: float = 0.0,
    lon: float = 0.0,
    hae: float = 0.0,
    group_name: str = "Cyan",
    group_role: str = "Team Member",
    stale_seconds: float = 300.0,
    platform: str = "cotkit",
    device: str = "service",
    os_name: str = "linux",
    version: str = "1.0",
) -> str:
    """Situational-awareness self-identification event.

    Sending one of these on connect (and periodically as keepalive) is
    how a headless client registers with a TAK server as a peer instead
    of an anonymous socket. The ``<takv>`` element is what server UIs
    display as the client's platform.
    """
    return build_event(
        uid,
        "a-f-G-U-C",
        lat,
        lon,
        hae=hae,
        how="m-g",
        stale_seconds=stale_seconds,
        callsign=callsign,
        group_name=group_name,
        group_role=group_role,
        detail_elements=[
            ("takv", {"version": version, "platform": platform, "device": device, "os": os_name}),
        ],
    )


def build_delete_event(
    uid: str,
    event_type: str,
    *,
    lat: float = 0.0,
    lon: float = 0.0,
    stale_seconds: float = 30.0,
    group_name: Optional[str] = None,
) -> str:
    """Remove an object from TAK maps: a ``t-x-d-d`` delete tombstone.

    ``uid``/``event_type`` are those of the object being removed. This is
    the reliable removal idiom — simply re-sending the object with a
    near-past ``stale`` is NOT enough: ATAK auto-purges stale *point
    markers* but leaves drawings (routes, polygons) on the map. The
    tombstone (target-linked ``t-x-d-d`` plus ``<__forcedelete/>``)
    removes markers and shapes alike.

    Deletes are only meaningful for objects your sender owns; pair with
    the upsert flow (same-uid re-send updates in place).
    """
    now = cot_time()
    event = ET.Element(
        "event",
        {
            "version": "2.0",
            "uid": uid,
            "type": "t-x-d-d",
            "time": now,
            "start": now,
            "stale": cot_time_offset(max(1.0, stale_seconds)),
            "how": "m-g",
        },
    )
    ET.SubElement(
        event,
        "point",
        {
            "lat": f"{lat:.7f}",
            "lon": f"{lon:.7f}",
            "hae": "0.00",
            "ce": _UNKNOWN_ERROR,
            "le": _UNKNOWN_ERROR,
        },
    )
    detail = ET.SubElement(event, "detail")
    if group_name:
        ET.SubElement(detail, "__group", {"name": group_name})
    ET.SubElement(detail, "link", {"uid": uid, "type": event_type, "relation": "none"})
    ET.SubElement(detail, "__forcedelete")
    return ET.tostring(event, encoding="unicode")


def build_ping(uid: str) -> str:
    """TAK protocol ping (``t-x-c-t``); servers answer with ``t-x-c-t-r``."""
    now = cot_time()
    event = ET.Element(
        "event",
        {
            "version": "2.0",
            "uid": f"{uid}-ping",
            "type": "t-x-c-t",
            "time": now,
            "start": now,
            "stale": cot_time_offset(10.0),
            "how": "m-g",
        },
    )
    ET.SubElement(
        event,
        "point",
        {
            "lat": f"{0.0:.7f}",
            "lon": f"{0.0:.7f}",
            "hae": f"{0.0:.2f}",
            "ce": _UNKNOWN_ERROR,
            "le": _UNKNOWN_ERROR,
        },
    )
    return ET.tostring(event, encoding="unicode")
