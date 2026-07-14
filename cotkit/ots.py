"""OpenTAKServer conveniences.

cotkit works with any TAK server, but if yours is OpenTAKServer (OTS)
these helpers bake in its port conventions so you never type them:

===========  =====  ==================================================
Port         Proto  What
===========  =====  ==================================================
8088         TCP    plaintext CoT streaming (LAN/VPN use)
8089         TLS    SSL CoT streaming (client certificate required)
8443         HTTPS  web UI + Marti API (not handled by cotkit)
===========  =====  ==================================================

``ots_client("10.0.0.5")`` → reconnecting plaintext client on :8088.
``ots_client("10.0.0.5", tls=TlsConfig(...))`` → TLS client on :8089.
Same pattern for ``ots_sender``. Explicit ``port=`` always wins.
"""

from __future__ import annotations

from typing import Optional

from .client import TakClient, TakSender, TlsConfig

__all__ = ["OTS_TCP_PORT", "OTS_SSL_PORT", "OTS_API_PORT", "ots_client", "ots_sender"]

OTS_TCP_PORT = 8088
OTS_SSL_PORT = 8089
OTS_API_PORT = 8443


def _default_port(tls: Optional[TlsConfig], port: Optional[int]) -> int:
    if port is not None:
        return port
    return OTS_SSL_PORT if tls is not None else OTS_TCP_PORT


def ots_client(host: str, *, tls: Optional[TlsConfig] = None,
               port: Optional[int] = None, **kwargs) -> TakClient:
    """A ``TakClient`` wired to OTS conventions (8088 plain / 8089 TLS)."""
    return TakClient(host, _default_port(tls, port), tls=tls, **kwargs)


def ots_sender(host: str, *, tls: Optional[TlsConfig] = None,
               port: Optional[int] = None, **kwargs) -> TakSender:
    """A ``TakSender`` wired to OTS conventions (8088 plain / 8089 TLS)."""
    return TakSender(host, _default_port(tls, port), tls=tls, **kwargs)
