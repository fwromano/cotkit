"""UdpCotListener: one CoT event per datagram with field wrappers tolerated."""

import socket

from cotkit.build import build_event
from cotkit.testing import wait_for
from cotkit.udp import UdpCotListener, sanitize_cot_datagram


def send_udp(port, payload):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(payload, ("127.0.0.1", port))


def test_sanitize_accepts_wrapped_xml_and_rejects_probes():
    assert sanitize_cot_datagram(b"") is None
    assert sanitize_cot_datagram(b" hello\r\n") is None
    assert sanitize_cot_datagram(b"not xml") is None
    assert sanitize_cot_datagram(b"prefix:\x00 <event uid='x'/>\x00\r\n") == "<event uid='x'/>"
    assert sanitize_cot_datagram(b"<event uid='\xff'/>") is None
    assert sanitize_cot_datagram("<event uid='utf16'/>​".encode("utf-16")) == (
        "<event uid='utf16'/>"
    )


def test_listener_receives_original_xml_and_typed_event():
    seen = []
    raw = []
    xml = build_event(
        "heli-1",
        "a-f-A-M-H",
        43.615,
        -116.2023,
        callsign="Firehawk 1",
        group_name="Red",
        group_role="Team Member",
    )
    with UdpCotListener(
        "127.0.0.1",
        0,
        on_event=lambda event, peer: seen.append((event, peer)),
        on_raw=lambda event_xml, peer: raw.append(event_xml),
    ) as listener:
        assert listener.actual_socket_buffer > 0
        send_udp(listener.port, b"transport-prefix " + xml.encode() + b"\x00\n")
        assert wait_for(lambda: len(seen) == 1)

    event, peer = seen[0]
    assert event.uid == "heli-1"
    assert event.callsign == "Firehawk 1"
    assert event.group_name == "Red"
    assert event.group_role == "Team Member"
    assert peer[0] == "127.0.0.1"
    assert raw == [xml]
    assert listener.datagrams_received == 1
    assert listener.events_received == 1
    assert listener.invalid_datagrams == 0


def test_listener_rejects_invalid_events_and_survives_callback_error():
    seen = []

    def flaky(event, peer):
        if event.uid == "bad-callback":
            raise RuntimeError("boom")
        seen.append(event.uid)

    with UdpCotListener("127.0.0.1", 0, on_event=flaky) as listener:
        send_udp(listener.port, b"hello")
        send_udp(listener.port, b"<not-an-event/>")
        send_udp(listener.port, build_event("bad-callback", "a-f-G", 1, 1).encode())
        send_udp(listener.port, build_event("survivor", "a-f-G", 1, 1).encode())
        assert wait_for(lambda: seen == ["survivor"])

    assert listener.datagrams_received == 4
    assert listener.invalid_datagrams == 2
    assert listener.callback_errors == 1


def test_listener_stop_releases_port():
    listener = UdpCotListener("127.0.0.1", 0).start()
    port = listener.port
    listener.stop()
    UdpCotListener("127.0.0.1", port).start().stop()


def test_listener_reports_effective_socket_buffer():
    requested = 256 * 1024
    with UdpCotListener(
        "127.0.0.1",
        0,
        socket_buffer=requested,
    ) as listener:
        assert listener._sock is not None
        assert listener.actual_socket_buffer == listener._sock.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_RCVBUF,
        )
        assert listener.actual_socket_buffer > 0
