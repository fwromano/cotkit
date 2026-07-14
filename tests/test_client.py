"""TakClient / TakSender behavior against an in-process fake TAK server."""

import threading
import time

import pytest

from cotkit.build import build_event
from cotkit.client import Backoff, SaIdentity, TakClient, TakSender
from cotkit.model import parse_event
from cotkit.testing import FakeTakServer, wait_for


def fast_client(server, **kwargs):
    """Client tuned for test speed: quick ticks, near-instant reconnect."""
    kwargs.setdefault("identity", SaIdentity(uid="test-client", callsign="TEST"))
    kwargs.setdefault("read_timeout", 0.05)
    kwargs.setdefault("backoff", Backoff(initial=0.05, maximum=0.1, jitter=0.0))
    return TakClient(server.host, server.port, **kwargs)


def sa_events(server):
    return [x for x in server.received if 'uid="test-client"' in x]


def test_client_registers_with_sa_on_connect():
    with FakeTakServer() as server:
        with fast_client(server):
            assert wait_for(lambda: len(sa_events(server)) >= 1)
            ev = parse_event(sa_events(server)[0])
            assert ev.callsign == "TEST"
            assert "takv" in ev.detail_xml


def test_client_receives_broadcast_events():
    with FakeTakServer() as server:
        seen = []
        with fast_client(server, on_event=seen.append):
            assert wait_for(lambda: server.connection_count == 1)
            server.broadcast(build_event("other-1", "a-f-G", 30.0, -96.0, callsign="OTHER"))
            assert wait_for(lambda: len(seen) == 1)
            assert seen[0].uid == "other-1"
            assert seen[0].callsign == "OTHER"


def test_client_filters_own_sa_from_on_event_but_not_on_raw():
    with FakeTakServer() as server:
        seen, raw = [], []
        with fast_client(server, on_event=seen.append, on_raw=raw.append):
            assert wait_for(lambda: len(sa_events(server)) >= 1)
            server.broadcast(sa_events(server)[0])  # reflect its own SA back
            server.broadcast(build_event("real", "a-f-G", 1.0, 2.0))
            assert wait_for(lambda: len(seen) == 1)
            assert seen[0].uid == "real"
            # on_raw is a wire tap: it sees the reflection too
            assert wait_for(lambda: len(raw) == 2)


def test_client_send_reaches_server():
    with FakeTakServer() as server:
        with fast_client(server) as client:
            assert wait_for(lambda: server.connection_count == 1)
            client.send(build_event("from-client", "a-f-G", 3.0, 4.0))
            assert wait_for(
                lambda: any('uid="from-client"' in x for x in server.received))


def test_send_before_connect_is_buffered_and_delivered():
    """The quickstart pattern: start(); send() immediately. Must not raise
    and must arrive once connected."""
    with FakeTakServer() as server:
        client = fast_client(server)
        client.send(build_event("early-bird", "a-f-G", 1.0, 1.0))  # not even started
        client.start()
        client.send(build_event("early-bird-2", "a-f-G", 1.0, 1.0))
        try:
            assert wait_for(lambda: any('uid="early-bird"' in x for x in server.received))
            assert wait_for(lambda: any('uid="early-bird-2"' in x for x in server.received))
        finally:
            client.stop()


def test_client_reconnects_after_server_drop():
    with FakeTakServer() as server:
        connects = []
        with fast_client(server, on_connect=lambda: connects.append(1)):
            assert wait_for(lambda: len(connects) == 1)
            server.drop_all_connections()
            assert wait_for(lambda: len(connects) >= 2, timeout=10)
            assert wait_for(lambda: len(sa_events(server)) >= 2)


def test_client_no_reconnect_when_disabled():
    with FakeTakServer() as server:
        disconnects = []
        client = fast_client(server, reconnect=False,
                             on_disconnect=lambda e: disconnects.append(e))
        client.start()
        assert wait_for(lambda: server.connection_count == 1)
        server.drop_all_connections()
        assert wait_for(lambda: len(disconnects) == 1)
        assert wait_for(lambda: not client._thread.is_alive())
        client.stop()


def test_clean_stop_does_not_fire_on_disconnect():
    with FakeTakServer() as server:
        disconnects = []
        client = fast_client(server, on_disconnect=lambda e: disconnects.append(e))
        client.start()
        assert wait_for(lambda: server.connection_count == 1)
        client.stop()
        time.sleep(0.1)
        assert disconnects == []


def test_callback_exception_does_not_kill_client():
    """One bad on_event invocation must not stop ingestion (field recorders
    run for days)."""
    with FakeTakServer() as server:
        seen = []

        def flaky(ev):
            if ev.uid == "bomb":
                raise KeyError("boom")
            seen.append(ev)

        with fast_client(server, on_event=flaky) as client:
            assert wait_for(lambda: server.connection_count == 1)
            server.broadcast(build_event("bomb", "a-f-G", 0.0, 0.0))
            server.broadcast(build_event("survivor", "a-f-G", 0.0, 0.0))
            assert wait_for(lambda: len(seen) == 1)
            assert seen[0].uid == "survivor"
            assert client._thread.is_alive()


