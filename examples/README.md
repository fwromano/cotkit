# Examples

Each script is self-contained and runs against any TAK server
(OpenTAKServer ports assumed; pass your server's IP as the argument).

| Script | Shows |
|---|---|
| `send_marker.py` | Hello-world: one marker on the map |
| `vehicle_tracker.py` | Moving track: update-in-place, course/speed, clean delete |
| `geojson_to_tak.py` | A whole GeoJSON file → TAK objects |
| `tak_logger.py` | Subscribe to everything, typed, reconnecting |
| `arcgis_to_tak.py` | Live NIFC fire perimeters (ArcGIS REST) → TAK |

No TAK server handy? Every pattern here also runs against
`cotkit.testing.FakeTakServer` — see the test suite for wiring.

TLS note: these use the plaintext port (8088) for brevity. For the SSL
port, pass a `TlsConfig` — the same helpers switch to 8089 automatically:

```python
from cotkit import TlsConfig, ots_sender
sender = ots_sender("10.0.0.5", tls=TlsConfig(
    client_cert="me.pem", client_key="me.key", ca_cert="ca.pem"))
```
