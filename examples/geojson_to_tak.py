#!/usr/bin/env python3
"""Push a whole GeoJSON file onto TAK maps — points, lines, polygons.

    python examples/geojson_to_tak.py hydrants.geojson 10.0.0.5

Any FeatureCollection works (ArcGIS/QGIS exports, open-data portals).
Feature names become callsigns; axis order is handled for you.
"""

import json
import sys

from cotkit import feature_to_events, ots_sender

if len(sys.argv) < 2:
    sys.exit("usage: geojson_to_tak.py FILE.geojson [host]")
path = sys.argv[1]
host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"

with open(path, encoding="utf-8") as fh:
    data = json.load(fh)

features = data["features"] if data.get("type") == "FeatureCollection" else [data]

sent = 0
with ots_sender(host) as sender:
    for feature in features:
        # feature_to_events handles Multi* geometries too (one event per part)
        sent += sender.send(feature_to_events(feature, stale_seconds=3600))

print(f"sent {sent} objects from {path} to {host}:8088 (stale in 1 h)")
