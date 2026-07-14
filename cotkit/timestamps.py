"""CoT timestamp helpers.

CoT events carry three ISO-8601 UTC timestamps: ``time`` (when the event
was generated), ``start`` (when it becomes valid), and ``stale`` (when
consumers should discard it). TAK clients accept several sub-second
precisions; cotkit standardizes on milliseconds with a ``Z`` suffix,
the most compact form ATAK/iTAK/WinTAK all parse.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

__all__ = ["cot_time", "cot_time_offset"]


def cot_time(dt: datetime | None = None) -> str:
    """Format ``dt`` (default: now) as a CoT UTC timestamp string."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def cot_time_offset(seconds: float, base: datetime | None = None) -> str:
    """CoT timestamp ``seconds`` in the future (or past, if negative)."""
    if base is None:
        base = datetime.now(timezone.utc)
    return cot_time(base + timedelta(seconds=seconds))
