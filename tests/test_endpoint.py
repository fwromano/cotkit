"""Server-neutral TAK endpoint construction and client wiring."""

from dataclasses import FrozenInstanceError

import pytest

from cotkit import TakClient, TakEndpoint, TakSender, TlsConfig
from cotkit.build import build_event
from cotkit.testing import FakeTakServer, wait_for


def test_tcp_url_defaults_and_canonical_form():
    endpoint = TakEndpoint.from_url("tcp://tak-edge")

    assert endpoint.host == "tak-edge"
    assert endpoint.port == 8088
    assert endpoint.tls is None
    assert endpoint.scheme == "tcp"
    assert endpoint.url == "tcp://tak-edge:8088"


def test_tls_url_defaults_and_preserves_config():
    tls = TlsConfig(
        client_cert="bridge.pem",
        client_key="bridge.key",
        ca_cert="ca.pem",
    )
    endpoint = TakEndpoint.from_url("tls://tak.example.org", tls=tls)

    assert endpoint.host == "tak.example.org"
    assert endpoint.port == 8089
    assert endpoint.tls is tls
    assert endpoint.scheme == "tls"
    assert endpoint.url == "tls://tak.example.org:8089"


def test_tls_url_creates_default_tls_policy():
    endpoint = TakEndpoint.from_url("tls://127.0.0.1:9443")

    assert isinstance(endpoint.tls, TlsConfig)
    assert endpoint.url == "tls://127.0.0.1:9443"


def test_named_constructors_and_ipv6_url():
    plain = TakEndpoint.for_tcp(" 127.0.0.1 ", 9000)
    secure = TakEndpoint.for_tls("::1")

    assert plain == TakEndpoint("127.0.0.1", 9000)
    assert secure.port == 8089
    assert secure.url == "tls://[::1]:8089"
    assert TakEndpoint.from_url(secure.url).host == "::1"


def test_endpoint_is_immutable():
    endpoint = TakEndpoint.for_tcp("127.0.0.1")

    with pytest.raises(FrozenInstanceError):
        endpoint.host = "other"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://tak.example.org:8089",
        "tcp://",
        "tcp://user@tak.example.org:8088",
        "tcp://tak.example.org:8088/path",
        "tcp://tak.example.org:8088?x=1",
        "tcp://tak.example.org:8088#fragment",
        "tcp://tak.example.org:0",
        "tcp://tak.example.org:not-a-port",
        "tcp://tak.example.org:70000",
    ],
)
def test_invalid_endpoint_urls_are_rejected(url):
    with pytest.raises((TypeError, ValueError)):
        TakEndpoint.from_url(url)


def test_tcp_url_rejects_tls_policy():
    with pytest.raises(ValueError, match="tcp"):
        TakEndpoint.from_url("tcp://tak.example.org", tls=TlsConfig())


def test_direct_endpoint_validates_host_and_port():
    with pytest.raises(ValueError, match="host"):
        TakEndpoint("", 8088)
    with pytest.raises(TypeError, match="integer"):
        TakEndpoint("tak.example.org", "8088")
    with pytest.raises(ValueError, match="between"):
        TakEndpoint("tak.example.org", 0)


def test_client_and_sender_share_endpoint():
    endpoint = TakEndpoint.for_tcp("127.0.0.1", 9000)
    client = TakClient(endpoint)
    sender = TakSender(endpoint)

    assert client.endpoint is endpoint
    assert sender.endpoint is endpoint
    assert (client.host, client.port, client.tls) == ("127.0.0.1", 9000, None)
    assert (sender.host, sender.port, sender.tls) == ("127.0.0.1", 9000, None)


def test_endpoint_rejects_conflicting_legacy_arguments():
    endpoint = TakEndpoint.for_tcp("127.0.0.1")

    with pytest.raises(TypeError, match="port"):
        TakClient(endpoint, 8088)
    with pytest.raises(TypeError, match="tls"):
        TakSender(endpoint, tls=TlsConfig())


def test_legacy_host_port_api_remains_supported():
    tls = TlsConfig(insecure=True)
    client = TakClient("127.0.0.1", 8089, tls=tls)
    sender = TakSender("127.0.0.1", 8089, tls=tls)

    assert client.endpoint == TakEndpoint("127.0.0.1", 8089, tls)
    assert sender.endpoint == TakEndpoint("127.0.0.1", 8089, tls)


def test_tcp_endpoint_roundtrip():
    with FakeTakServer() as server:
        endpoint = TakEndpoint.for_tcp(server.host, server.port)
        with TakSender(endpoint) as sender:
            sender.send(build_event("endpoint", "a-f-G", 1.0, 2.0))
            assert wait_for(
                lambda: any('uid="endpoint"' in xml for xml in server.received)
            )
