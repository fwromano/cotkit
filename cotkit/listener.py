"""Accept-side CoT ingest: receive events from clients that connect to you.

This is the gateway pattern: your process is the server, TAK bridges or
injectors dial in and stream events. Each connection gets its own
framing parser; every complete event is delivered to your callback.

For talking *to* a TAK server, use ``TakClient``/``TakSender`` instead.
"""

from __future__ import annotations

import logging
import socket
import socketserver
import threading
from typing import Callable, Optional, Set, Tuple

from .framing import CotStreamParser
from .model import CotEvent, parse_event

__all__ = ["CotListener"]

_LOG = logging.getLogger("cotkit.listener")


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        listener: "CotListener" = self.server.cot_listener  # type: ignore[attr-defined]
        if not listener._register(self.request):
            return  # over max_connections — refused
        parser = CotStreamParser()
        try:
            while True:
                try:
                    data = self.request.recv(65536)
                except OSError:
                    break
                if not data:
                    break
                for xml in parser.feed(data):
                    listener._dispatch(xml, self.client_address)
        finally:
            listener._unregister(self.request)


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class CotListener:
    """Threaded TCP listener that yields parsed CoT events.

    Usage::

        def on_event(ev, peer):
            print(peer, ev.uid, ev.event_type)

        listener = CotListener("0.0.0.0", 9500, on_event=on_event)
        listener.start()
        ...
        listener.stop()   # also closes established peer connections

    ``port=0`` binds an ephemeral port; read ``listener.port`` after
    ``start()`` (useful in tests). Callbacks run on per-connection
    threads; exceptions they raise are logged (``cotkit.listener``
    logger) and do not tear down the peer's connection.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        on_event: Optional[Callable[[CotEvent, Tuple[str, int]], None]] = None,
        on_raw: Optional[Callable[[str, Tuple[str, int]], None]] = None,
        max_connections: int = 64,
    ):
        self.host = host
        self.port = port
        self.on_event = on_event
        self.on_raw = on_raw
        # Each connection holds a thread and a framing buffer (up to
        # 4 MiB); an unauthenticated ingest port needs a ceiling or it
        # is a trivial exhaustion target. Excess connections are closed
        # on accept.
        self.max_connections = max_connections
        self._server: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None
        self._connections: Set[socket.socket] = set()
        self._conn_lock = threading.Lock()

    def start(self) -> "CotListener":
        """Bind and serve on a background thread; returns self."""
        if self._server is not None:
            return self
        self._server = _Server((self.host, self.port), _Handler)
        self._server.cot_listener = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
            name=f"CotListener(:{self.port})",
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop accepting and close all established peer connections."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        # socketserver doesn't track accepted sockets — close them
        # ourselves so handler threads unblock and peers see the close.
        with self._conn_lock:
            conns, self._connections = list(self._connections), set()
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "CotListener":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- internal, called from handler threads -----------------------------

    def _register(self, conn: socket.socket) -> bool:
        with self._conn_lock:
            if len(self._connections) >= self.max_connections:
                _LOG.warning("connection refused: max_connections=%d reached",
                             self.max_connections)
                return False
            self._connections.add(conn)
            return True

    def _unregister(self, conn: socket.socket) -> None:
        with self._conn_lock:
            self._connections.discard(conn)

    def _dispatch(self, xml: str, peer: Tuple[str, int]) -> None:
        if self.on_raw:
            try:
                self.on_raw(xml, peer)
            except Exception:
                _LOG.exception("on_raw callback raised; connection continues")
        if self.on_event:
            event = parse_event(xml)
            if event is not None:
                try:
                    self.on_event(event, peer)
                except Exception:
                    _LOG.exception("on_event callback raised; connection continues")
