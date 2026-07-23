"""Datagram-side CoT ingest.

Some simulators and sensor gateways emit exactly one CoT ``<event>`` per UDP
datagram.  ``UdpCotListener`` turns that feed into the same typed
``CotEvent`` objects used by the rest of cotkit while retaining the source
address and original XML.

UDP is intentionally treated as a datagram protocol here: events split
across packets are invalid, and multiple events packed into one packet are
not silently guessed apart.  Use :class:`cotkit.listener.CotListener` for a
framed TCP stream.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Callable, Optional, Tuple

from .model import CotEvent, parse_event

__all__ = ["UdpCotListener", "sanitize_cot_datagram"]

_LOG = logging.getLogger("cotkit.udp")

MAX_UDP_DATAGRAM = 65535
DEFAULT_SOCKET_BUFFER = 4 * 1024 * 1024


def sanitize_cot_datagram(data: bytes) -> Optional[str]:
    """Return one UTF-8 CoT document from a datagram, or ``None``.

    Field emitters sometimes append NULs/newlines or prepend a small transport
    label.  Those wrappers are removed, as is the common ``hello`` probe.  A
    malformed UTF-8 payload is rejected rather than decoded lossy: changing
    bytes inside a UID or callsign would create a different tactical object.
    """
    if not data:
        return None

    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            data = data.decode("utf-16").encode("utf-8")
        except UnicodeError:
            return None

    payload = data.replace(b"\x00", b"").strip(b"\r\n\t ")
    if not payload or payload.lower() == b"hello":
        return None

    xml_start = payload.find(b"<")
    xml_end = payload.rfind(b">")
    if xml_start < 0 or xml_end < xml_start:
        return None
    payload = payload[xml_start : xml_end + 1]

    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


class UdpCotListener:
    """Receive one complete CoT event per UDP datagram.

    ``on_event`` receives ``(CotEvent, peer)`` for valid events. ``on_raw``
    receives sanitized XML before parsing, which is useful for lossless
    forwarding.  Callback exceptions are logged and do not stop reception.

    ``port=0`` requests an ephemeral port; read ``listener.port`` after
    :meth:`start`.  Counters are safe to read for operator status output.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        on_event: Optional[Callable[[CotEvent, Tuple[str, int]], None]] = None,
        on_raw: Optional[Callable[[str, Tuple[str, int]], None]] = None,
        recv_size: int = MAX_UDP_DATAGRAM,
        socket_buffer: int = DEFAULT_SOCKET_BUFFER,
        timeout: float = 0.5,
    ):
        if recv_size < 1 or recv_size > MAX_UDP_DATAGRAM:
            raise ValueError(f"recv_size must be between 1 and {MAX_UDP_DATAGRAM}")
        if socket_buffer < 1:
            raise ValueError("socket_buffer must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        self.host = host
        self.port = port
        self.on_event = on_event
        self.on_raw = on_raw
        self.recv_size = recv_size
        self.socket_buffer = socket_buffer
        self.timeout = timeout

        self.datagrams_received = 0
        self.events_received = 0
        self.invalid_datagrams = 0
        self.callback_errors = 0
        self.actual_socket_buffer = 0

        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()

    def start(self) -> "UdpCotListener":
        """Bind and receive on a background thread; returns ``self``."""
        if self._thread is not None and self._thread.is_alive():
            return self

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.socket_buffer)
        except OSError:
            _LOG.warning("could not set UDP receive buffer to %d bytes", self.socket_buffer)
        try:
            self.actual_socket_buffer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        except OSError:
            self.actual_socket_buffer = 0
        try:
            sock.bind((self.host, self.port))
            sock.settimeout(self.timeout)
        except Exception:
            sock.close()
            raise

        self._sock = sock
        self.port = sock.getsockname()[1]
        self._running.set()
        self._thread = threading.Thread(
            target=self._receive_loop,
            daemon=True,
            name=f"UdpCotListener(:{self.port})",
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop reception and release the UDP port."""
        self._running.clear()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(2.0, self.timeout * 2))
        self._thread = None

    def __enter__(self) -> "UdpCotListener":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def _receive_loop(self) -> None:
        while self._running.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                data, peer = sock.recvfrom(self.recv_size)
            except socket.timeout:
                continue
            except OSError:
                if self._running.is_set():
                    _LOG.exception("UDP receive failed")
                break

            self.datagrams_received += 1
            xml = sanitize_cot_datagram(data)
            if xml is None:
                self.invalid_datagrams += 1
                continue

            if self.on_raw:
                try:
                    self.on_raw(xml, peer)
                except Exception:
                    self.callback_errors += 1
                    _LOG.exception("on_raw callback raised; UDP reception continues")

            event = parse_event(xml)
            if event is None or not event.uid or not event.event_type:
                self.invalid_datagrams += 1
                continue

            self.events_received += 1
            if self.on_event:
                try:
                    self.on_event(event, peer)
                except Exception:
                    self.callback_errors += 1
                    _LOG.exception("on_event callback raised; UDP reception continues")
