"""Typed CoT event model: parse, classify, deduplicate.

``CotEvent`` is the one structured form for a received event. It carries
the universal CoT fields; anything domain-specific stays in the raw
``detail_xml`` for the consumer to interpret.

Lineage: field set from heartbeat's ``parse_cot_event`` dict; the typed
dataclass shape and the wall-clock-free ``signature()`` dedup hash from
SWM bifrost's ``records.CotSpec``.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

__all__ = ["CotEvent", "parse_event", "extract_geometry_points", "classify_event"]

GeometryPoint = Tuple[float, float, float]  # (lat, lon, hae)


@dataclass
class CotEvent:
    """A parsed Cursor-on-Target event."""

    uid: str
    event_type: str
    how: str = ""
    time: str = ""
    start: str = ""
    stale: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    hae: Optional[float] = None
    ce: Optional[float] = None
    le: Optional[float] = None
    callsign: str = ""
    group_name: str = ""
    group_role: str = ""
    remarks: str = ""
    course_deg: Optional[float] = None
    speed_mps: Optional[float] = None
    detail_xml: str = ""
    geometry_points: List[GeometryPoint] = field(default_factory=list)
    raw_xml: str = ""

    @property
    def has_point(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def anchor(self) -> Optional[GeometryPoint]:
        """Best single position: the point, else the first geometry vertex.

        Some drawing CoTs (routes, polygons) omit the top-level ``<point>``;
        the first vertex is the conventional anchor (heartbeat recorder
        behavior).
        """
        if self.has_point:
            return (self.lat, self.lon, self.hae if self.hae is not None else 0.0)
        if self.geometry_points:
            return self.geometry_points[0]
        return None

    def signature(self) -> str:
        """Stable content hash for dedup / change detection.

        Excludes wall-clock fields (``time``/``start``/``stale``) and
        ``raw_xml`` so a re-sent-but-unchanged object hashes identically —
        senders can skip retransmits on constrained links.
        """
        parts = (
            self.uid,
            self.event_type,
            self.how,
            "" if self.lat is None else f"{self.lat:.7f}",
            "" if self.lon is None else f"{self.lon:.7f}",
            "" if self.hae is None else f"{self.hae:.2f}",
            self.callsign,
            self.detail_xml,
            ";".join(f"{a:.7f},{b:.7f},{c:.2f}" for a, b, c in self.geometry_points),
        )
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def parse_event(xml_str: str) -> Optional[CotEvent]:
    """Parse one complete ``<event>`` XML document. Returns None if invalid.

    Documents containing a DOCTYPE are rejected outright: CoT never uses
    one, and stdlib expat does not cap internal-entity amplification, so
    a hostile DOCTYPE is a memory-exhaustion vector ("billion laughs").
    """
    if "<!doctype" in xml_str[:4096].lower():
        return None
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return None
    if root.tag != "event":
        return None

    ev = CotEvent(
        uid=root.get("uid", ""),
        event_type=root.get("type", ""),
        how=root.get("how", ""),
        time=root.get("time", ""),
        start=root.get("start", ""),
        stale=root.get("stale", ""),
        raw_xml=xml_str,
    )

    point = root.find("point")
    if point is not None:
        try:
            ev.lat = float(point.get("lat", 0))
            ev.lon = float(point.get("lon", 0))
            ev.hae = float(point.get("hae", 0))
            ev.ce = float(point.get("ce", 0))
            ev.le = float(point.get("le", 0))
        except (ValueError, TypeError):
            ev.lat = ev.lon = ev.hae = ev.ce = ev.le = None

    detail = root.find("detail")
    if detail is not None:
        ev.detail_xml = ET.tostring(detail, encoding="unicode")
        contact = detail.find("contact")
        if contact is not None:
            ev.callsign = contact.get("callsign", "")
        group = detail.find("__group")
        if group is not None:
            ev.group_name = group.get("name", "")
            ev.group_role = group.get("role", "")
        remarks = detail.find("remarks")
        if remarks is not None and remarks.text:
            ev.remarks = remarks.text
        track = detail.find("track")
        if track is not None:
            try:
                if track.get("course") is not None:
                    ev.course_deg = float(track.get("course"))
                if track.get("speed") is not None:
                    ev.speed_mps = float(track.get("speed"))
            except (ValueError, TypeError):
                pass
        ev.geometry_points = extract_geometry_points(detail)

    return ev


def extract_geometry_points(detail: ET.Element) -> List[GeometryPoint]:
    """Ordered (lat, lon, hae) vertices from geometry-bearing detail elements.

    CoT encodes route/polygon vertices two ways:
    - any element with ``point="lat,lon[,hae]"`` (ATAK link elements)
    - elements with explicit ``lat``/``lon``[/``hae``] attributes

    Document order is preserved; ring-closing repeats (first == last) are
    kept; only *immediately* repeated duplicates are collapsed.
    """
    points: List[GeometryPoint] = []

    def add(lat: float, lon: float, hae: float) -> None:
        key = (lat, lon, hae)
        if points and points[-1] == key:
            return
        points.append(key)

    for elem in detail.iter():
        # <link> carries geometry only as a route/shape vertex
        # (relation="c"). Association links (relation="p-p" parent,
        # "none" in delete tombstones, ...) point AT another object and
        # must not be misread as this object's geometry.
        if elem.tag == "link":
            relation = elem.get("relation")
            if relation is not None and relation != "c":
                continue

        point_str = elem.get("point")
        if point_str:
            parts = [p.strip() for p in point_str.split(",")]
            if len(parts) >= 2:
                try:
                    lat = float(parts[0])
                    lon = float(parts[1])
                    hae = float(parts[2]) if len(parts) > 2 and parts[2] != "" else 0.0
                    add(lat, lon, hae)
                    continue
                except (ValueError, TypeError):
                    pass

        lat_s = elem.get("lat")
        lon_s = elem.get("lon")
        if lat_s is None or lon_s is None:
            continue
        try:
            hae_s = elem.get("hae")
            add(float(lat_s), float(lon_s), float(hae_s) if hae_s is not None else 0.0)
        except (ValueError, TypeError):
            continue

    return points


def classify_event(event_type: str, how: str = "", closed: Optional[bool] = None) -> str:
    """Classify a CoT type into a coarse layer name.

    Returns one of ``positions`` / ``markers`` / ``routes`` / ``areas`` /
    ``other``. Friendly ground SA (``a-f-G``) is always a position
    regardless of ``how``, because ATAK sends ``h-g-i-g-o`` / ``h-e`` for
    automatic SA reports. For other atoms ``how`` discriminates:
    machine-generated → positions, human-placed → markers.

    ``u-d-f`` (user-drawn freehand) is ambiguous by type alone — an open
    path or a closed area. Pass ``closed`` (e.g. from a repeated
    first/last geometry vertex) to resolve it; without the hint it
    defaults to ``routes``.
    """
    if event_type.startswith("a-"):
        if event_type.startswith("a-f-G"):
            return "positions"
        if how.startswith("m-"):
            return "positions"
        if how.startswith("h-"):
            return "markers"
        if event_type.startswith("a-f-"):
            return "positions"
        return "markers"
    if event_type.startswith("b-m-p"):
        return "markers"
    if event_type.startswith("u-d-f"):
        return "areas" if closed else "routes"
    if event_type.startswith(("b-m-r", "u-d-r")):
        return "routes"
    if event_type.startswith("u-d-c"):
        return "areas"
    return "other"
