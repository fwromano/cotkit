# cotkit

**Working with TAK? Here's the toolbox.**

cotkit is a small Python library for moving **Cursor-on-Target (CoT)**
data into and out of TAK systems — ATAK, iTAK, WinTAK, OpenTAKServer,
TAK Server. If you have data with a lat/lon, cotkit puts it on every
connected TAK map. If TAK has data you need, cotkit hands it to you as
typed Python objects.

- **Zero dependencies.** Pure standard library. `pip install` it or
  copy the `cotkit/` folder onto an air-gapped field box — identical
  behavior either way.
- **Field-derived.** Distilled from bridge code that ran real
  multi-agency exercises; the keepalive cadences, reconnect backoff,
  and TLS defaults are the ones that survived contact with the field.
- **Testable offline.** Ships a `FakeTakServer`, so your integration
  tests need no TAK server, no network, no certificates.

## The three things people do with it

### 1. Inject data into a TAK server

Sensors, vehicle feeds, CAD exports, scripts — anything that knows a
position becomes a map object. Assuming OpenTAKServer (defaults to its
ports — 8088 plaintext / 8089 TLS):

```python
from cotkit import ots_sender, build_event

with ots_sender("10.0.0.5") as sender:
    sender.send(build_event(
        "engine-41", "a-f-G-E-V-C",       # friendly ground vehicle
        30.6187, -96.3365,
        callsign="Engine 41",
        course_deg=270, speed_mps=12.0,   # renders as a moving track
        group_name="Blue",
        remarks="responding",
    ))
```

Have GeoJSON instead? (ArcGIS/QGIS exports, open-data portals):

```python
import json
from cotkit import ots_sender, feature_to_events

fc = json.load(open("hydrants.geojson"))
with ots_sender("10.0.0.5") as sender:
    for feature in fc["features"]:
        sender.send(feature_to_events(feature))  # handles Multi* too
```

Axis order (`[lon, lat]` vs `lat/lon`) is handled for you. Prefer not
to learn CoT type strings? `build_event` also takes friendly aliases:
`"friendly"`, `"hostile"`, `"neutral"`, `"unknown"` — and rejects
typos loudly instead of guessing.

Or straight from ArcGIS — any Feature Service layer, including the
hosted layers behind ESRI Field Maps (pass a `token` for private ones):

```python
from cotkit import query_features, feature_to_events, ots_sender

features = query_features(
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services"
    "/WFIGS_Interagency_Perimeters/FeatureServer/0",   # live US fire perimeters
    bbox=(-97.8, 30.2, -97.5, 30.5),
)
with ots_sender("10.0.0.5") as sender:
    for feature in features:
        sender.send(feature_to_events(feature, stale_seconds=3600))
```

Pagination, Esri-JSON conversion, and the projection footgun
(`outSR=4326`, always) are handled inside `query_features`.

### 2. Subscribe to what's happening on the TAK network

Recorders, dashboards, relays, triggers. `TakClient` registers itself
like a TAK client (SA event + keepalive), hands every event to your
callback as a typed `CotEvent`, and rides out server restarts with
jittered exponential backoff:

```python
from cotkit import ots_client, TlsConfig

def on_event(ev):
    print(ev.uid, ev.event_type, ev.callsign, ev.lat, ev.lon)

client = ots_client("10.0.0.5",
                    tls=TlsConfig(client_cert="me.pem", client_key="me.key",
                                  ca_cert="ca.pem"),
                    on_event=on_event)
client.run_forever()   # owns this thread and reconnects forever;
                       # or client.start() to run it in the background
                       # (then keep your process alive yourself)
```

`client.send(xml)` works from any thread at any time — events queue
until the connection is up and survive reconnects.

Going the other way for GIS consumers: `event_to_feature(ev)` turns any
received event back into a GeoJSON Feature.

### 3. Be a CoT endpoint yourself

Other systems stream CoT *to you* (gateway/ingest pattern):

```python
import time
from cotkit import CotListener

with CotListener("0.0.0.0", 9500,
                 on_event=lambda ev, peer: print(peer, ev.uid)):
    while True:
        time.sleep(1)
```

## Toolbox reference

