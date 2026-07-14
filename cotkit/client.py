"""TAK client connections: subscribing, sending, reconnecting.

Two classes cover the two bridge directions:

- ``TakClient`` — long-lived, reconnecting client. Registers itself with
  an SA event, keeps the registration alive, and delivers every received
  event to a callback. This is the "bridge out of CoT" building block
  (recorders, relays, dashboards).
- ``TakSender`` — lazy, fire-and-forget-or-raise outbound sender for
  bridges *into* CoT (feed injectors, exporters). Connects on first
  send; one reconnect-and-retry per batch.

Threading model (deliberate): ``TakClient`` performs ALL socket I/O on
its own single thread. ``send()`` from any thread only enqueues; the
client thread flushes the queue between read ticks. This avoids
concurrent read/write on one SSL connection (not safe at the OpenSSL
level), lets ``send()`` work before connect and across reconnects by
buffering, and makes ``send()`` non-blocking. ``TakSender`` serializes callers with a
lock and drains inbound broadcast traffic before each write.

Note on ``TakSender`` retry: a mid-batch failure retries the *whole*
batch. Only ship idempotent upserts (same-uid re-send is an update in
TAK), or track ``CotEvent.signature()`` to suppress unchanged repeats.
"""

from __future__ import annotations

import collections
import ipaddress
import logging
import random
import socket
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Union

from .build import build_sa_event
from .framing import CotStreamParser
from .model import CotEvent, parse_event

__all__ = ["TlsConfig", "SaIdentity", "Backoff", "open_tak_socket", "TakClient", "TakSender"]

_LOG = logging.getLogger("cotkit.client")

DEFAULT_READ_TICK = 0.5
DEFAULT_KEEPALIVE_INTERVAL = 240.0
STARTUP_SA_RETRIES = 3
STARTUP_SA_INTERVAL = 2.0
DEFAULT_OUTBOX_LIMIT = 4096
_DRAIN_CHUNK = 65536
_DRAIN_MAX_PER_CALL = 4 * 1024 * 1024


@dataclass
class TlsConfig:
    """TLS parameters for a TAK SSL streaming port (typically :8089).

    Trust model, explicitly:

    - DNS names and public IPs: certificate verification is REQUIRED
      (against ``ca_cert`` if provided, else the system store).
    - Loopback and CGNAT (100.64/10, i.e. WireGuard-authenticated
      Tailscale) *IP literals*: exempted from verification by default,
      because the transport itself is local or already authenticated.
      Set ``allow_unverified_local=False`` to remove even this.
    - Plain-LAN RFC-1918 addresses are NOT exempted: an on-path attacker
      at an incident is exactly the threat TLS exists for. Pin the
      server's CA with ``ca_cert`` (every TAK data package ships it), or
      consciously set ``insecure=True``.
    - Hostname/SAN binding is never enforced (``check_hostname=False``)
      — TAK server certificates are rarely hostname-valid. Consequence:
      with a pinned CA, any certificate issued by that CA is accepted,
      so treat the TAK CA as the trust boundary.
    """

    client_cert: Optional[str] = None
    client_key: Optional[str] = None
    client_password: Optional[str] = field(default=None, repr=False)
    ca_cert: Optional[str] = None
    insecure: bool = False
    allow_unverified_local: bool = True

    def build_context(self, host: str) -> ssl.SSLContext:
        """Build the ``ssl.SSLContext`` this config prescribes for ``host``."""
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.check_hostname = False  # TAK certs are rarely hostname-valid
        if self.ca_cert:
            context.load_verify_locations(cafile=self.ca_cert)
            context.verify_mode = ssl.CERT_REQUIRED
        elif self.insecure or (self.allow_unverified_local and _is_local_host(host)):
            context.verify_mode = ssl.CERT_NONE
        else:
            context.verify_mode = ssl.CERT_REQUIRED
        if self.client_cert:
            context.load_cert_chain(
                certfile=self.client_cert,
                keyfile=self.client_key,
                password=self.client_password,
            )
        return context


