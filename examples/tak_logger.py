#!/usr/bin/env python3
"""Watch everything happening on a TAK server, as typed events.

    python examples/tak_logger.py 10.0.0.5

Connects like a TAK client (registers itself, keeps alive, reconnects
through server restarts) and prints every event. This skeleton is the
starting point for recorders, relays, geofence triggers, dashboards —
replace print() with whatever your system needs.
"""

import sys
import time

from cotkit import classify_event, ots_client

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"


def on_event(ev):
    layer = classify_event(ev.event_type, ev.how)
    where = f"{ev.lat:.5f},{ev.lon:.5f}" if ev.has_point else f"{len(ev.geometry_points)} vertices"
    print(f"[{layer:9s}] {ev.uid:30s} {ev.event_type:12s} {ev.callsign:15s} {where}")


client = ots_client(host, on_event=on_event,
                    on_connect=lambda: print(f"connected to {host}"),
                    on_disconnect=lambda e: print(f"disconnected ({e}); retrying..."))
client.start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    client.stop()
