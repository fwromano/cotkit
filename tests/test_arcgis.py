"""ArcGIS ingest: Esri JSON conversion and the query/pagination client
(against an in-process HTTP server — no ArcGIS required)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from cotkit.arcgis import ArcGisError, esri_to_geojson, query_features
from cotkit.geojson import feature_to_event
from cotkit.model import parse_event


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------

def test_point_feature_conversion_and_id():
    f = esri_to_geojson({
        "attributes": {"OBJECTID": 7, "NAME": "Hydrant 7"},
        "geometry": {"x": -96.3, "y": 30.6, "z": 100.0},
    })
    assert f["id"] == 7
    assert f["geometry"] == {"type": "Point", "coordinates": [-96.3, 30.6, 100.0]}
    assert f["properties"]["NAME"] == "Hydrant 7"


def test_polygon_rings_and_paths_conversion():
    poly = esri_to_geojson({"attributes": {}, "geometry": {
        "rings": [[[-96.1, 30.1], [-96.2, 30.2], [-96.1, 30.3], [-96.1, 30.1]]]}})
    assert poly["geometry"]["type"] == "Polygon"
    line = esri_to_geojson({"attributes": {}, "geometry": {
        "paths": [[[-96.1, 30.1], [-96.2, 30.2]]]}})
    assert line["geometry"]["type"] == "LineString"
    multi = esri_to_geojson({"attributes": {}, "geometry": {
        "paths": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]}})
    assert multi["geometry"]["type"] == "MultiLineString"


def test_empty_and_missing_geometry():
    assert esri_to_geojson({"attributes": {"OBJECTID": 1}})["geometry"] is None
    assert esri_to_geojson({"attributes": {}, "geometry": {"x": None, "y": None}})["geometry"] is None


def test_converted_feature_flows_into_cot():
    """The whole point: ArcGIS feature → GeoJSON → CoT event."""
    f = esri_to_geojson({
        "attributes": {"OBJECTID": 3, "name": "Station 3"},
        "geometry": {"x": -96.33, "y": 30.61},
    })
    ev = parse_event(feature_to_event(f, event_type="a-f-G-E-S"))
    assert ev.callsign == "Station 3"
    assert abs(ev.lat - 30.61) < 1e-9  # axis order verified end-to-end
    assert abs(ev.lon - -96.33) < 1e-9


# --------------------------------------------------------------------------
# Query client against a fake ArcGIS REST endpoint
# --------------------------------------------------------------------------

class _FakeArcGis(BaseHTTPRequestHandler):
    # Two pages of two features, then done.
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        self.server.requests.append(qs)
        offset = int(qs["resultOffset"][0])
        if qs.get("where", [""])[0] == "explode":
            body = {"error": {"code": 400, "message": "bad where", "details": []}}
        else:
            start = offset
            feats = [{"attributes": {"OBJECTID": i}, "geometry": {"x": float(i), "y": 1.0}}
                     for i in range(start, min(start + 2, 3))]
            body = {"features": feats, "exceededTransferLimit": start + 2 < 3}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture()
def fake_service():
    server = HTTPServer(("127.0.0.1", 0), _FakeArcGis)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/FeatureServer/0"
    yield url, server
    server.shutdown()
    server.server_close()


def test_query_paginates_and_converts(fake_service):
    url, server = fake_service
    features = query_features(url, page_size=2)
    assert [f["id"] for f in features] == [0, 1, 2]  # both pages, in order
    assert features[0]["geometry"]["type"] == "Point"
    assert len(server.requests) == 2  # exactly two pages fetched


def test_query_always_forces_wgs84_and_passes_token_and_bbox(fake_service):
    url, server = fake_service
    query_features(url, token="SECRET", bbox=(-97.8, 30.2, -97.5, 30.5))
    qs = server.requests[0]
    assert qs["outSR"] == ["4326"]          # the projection footgun, pinned
    assert qs["token"] == ["SECRET"]
    assert qs["geometryType"] == ["esriGeometryEnvelope"]
    assert qs["geometry"] == ["-97.8,30.2,-97.5,30.5"]


def test_query_max_features_truncates(fake_service):
    url, _ = fake_service
    assert len(query_features(url, page_size=2, max_features=2)) == 2


def test_query_surfaces_esri_errors(fake_service):
    url, _ = fake_service
    with pytest.raises(ArcGisError, match="bad where"):
        query_features(url, where="explode")
