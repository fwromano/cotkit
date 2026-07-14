#!/usr/bin/env python3
"""A moving vehicle track: update-in-place, then clean removal.

    python examples/vehicle_tracker.py 10.0.0.5

Demonstrates the full lifecycle every feed bridge uses:
  1. re-sending the SAME uid moves the object (upsert semantics)
  2. course/speed make TAK render it as a moving track, not a dot
  3. a delete tombstone removes it from every map when you're done
"""

import math
import sys
import time

from cotkit import build_delete_event, build_event, ots_sender

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
UID = "demo-engine-41"

with ots_sender(host) as sender:
    print("driving a lap (30 s)... watch the map")
    for step in range(30):
        angle = step * (2 * math.pi / 30)
        sender.send(build_event(
            UID, "a-f-G-E-V-C",                       # friendly ground vehicle
            30.6187 + 0.002 * math.sin(angle),
            -96.3365 + 0.002 * math.cos(angle),
            callsign="Engine 41",
            course_deg=(90 - math.degrees(angle)) % 360,
            speed_mps=12.0,
            group_name="Blue",
            stale_seconds=60,
        ))
        time.sleep(1)

    print("lap done — removing the track from all maps")
    sender.send(build_delete_event(UID, "a-f-G-E-V-C"))
