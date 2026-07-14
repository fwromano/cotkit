"""CotListener: accept-side ingest wired to the shared framing."""

from cotkit.build import build_event
from cotkit.client import TakSender
from cotkit.listener import CotListener
from cotkit.testing import wait_for


def test_listener_receives_parsed_events_from_sender():
    events = []
    with CotListener("127.0.0.1", 0,
                     on_event=lambda ev, peer: events.append((ev, peer))) as listener:
        with TakSender("127.0.0.1", listener.port) as sender:
            sender.send([
                build_event("in-1", "a-f-G", 30.0, -96.0, callsign="ONE"),
                build_event("in-2", "u-d-c", 30.1, -96.1),
            ])
            assert wait_for(lambda: len(events) == 2)
    uids = {ev.uid for ev, _ in events}
    assert uids == {"in-1", "in-2"}
    assert all(peer[0] == "127.0.0.1" for _, peer in events)


def test_listener_on_raw_and_multiple_connections():
    raw = []
    with CotListener("127.0.0.1", 0, on_raw=lambda x, peer: raw.append(x)) as listener:
        with TakSender("127.0.0.1", listener.port) as s1, \
                TakSender("127.0.0.1", listener.port) as s2:
            s1.send(build_event("a", "a-f-G", 0.0, 0.0))
            s2.send(build_event("b", "a-f-G", 0.0, 0.0))
            assert wait_for(lambda: len(raw) == 2)


def test_listener_survives_garbage_bytes():
    events = []
    with CotListener("127.0.0.1", 0,
                     on_event=lambda ev, peer: events.append(ev)) as listener:
        import socket
        with socket.create_connection(("127.0.0.1", listener.port)) as sock:
            sock.sendall(b"\xff\x00 not xml at all ")
            sock.sendall(build_event("ok", "a-f-G", 1.0, 1.0).encode())
            assert wait_for(lambda: len(events) == 1)
        assert events[0].uid == "ok"


def test_listener_callback_exception_does_not_drop_connection():
    events = []

    def flaky(ev, peer):
        if ev.uid == "bomb":
            raise RuntimeError("boom")
        events.append(ev)

    with CotListener("127.0.0.1", 0, on_event=flaky) as listener:
        with TakSender("127.0.0.1", listener.port) as sender:
            sender.send(build_event("bomb", "a-f-G", 0.0, 0.0))
            sender.send(build_event("survivor", "a-f-G", 0.0, 0.0))
            assert wait_for(lambda: len(events) == 1)
            assert events[0].uid == "survivor"


def test_listener_stop_closes_established_connections():
    import socket
    listener = CotListener("127.0.0.1", 0).start()
    sock = socket.create_connection(("127.0.0.1", listener.port))
    sock.settimeout(3)
    listener.stop()
    try:
        assert sock.recv(1) == b""  # EOF — peer sees the close promptly
    except ConnectionResetError:
        pass  # RST is an equally prompt close signal
    sock.close()


def test_listener_stop_releases_port():
    listener = CotListener("127.0.0.1", 0).start()
    port = listener.port
    listener.stop()
    relisten = CotListener("127.0.0.1", port).start()
    relisten.stop()
