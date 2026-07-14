"""ArcGIS Feature Service ingest: Esri REST → GeoJSON → CoT.

Most agency GIS lives in ArcGIS (ArcGIS Online "hosted feature layers",
Enterprise feature services, ESRI Field Maps edits land in these).
This module reads them with nothing but the standard library:

    from cotkit import query_features, feature_to_events, ots_sender

    features = query_features(
        "https://services3.arcgis.com/.../FeatureServer/0",
        bbox=(-97.8, 30.2, -97.5, 30.5),          # lon/lat envelope
        token="...",                              # private layers only
    )
    with ots_sender("10.0.0.5") as sender:
        for f in features:
            sender.send(feature_to_events(f))

Design notes (learned from field use):
- Requests Esri JSON (``f=json``) and converts locally — older
  Enterprise servers don't support ``f=geojson``, and one code path
  beats content sniffing.
- Always queries with ``outSR=4326``: Esri servers otherwise return the
  layer's native projection (often web-mercator meters), which would be
  silently catastrophic downstream.
- Paginates with ``resultOffset`` until the server stops reporting
  ``exceededTransferLimit``.
- Auth is a pass-through ``token`` string. Minting tokens (AGOL/Portal
  OAuth, generateToken) is deliberately out of scope — do it in your
  app, or interactively, and hand the token in.

Esri→GeoJSON conversion is exposed separately (``esri_to_geojson``) for
callers with their own transport. Simplifications for the base version:
multi-ring polygons are emitted as one Polygon (first ring exterior,
rest as holes — which basic CoT drawings drop anyway); multi-path lines
become MultiLineString.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = ["query_features", "esri_to_geojson", "ArcGisError"]

DEFAULT_TIMEOUT = 30.0
DEFAULT_PAGE_SIZE = 1000
_MAX_PAGES = 1000  # runaway-pagination backstop (1M features)


class ArcGisError(RuntimeError):
    """The service answered, but with an Esri error payload."""


def esri_to_geojson(esri_feature: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one Esri JSON feature to a GeoJSON Feature dict.

    Attributes become ``properties``; ``OBJECTID``/``GlobalID`` (either
    case) becomes the feature ``id`` when present.
    """
    attributes = esri_feature.get("attributes") or {}
    feature_id = None
    for key in ("OBJECTID", "objectid", "GlobalID", "globalid"):
        if key in attributes:
            feature_id = attributes[key]
            break

    feature: Dict[str, Any] = {
        "type": "Feature",
        "geometry": _esri_geometry_to_geojson(esri_feature.get("geometry")),
        "properties": dict(attributes),
    }
    if feature_id is not None:
        feature["id"] = feature_id
    return feature


def _esri_geometry_to_geojson(geom: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not geom:
        return None
    if "x" in geom and "y" in geom:
        if geom.get("x") is None or geom.get("y") is None:
            return None  # Esri's representation of an empty point
        coords = [geom["x"], geom["y"]]
        if geom.get("z") is not None:
            coords.append(geom["z"])
        return {"type": "Point", "coordinates": coords}
    if "paths" in geom:
        paths = geom.get("paths") or []
        if not paths:
            return None
        if len(paths) == 1:
            return {"type": "LineString", "coordinates": paths[0]}
        return {"type": "MultiLineString", "coordinates": paths}
    if "rings" in geom:
        rings = geom.get("rings") or []
        if not rings:
            return None
        # Base-version simplification: one Polygon; first ring exterior,
        # remaining rings holes. (Esri encodes multipolygons as extra
        # clockwise rings — refine here if a consumer ever needs them.)
        return {"type": "Polygon", "coordinates": rings}
    if "points" in geom:
        points = geom.get("points") or []
        if not points:
            return None
        return {"type": "MultiPoint", "coordinates": points}
    return None


def query_features(
    layer_url: str,
    *,
    where: str = "1=1",
    bbox: Optional[Tuple[float, float, float, float]] = None,
    out_fields: str = "*",
    token: Optional[str] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_features: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """Query an ArcGIS Feature Service layer; return GeoJSON Features.

    ``layer_url`` is the layer endpoint (ends in ``/FeatureServer/<n>``
    or ``/MapServer/<n>``). ``bbox`` is a lon/lat ``(xmin, ymin, xmax,
    ymax)`` envelope filter. ``token`` authenticates against private
    (e.g. Field Maps) layers. Coordinates always come back lon/lat
    WGS-84 (``outSR=4326``), ready for ``feature_to_events``.
    """
    base = layer_url.rstrip("/") + "/query"
    features: List[Dict[str, Any]] = []
    offset = 0

    for _ in range(_MAX_PAGES):
        params: Dict[str, str] = {
            "f": "json",
            "where": where,
            "outFields": out_fields,
            "outSR": "4326",
            "returnGeometry": "true",
            "resultOffset": str(offset),
            "resultRecordCount": str(page_size),
        }
        if bbox is not None:
            xmin, ymin, xmax, ymax = bbox
            params.update({
                "geometry": f"{xmin},{ymin},{xmax},{ymax}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
            })
        if token:
            params["token"] = token

        payload = _get_json(base, params, timeout)
        if "error" in payload:
            err = payload["error"]
            raise ArcGisError(
                f"ArcGIS error {err.get('code')}: {err.get('message')} "
                f"({'; '.join(err.get('details') or [])})"
            )

        page = payload.get("features") or []
        features.extend(esri_to_geojson(f) for f in page)

        if max_features is not None and len(features) >= max_features:
            return features[:max_features]
        if not payload.get("exceededTransferLimit") or not page:
            return features
        offset += len(page)

    raise ArcGisError(f"pagination did not terminate after {_MAX_PAGES} pages")


def _get_json(url: str, params: Dict[str, str], timeout: float) -> Dict[str, Any]:
    request = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "cotkit"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
