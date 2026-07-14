"""cotkit — a small, solid Cursor-on-Target (CoT) bridge library.

Stdlib-only building blocks for talking to TAK servers and moving CoT
in and out of anything else: stream framing, a typed event model,
escaping-safe builders, reconnecting clients, an accept-side listener,
and an offline fake TAK server for tests.

Quickstart::

    from cotkit import TakClient, TlsConfig, build_event

    client = TakClient("10.0.0.5", 8089, tls=TlsConfig(
        client_cert="me.pem", client_key="me.key", ca_cert="ca.pem",
    ), on_event=lambda ev: print(ev.uid, ev.lat, ev.lon))
    client.start()
    client.send(build_event("sensor-7", "a-f-G-E-S", 30.62, -96.34,
                            callsign="Sensor 7"))
"""

from .arcgis import ArcGisError, esri_to_geojson, query_features
from .build import (
    ICON_SHORTCUTS,
    argb_to_signed_int_string,
    build_delete_event,
    build_event,
    build_ping,
    build_sa_event,
    resolve_event_type,
)
from .client import Backoff, SaIdentity, TakClient, TakSender, TlsConfig, open_tak_socket
from .framing import CotStreamParser
from .geojson import event_to_feature, feature_to_event, feature_to_events
from .listener import CotListener
from .model import CotEvent, classify_event, extract_geometry_points, parse_event
from .ots import OTS_API_PORT, OTS_SSL_PORT, OTS_TCP_PORT, ots_client, ots_sender
from .timestamps import cot_time, cot_time_offset

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # model
    "CotEvent", "parse_event", "classify_event", "extract_geometry_points",
    # framing
    "CotStreamParser",
    # build
    "build_event", "build_sa_event", "build_delete_event", "build_ping", "resolve_event_type",
    "ICON_SHORTCUTS", "argb_to_signed_int_string",
    # client
    "TakClient", "TakSender", "TlsConfig", "SaIdentity", "Backoff", "open_tak_socket",
    # listener
    "CotListener",
    # OpenTAKServer conveniences
    "ots_client", "ots_sender", "OTS_TCP_PORT", "OTS_SSL_PORT", "OTS_API_PORT",
    # GeoJSON bridge
    "feature_to_event", "feature_to_events", "event_to_feature",
    # ArcGIS ingest
    "query_features", "esri_to_geojson", "ArcGisError",
    # time
    "cot_time", "cot_time_offset",
]
