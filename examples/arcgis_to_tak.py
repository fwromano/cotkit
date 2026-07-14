#!/usr/bin/env python3
"""Live US wildland fire perimeters (NIFC ArcGIS) → TAK maps.

    python examples/arcgis_to_tak.py 10.0.0.5 [-97.8 30.2 -97.5 30.5]

Works with ANY ArcGIS Feature Service layer — including the private
hosted layers behind ESRI Field Maps (add token="..." to the query).
"""

import sys

from cotkit import feature_to_events, ots_sender, query_features

WFIGS_PERIMETERS = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services"
    "/WFIGS_Interagency_Perimeters/FeatureServer/0"
)

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
bbox = tuple(float(v) for v in sys.argv[2:6]) if len(sys.argv) >= 6 else \
    (-98.5, 29.5, -95.5, 31.5)  # central Texas by default

print(f"querying fire perimeters in bbox {bbox} ...")
features = query_features(WFIGS_PERIMETERS, bbox=bbox,
                          out_fields="poly_IncidentName", max_features=100)
print(f"{len(features)} perimeter(s) found")

sent = 0
with ots_sender(host) as sender:
    for feature in features:
        name = (feature.get("properties") or {}).get("poly_IncidentName") or "Fire"
        sent += sender.send(feature_to_events(
            feature, callsign=str(name), color_argb="FFFF4500",
            stale_seconds=3600,
        ))

print(f"sent {sent} objects to {host}:8088 (stale in 1 h)")