def _is_local_host(host: str) -> bool:
    """True for loopback and CGNAT (100.64/10, Tailscale) peers ONLY.

    Operates on the host *string*: only IP literals qualify, never DNS
    names (a name that resolves into these ranges is not exempted).
    Deliberately excludes RFC-1918 LAN ranges — see TlsConfig's trust
    model: a plain LAN offers no transport authentication.
    """
    name = (host or "").strip().lower()
    if name in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        addr = ipaddress.ip_address(name)
    except ValueError:
        return False
    return addr.is_loopback or addr in ipaddress.ip_network("100.64.0.0/10")


@dataclass
class SaIdentity:
    """How a headless client presents itself on the TAK network."""

    uid: str = ""
    callsign: str = ""
    group_name: str = "Cyan"
    group_role: str = "Team Member"
    platform: str = "cotkit"

    def __post_init__(self):
        hostname = socket.gethostname()
        if not self.uid:
            self.uid = f"cotkit-{hostname}-{uuid.uuid4().hex[:8]}"
        if not self.callsign:
            self.callsign = f"COTKIT-{hostname}"

    def sa_xml(self) -> str:
        """Render this identity as a situational-awareness event document."""
        return build_sa_event(
            self.uid,
            self.callsign,
            group_name=self.group_name,
            group_role=self.group_role,
            platform=self.platform,
        )


class Backoff:
    """Exponential backoff with jitter: 3 s → 60 s by default.

    Jitter matters in the field: after a server restart, a fleet of
    clients reconnecting on identical schedules arrives as a thundering
    herd (observed in the March 2026 incident cascade).
    """

    def __init__(self, initial: float = 3.0, maximum: float = 60.0,
                 factor: float = 2.0, jitter: float = 0.25):
        self.initial = initial
        self.maximum = maximum
        self.factor = factor
        self.jitter = jitter
        self._current = initial

    def next_delay(self) -> float:
        """Return the next jittered delay (seconds) and advance the schedule."""
        delay = self._current
        self._current = min(self._current * self.factor, self.maximum)
        spread = delay * self.jitter
        return max(0.1, delay + random.uniform(-spread, spread))

    def reset(self) -> None:
        """Return to the initial delay — call after a successful connect."""
        self._current = self.initial


def _drain_inbound(sock: socket.socket) -> bool:
    """Discard pending inbound bytes; return True if the peer has closed.

    Serves two purposes for write-mostly connections: (1) a TAK server
    broadcasts its whole feed to every connected client, so a sender
    that never reads would let its receive buffer fill and stall the
    server; (2) a received FIN is only visible by reading — without
    this, the first ``sendall`` after a server restart "succeeds" (the
    RST arrives later) and the payload is silently lost.

    Uses plain non-blocking ``recv`` (no MSG_PEEK — flags are rejected
    by SSLSocket), so it is safe on both TCP and TLS connections.
    """
    prior = sock.gettimeout()
    drained = 0
    try:
        sock.settimeout(0.0)
        while drained < _DRAIN_MAX_PER_CALL:
            try:
                chunk = sock.recv(_DRAIN_CHUNK)
            except (BlockingIOError, ssl.SSLWantReadError, InterruptedError):
                return False  # open, nothing more pending
            except OSError:
                return True
            if chunk == b"":
                return True
            drained += len(chunk)
        return False
    finally:
        try:
            sock.settimeout(prior)
        except OSError:
            pass


def open_tak_socket(
    host: str,
    port: int,
    tls: Optional[TlsConfig] = None,
    connect_timeout: float = 10.0,
) -> socket.socket:
    """Open a connected TCP (or TLS, if ``tls`` given) socket to a TAK port."""
    raw = socket.create_connection((host, port), timeout=connect_timeout)
    if tls is None:
        return raw
    try:
        context = tls.build_context(host)
        return context.wrap_socket(raw, server_hostname=host)
    except Exception:
        raw.close()
        raise


