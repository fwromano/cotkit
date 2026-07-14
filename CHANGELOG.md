# Changelog

## Unreleased

(nothing yet)

## 0.1.0 — 2026-07-14

Initial release, distilled from ten field bridge implementations and
hardened by a three-lens adversarial review (concurrency, TAK wire
format & security, developer experience) before first publication.

- `CotStreamParser` — chunk-safe framing: split tags, split UTF-8
  characters, garbage-fragment recovery, buffer cap.
- Coordinate validation at build time (finite + in-range) and DOCTYPE
  rejection at parse time (entity-expansion defense).
- `CotEvent` / `parse_event` — typed model, geometry extraction, track
  course/speed, wall-clock-free dedup `signature()`.
- `build_event` / `build_sa_event` / `build_ping` — ElementTree-only
  builders (structural escaping), route/polygon geometry, `<track>`
  course/speed, ARGB colors.
- `TakClient` — reconnecting subscriber/sender: SA registration,
  keepalive (240 s + fast startup retries), exponential backoff with
  jitter. Single-threaded socket I/O with a bounded outbound queue:
  `send()` is non-blocking, safe before connect and across reconnects,
  and TLS connections never see concurrent read/write. Callback
  exceptions are logged, never fatal.
- `TlsConfig` — client certs, CA pinning; verification required for DNS
  names, public IPs, and plain-LAN addresses; loopback/Tailscale-CGNAT
  IP literals exempt by default.
- `TakSender` — lazy outbound sender; drains inbound broadcast traffic
  and detects server restarts before each write.
- `CotListener` — accept-side threaded ingest.
- `cotkit.ots` — OpenTAKServer port conventions (`ots_client`,
  `ots_sender`).
- `cotkit.geojson` — GeoJSON ↔ CoT both directions, Multi* expansion,
  axis-order handled, CoT types preserved on round-trip.
- `cotkit.arcgis` — ArcGIS Feature Service ingest (`query_features`):
  stdlib HTTP, Esri-JSON→GeoJSON conversion, pagination, token
  pass-through for private (Field Maps) layers, `outSR=4326` pinned.
- `build_delete_event` — t-x-d-d force-delete tombstone (removes
  markers and drawings alike; plain force-stale only clears markers).
- `cotkit.testing.FakeTakServer` + `wait_for` — offline test double.
- `examples/` — runnable scripts: marker, moving vehicle with delete,
  GeoJSON file push, typed event logger.