def test_stop_immediately_after_start_lands():
    """stop() racing the thread's first instructions must still win."""
    with FakeTakServer() as server:
        client = fast_client(server)
        client.start()
        client.stop()
        assert wait_for(lambda: not client._thread.is_alive(), timeout=5)


def test_stop_from_callback_does_not_deadlock():
    with FakeTakServer() as server:
        client = fast_client(server)
        client.on_event = lambda ev: client.stop()
        client.start()
        assert wait_for(lambda: server.connection_count == 1)
        server.broadcast(build_event("x", "a-f-G", 0.0, 0.0))
        assert wait_for(lambda: not client._thread.is_alive(), timeout=5)


def test_client_keepalive_cadence_bounded():
    """After steady state, SAs arrive at roughly keepalive_interval — the
    assertion bounds both a dead keepalive (0 extra) and a spamming one."""
    with FakeTakServer() as server:
        with fast_client(server, keepalive_interval=0.2) as client:
            assert wait_for(lambda: server.connection_count == 1)
            server.broadcast(build_event("x", "a-f-G", 0.0, 0.0))  # end startup phase
            assert wait_for(lambda: len(sa_events(server)) >= 2, timeout=10)
            before = len(sa_events(server))
            time.sleep(1.0)
            delta = len(sa_events(server)) - before
            # 1.0 s at 0.2 s interval ≈ 5; allow scheduling slop either way
            assert 2 <= delta <= 10, f"keepalive rate off: {delta} SAs in 1 s"


def test_client_stop_is_prompt():
    with FakeTakServer() as server:
        client = fast_client(server).start()
        assert wait_for(lambda: server.connection_count == 1)
        t0 = time.monotonic()
        client.stop()
        assert time.monotonic() - t0 < 5.0
        assert not client._thread.is_alive()


def test_outbox_overflow_drops_oldest_and_counts():
    client = TakClient("127.0.0.1", 1, outbox_limit=3)  # never started
    for i in range(5):
        client.send(f"<event uid='{i}'/>")
    assert client.outbox_dropped == 2
    assert len(client._outbox) == 3


# --------------------------------------------------------------------------
# TakSender
# --------------------------------------------------------------------------

def test_sender_lazy_connect_and_batch():
    with FakeTakServer() as server:
        with TakSender(server.host, server.port) as sender:
            n = sender.send([build_event(f"s-{i}", "a-f-G", 0.0, 0.0) for i in range(3)])
            assert n == 3
            assert wait_for(lambda: len(server.received) == 3)


def test_sender_recovers_after_drop():
    with FakeTakServer() as server:
        with TakSender(server.host, server.port) as sender:
            sender.send(build_event("a", "a-f-G", 0.0, 0.0))
            assert wait_for(lambda: len(server.received) == 1)
            server.drop_all_connections()
            assert wait_for(lambda: server.connection_count == 0)
            # FIN delivery to the sender's buffer is asynchronous; the
            # drain-probe catches it once present. Allow a short settle.
            delivered = False
            for attempt in range(5):
                sender.send(build_event(f"b{attempt}", "a-f-G", 0.0, 0.0))
                if wait_for(lambda: any(f'uid="b{attempt}"' in x
                                        for x in server.received), timeout=1.0):
                    delivered = True
                    break
            assert delivered


def test_sender_survives_server_chatter():
    """A real TAK port broadcasts at senders; the drain must discard it
    rather than let the receive buffer grow forever."""
    with FakeTakServer() as server:
        with TakSender(server.host, server.port) as sender:
            sender.send(build_event("first", "a-f-G", 0.0, 0.0))
            assert wait_for(lambda: server.connection_count == 1)
            for _ in range(50):
                server.broadcast(build_event("noise", "a-f-G", 1.0, 1.0))
            sender.send(build_event("second", "a-f-G", 0.0, 0.0))
            assert wait_for(lambda: any('uid="second"' in x for x in server.received))


def test_sender_drop_on_failure_swallows_dead_target():
    sender = TakSender("127.0.0.1", 1, drop_on_failure=True, connect_timeout=0.2)
    assert sender.send("<event/>") == 0


def test_sender_raises_by_default_on_dead_target():
    sender = TakSender("127.0.0.1", 1, connect_timeout=0.2)
    with pytest.raises(OSError):
        sender.send("<event/>")


def test_sender_thread_safety_smoke():
    with FakeTakServer() as server:
        with TakSender(server.host, server.port) as sender:
            threads = [threading.Thread(
                target=lambda i=i: sender.send(build_event(f"t-{i}", "a-f-G", 0.0, 0.0)))
                for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert wait_for(lambda: len(server.received) == 8)
