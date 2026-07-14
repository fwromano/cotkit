"""Offline test double for a TAK server's CoT streaming port.

Untestable network plumbing gets copy-pasted instead of refactored —
so cotkit ships its own test double. ``FakeTakServer`` runs in-process,
letting your bridge's tests (and cotkit's own) exercise connect, SA
registration, broadcast, and reconnect behavior with no infrastructure.

Not a TAK server: no auth, no groups, no Marti API — just the CoT
streaming socket contract (accept, receive framed events, broadcast).
Pass ``ssl_context`` (an ``ssl.SSLContext`` with a server cert loaded)
to exercise TLS client paths.
"""

from __future__ import annotations

import socket
import ssl
import threading
import time
from typing import List, Optional

from .framing import CotStreamParser

__all__ = ["FakeTakServer", "wait_for"]


def wait_for(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    """Poll ``predicate`` until truthy or timeout. Returns the final result.

    Use this instead of bare sleeps in tests: it keeps suites fast when
    things are quick and stable when CI is slow.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


class FakeTakServer:
    """In-process stand-in for a TAK CoT streaming port.

    Usage::

        with FakeTakServer() as server:
            client = TakClient(server.host, server.port, ...)
            ...
            server.broadcast(xml)         # push an event to all clients
            server.received               # every event any client sent us
            server.drop_all_connections() # simulate a server restart
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0,
                 ssl_context: Optional[ssl.SSLContext] = None):
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.received: List[str] = []  # complete event XML strings, in order
        self._listener: Optional[socket.socket] = None
        self._connections: List[socket.socket] = []
        self._lock = threading.Lock()
        self._threads: List[threading.Thread] = []
        self._running = False

    # -- lifecycle -----------------------------------------------------

    def start(self) -> "FakeTakServer":
        if self._listener is not None:
            return self
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(16)
        self.port = listener.getsockname()[1]
        self._listener = listener
        self._running = True
        accept_thread = threading.Thread(target=self._accept_loop, args=(listener,),
                                         daemon=True, name="FakeTakServer.accept")
        accept_thread.start()
        with self._lock:
            self._threads.append(accept_thread)
        return self

    def stop(self) -> None:
        self._running = False
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        self.drop_all_connections()
        with self._lock:
            threads, self._threads = list(self._threads), []
        for t in threads:
            t.join(timeout=2)

    def __enter__(self) -> "FakeTakServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- test controls ---------------------------------------------------

    @property
    def connection_count(self) -> int:
        with self._lock:
            return len(self._connections)

    def broadcast(self, xml: str) -> None:
        """Send an event to every connected client. Serialized under the
        server lock so concurrent broadcasts cannot interleave bytes."""
        payload = xml.encode("utf-8") + b"\n"
        with self._lock:
            for conn in list(self._connections):
                try:
                    conn.sendall(payload)
                except OSError:
                    pass

    def drop_all_connections(self) -> None:
        """Hard-close every client connection (simulates a restart)."""
        with self._lock:
            conns, self._connections = self._connections, []
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    # -- internals -------------------------------------------------------

    def _accept_loop(self, listener: socket.socket) -> None:
        while self._running:
            try:
                conn, _ = listener.accept()
            except OSError:
                break
            if self.ssl_context is not None:
                try:
                    conn = self.ssl_context.wrap_socket(conn, server_side=True)
                except (OSError, ssl.SSLError):
                    try:
                        conn.close()
                    except OSError:
                        pass
                    continue
            reader = threading.Thread(target=self._read_loop, args=(conn,),
                                      daemon=True, name="FakeTakServer.read")
            with self._lock:
                self._connections.append(conn)
                self._threads.append(reader)
            reader.start()

    def _read_loop(self, conn: socket.socket) -> None:
        parser = CotStreamParser()
        while self._running:
            try:
                data = conn.recv(65536)
            except OSError:
                break
            if not data:
                break
            events = parser.feed(data)
            if events:
                with self._lock:
                    self.received.extend(events)
        with self._lock:
            if conn in self._connections:
                self._connections.remove(conn)
        try:
            conn.close()
        except OSError:
            pass