class TakClient:
    """Reconnecting TAK client: registers via SA, keeps alive, delivers events.

    Usage::

        def on_event(ev):
            print(ev.uid, ev.event_type, ev.lat, ev.lon)

        client = TakClient("tak.example.org", 8089, tls=TlsConfig(...),
                           on_event=on_event)
        client.start()          # background thread
        ...
        client.send(xml)        # non-blocking from any thread
        client.stop()

    Or ``client.run_forever()`` to own the calling thread.

    ``send()`` enqueues (bounded queue, oldest dropped on overflow — see
    ``outbox_dropped``); the client thread delivers between read ticks,
    including right after (re)connecting, so sending before the
    connection is up or across a server restart is safe.

    Callbacks run on the client thread; exceptions they raise are
    logged (``cotkit.client`` logger) and do not stop the client.
    ``on_raw`` is a wire tap: it receives every complete inbound event
    document, including the client's own SA reflected back by the
    server. ``on_event`` receives parsed events with own-SA filtered.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        tls: Optional[TlsConfig] = None,
        identity: Optional[SaIdentity] = None,
        on_event: Optional[Callable[[CotEvent], None]] = None,
        on_raw: Optional[Callable[[str], None]] = None,
        on_connect: Optional[Callable[[], None]] = None,
        on_disconnect: Optional[Callable[[Exception], None]] = None,
        keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL,
        read_timeout: float = DEFAULT_READ_TICK,
        reconnect: bool = True,
        backoff: Optional[Backoff] = None,
        separator: bytes = b"\n",
        max_buffer: Optional[int] = None,
        outbox_limit: int = DEFAULT_OUTBOX_LIMIT,
    ):
        self.host = host
        self.port = port
        self.tls = tls
        self.identity = identity or SaIdentity()
        self.on_event = on_event
        self.on_raw = on_raw
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.keepalive_interval = keepalive_interval
        # Read tick: recv timeout AND the outbound flush cadence.
        self.read_timeout = read_timeout
        self.reconnect = reconnect
        self.backoff = backoff or Backoff()
        self.separator = separator

        self._parser_kwargs = {} if max_buffer is None else {"max_buffer": max_buffer}
        self._outbox = collections.deque(maxlen=max(1, outbox_limit))
        self.outbox_dropped = 0
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._lifecycle_lock = threading.Lock()
        self._running = True  # supports bare run_forever(); start()/stop() manage it
        self.connected = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "TakClient":
        """Run the client on a background daemon thread."""
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return self
            self._running = True
            self._thread = threading.Thread(
                target=self.run_forever, daemon=True,
                name=f"TakClient({self.host}:{self.port})",
            )
            self._thread.start()
        return self

    def stop(self, join_timeout: float = 10.0) -> None:
        """Stop the loop and unblock any pending recv. Safe to call from
        callbacks (skips joining the client's own thread)."""
        self._running = False
        self._shutdown_socket()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=join_timeout)

    def __enter__(self) -> "TakClient":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- outbound ----------------------------------------------------------

    def send(self, xml: Union[str, bytes]) -> None:
        """Queue one event document for delivery. Non-blocking, callable
        from any thread, works before connect and across reconnects.
        On queue overflow the oldest entry is evicted (``outbox_dropped``
        counts evictions) — for SA/telemetry flows, fresh state wins."""
        payload = xml.encode("utf-8") if isinstance(xml, str) else bytes(xml)
        if len(self._outbox) == self._outbox.maxlen:
            self.outbox_dropped += 1
        self._outbox.append(payload)

    def _flush_outbox(self) -> None:
        while self._outbox:
            payload = self._outbox.popleft()
            try:
                self._sock.sendall(payload + self.separator)
            except OSError:
                self._outbox.appendleft(payload)  # keep for after reconnect
                raise

    # -- main loop ---------------------------------------------------------

    def run_forever(self) -> None:
        """Connect / serve / reconnect until ``stop()`` (or first failure
        when ``reconnect=False``)."""
        while self._running:
            try:
                self._connect_once()
                self.backoff.reset()
                self._serve_connection()
            except OSError as exc:
                # Suppress the synthetic error produced by stop() itself.
                if self._running and self.on_disconnect:
                    self._safe_callback(self.on_disconnect, exc)
            finally:
                self._close_socket()

            if not self._running or not self.reconnect:
                break
            self._sleep_interruptible(self.backoff.next_delay())

    def _connect_once(self) -> None:
        sock = open_tak_socket(self.host, self.port, self.tls)
        sock.settimeout(self.read_timeout)
        self._sock = sock
        if not self._running:  # stop() raced the connect
            raise OSError("stopped during connect")
        self.connected = True
        sock.sendall(self.identity.sa_xml().encode("utf-8") + self.separator)
        if self.on_connect:
            self._safe_callback(self.on_connect)

    def _serve_connection(self) -> None:
        parser = CotStreamParser(**self._parser_kwargs)
        last_sa = time.monotonic()
        got_foreign_event = False
        startup_retries = 0

        while self._running:
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                data = None
            if data == b"":
                raise OSError("connection closed by server")

            if data:
                for xml in parser.feed(data):
                    if self.on_raw:
                        self._safe_callback(self.on_raw, xml)
                    event = parse_event(xml)
                    if event is None:
                        continue
                    if event.uid == self.identity.uid:
                        continue  # our own SA reflected back
                    got_foreign_event = True
                    if self.on_event:
                        self._safe_callback(self.on_event, event)

            self._flush_outbox()

            # Keepalive: fast SA retries until the server sends us anything,
            # then the long steady-state interval.
            elapsed = time.monotonic() - last_sa
            if (not got_foreign_event and startup_retries < STARTUP_SA_RETRIES
                    and elapsed >= STARTUP_SA_INTERVAL):
                self._sock.sendall(self.identity.sa_xml().encode("utf-8") + self.separator)
                last_sa = time.monotonic()
                startup_retries += 1
            elif elapsed >= self.keepalive_interval:
                self._sock.sendall(self.identity.sa_xml().encode("utf-8") + self.separator)
                last_sa = time.monotonic()

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _safe_callback(fn, *args) -> None:
        try:
            fn(*args)
        except Exception:
            _LOG.exception("cotkit callback %r raised; client continues", fn)

    def _sleep_interruptible(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def _shutdown_socket(self) -> None:
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _close_socket(self) -> None:
        sock, self._sock = self._sock, None
        self.connected = False
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


class TakSender:
    """Lazy outbound-only sender: connects on first send, retries once.

    Before each write on an existing connection, inbound broadcast
    traffic is drained (a TAK server sends its feed to every client)
    and a received FIN is detected, so a server restart costs one
    reconnect, not a silently lost batch. ``drop_on_failure=True``
    gives fire-and-forget semantics; the default re-raises so callers
    know delivery failed. Thread-safe. See the module docstring for the
    whole-batch retry caveat.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        tls: Optional[TlsConfig] = None,
        drop_on_failure: bool = False,
        separator: bytes = b"\n",
        connect_timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.tls = tls
        self.drop_on_failure = drop_on_failure
        self.separator = separator
        self.connect_timeout = connect_timeout
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()

    def send(self, events: Union[str, bytes, Iterable[Union[str, bytes]]]) -> int:
        """Send one event or an iterable of events. Returns count sent
        (0 on dropped failure when ``drop_on_failure``)."""
        if isinstance(events, (str, bytes)):
            events = [events]
        payloads = [e.encode("utf-8") if isinstance(e, str) else bytes(e) for e in events]
        if not payloads:
            return 0
        with self._lock:
            try:
                self._write(payloads)
            except OSError:
                self._close_locked()
                try:
                    self._write(payloads)  # one reconnect attempt
                except OSError:
                    self._close_locked()
                    if self.drop_on_failure:
                        return 0
                    raise
        return len(payloads)

    def _write(self, payloads) -> None:
        if self._sock is not None and _drain_inbound(self._sock):
            self._close_locked()
        if self._sock is None:
            self._sock = open_tak_socket(self.host, self.port, self.tls, self.connect_timeout)
        for payload in payloads:
            self._sock.sendall(payload + self.separator)

    def close(self) -> None:
        """Close the cached connection (reopens lazily on the next send)."""
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self) -> "TakSender":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
