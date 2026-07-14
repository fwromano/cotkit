#!/usr/bin/env python3
"""Hello, TAK: put one marker on every connected ATAK/iTAK map.

    python examples/send_marker.py 10.0.0.5

Assumes OpenTAKServer's plaintext port (8088). That's it — if you can
see the marker appear, you've bridged to TAK.
"""

import sys

from cotkit import build_event, ots_sender

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"

with ots_sender(host) as sender:
    sender.send(build_event(
        "demo-marker-1",          # uid: re-send with the same uid to update
        "a-u-G",                  # CoT type: unknown ground point
        30.6187, -96.3365,        # lat, lon
        callsign="Hello from cotkit",
        remarks="sent by examples/send_marker.py",
        stale_seconds=300,        # disappears after 5 minutes
    ))

print(f"marker sent to {host}:8088 — check an ATAK map connected to that server")