Runnable scripts for the main flows live in [`examples/`](examples/);
the full API surface is in `cotkit/__init__.py` — everything exported
there is public and documented in its docstring.

| Tool | What it does |
|---|---|
| `build_event` / `build_sa_event` / `build_ping` | Escaping-safe CoT XML (note 1) |
| `build_delete_event` | Remove an object from all maps (markers AND shapes) |
| `parse_event` / `CotEvent` | XML → typed event: point, geometry, track, dedup signature (note 2) |
| `TakClient` | Reconnecting subscriber/sender with SA keepalive |
| `TakSender` | Lazy outbound sender: connect on demand, one retry (note 5) |
| `CotListener` | Accept-side ingest |
| `UdpCotListener` | One-event-per-datagram UDP ingest for simulators and sensor gateways |
| `ots_client` / `ots_sender` | The above, pre-wired for OpenTAKServer ports |
| `feature_to_event` / `event_to_feature` | GeoJSON ↔ CoT, both directions |
| `query_features` / `esri_to_geojson` | ArcGIS Feature Services → GeoJSON (Field Maps layers) |
| `TlsConfig` | Client certs, CA pinning, verify-by-default (note 3) |
| `CotStreamParser` | Raw TCP → complete events (note 4) |
| `classify_event` | CoT type → positions / markers / routes / areas |
| `cotkit.testing.FakeTakServer` | In-process TAK port for your tests |

Notes:
1. All XML is built with ElementTree — a callsign containing `"` or `<`
   cannot produce malformed wire data — and coordinates are validated
   (finite, in range) so upstream GPS glitches fail loudly instead of
   poisoning the stream.
2. `CotEvent.signature()` hashes content but not timestamps: skip
   retransmitting unchanged objects on constrained links.
3. Trust model: DNS names and public IPs are always verified; loopback
   and Tailscale-CGNAT IP literals are exempt (the transport is already
   authenticated); plain-LAN addresses are NOT exempt — pin the server
   CA with `ca_cert` (every TAK data package ships it). Hostname
   binding is never enforced (TAK certs are rarely hostname-valid), so
   the pinned CA is the trust boundary.
4. Survives events split anywhere across TCP reads — mid-tag and even
   mid-UTF-8-character — caps buffer growth, and a garbage fragment
   can't swallow the next valid event.
5. `TakSender` retries a failed batch whole: ship idempotent upserts
   (same-uid re-send is an update), or gate on `signature()`.

## Testing your bridge (no infrastructure)

```python
from cotkit import TakClient, build_event
from cotkit.testing import FakeTakServer, wait_for

def test_my_bridge_sees_events():
    with FakeTakServer() as server:
        seen = []
        with TakClient(server.host, server.port, on_event=seen.append):
            wait_for(lambda: server.connection_count == 1)
            server.broadcast(build_event("other-unit", "a-f-G", 30.0, -96.0))
            assert wait_for(lambda: len(seen) == 1)
```

## Install

```bash
pip install git+https://github.com/tamu-edu/cotkit.git
```

or vendor it: copy the `cotkit/` directory into your project (it's
stdlib-only, so the copy has no transitive footprint). Record the
commit hash you vendored so drift is detectable.

## Design rules (for contributors)

1. **The core stays stdlib-only.**
2. **All XML through ElementTree.** No string-built XML, ever.
3. **Every network behavior is testable against `FakeTakServer`.**
4. **Policies are explicit** — named constants and dataclasses, not
   buried literals.

See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## What kind of data can TAK carry? (read before asking "can I push X?")

Think of geo-data as a 2×2: static/live × vector/raster. TAK's CoT
stream — the thing cotkit speaks — is the **vector column**:

```text
                VECTOR                          RASTER
         ┌──────────────────────────┬─────────────────────────────┐
 STATIC  │ Boundaries, hydrants,    │ Basemaps, imagery, DEMs     │
         │ pre-planned zones        │ → map-layer plane: tile     │
         │ → geojson / arcgis       │   servers, imagery packages │
         │   modules + a sender     │   (NOT CoT — not cotkit)    │
         ├──────────────────────────┼─────────────────────────────┤
 LIVE    │ Unit positions, tracks,  │ Weather radar, model output │
         │ drawings — CoT's native  │ → no TAK channel exists;    │
         │ domain → TakClient/      │   vectorize it (contours →  │
         │   TakSender              │   polygons) and stream that │
         └──────────────────────────┴─────────────────────────────┘
```

There is no separate "static layer" on the CoT plane — everything is an
event with a `stale` time. *Live* just means re-sending the same uid
frequently with a short TTL (that's an update); *static* means sending
once with a long one. Same wire, same five lines of code.

Rasters never travel over CoT. Basemaps and imagery reach TAK clients
as map sources (tile URLs) or side-loaded imagery packages — different
plane, different plumbing. For *live* rasters (spread models, weather),
the working pattern is to contour them into polygons and stream those
as CoT drawings — `build_event(geometry_points=..., closed=True)` plus
`build_delete_event` for retired contours is exactly that flow.

## Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| Nothing appears on the map | wrong port — see note 1 |
| Object appears, then vanishes | `stale_seconds` elapsed — see note 2 |
| Two copies of my object | uid changed between sends — see note 3 |
| Polygon draws as an open line | not closed — see note 4 |
| `SSLCertVerificationError` | unpinned CA — see note 5 |
| `ValueError` from `build_event` | bad input, on purpose — see note 6 |
| Client sends nothing, no errors | `send()` queues — see note 7 |
| Deleted object still on map | you re-sent it after deleting; stop sending that uid |

1. Plaintext CoT and TLS CoT are different ports (OTS: 8088 vs 8089).
   A TLS client on the plaintext port (or vice versa) connects and then
   sees nothing. Also confirm the server firewall actually exposes the
   port to *your* network.
2. `stale` tells TAK clients when to discard. Live feeds should re-send
   the same uid faster than the TTL; one-shot imports want a long
   `stale_seconds`.
3. The uid IS the object identity: same uid = update, new uid = new
   object. Keep uids stable across restarts of your bridge.
4. Pass `closed=True` (or use a GeoJSON Polygon, which closes itself).
5. You're talking TLS to a DNS name / public IP / LAN address without
   pinning. Point `TlsConfig(ca_cert=...)` at the server CA — it's in
   every TAK data package (`caCert.p12`/`truststore` — convert with
   openssl) or ask the server admin.
6. cotkit refuses NaN/Inf/out-of-range coordinates and unknown type
   aliases at build time so they can't poison the stream — fix the
   input, don't catch-and-ignore.
7. `TakClient.send()` is queued until the connection is up (check
   `client.connected`, or use `TakSender` for connect-now semantics).

## FAQ

**How is this different from pytak?** Different layer. pytak is a
mature asyncio *transport framework* — you subclass workers and move
CoT bytes through queues; generating/parsing the CoT itself is mostly
up to you. cotkit is a synchronous *data toolkit*: builders, typed
parsing, GeoJSON/ArcGIS conversion, dedup, plus a reconnecting client —
callable from a ten-line script with no asyncio. pytak also speaks UDP
mesh multicast and TAK-protocol protobuf, which cotkit (TCP/TLS XML
streaming only) deliberately doesn't yet. If you're building an
always-on multicast gateway, use pytak; if you want your data on a TAK
map this afternoon with tests, use cotkit. They can coexist — cotkit's
builders work fine inside a pytak worker.

**Where do certificates come from?** Your TAK server admin — every
enrollment data package contains the client cert/key and the server CA.
For OpenTAKServer they're minted by the server's CA on package
generation.

**Can I push imagery / rasters?** No — not a cotkit limitation, a CoT
one. See the data-quadrant section above for what to do instead.

**Is it thread-safe?** `TakClient.send()` and `TakSender.send()` are
callable from any thread. Callbacks arrive on cotkit's threads — hand
off to your own queue if you do heavy work.

**Full API?** Every export is documented in-source:
`python -m pydoc cotkit` (or `cotkit.client`, `cotkit.build`, ...).

## Scope

cotkit is the *bridge* layer. Deliberately out of scope: TAK server
implementations, Marti/mission REST APIs, data-package generation,
persistence, dashboards, tile/imagery serving (see the quadrant above),
and credential minting for ArcGIS (hand `query_features` a token).
Build those on top.

## License

MIT — see [LICENSE](LICENSE).
