"""OTS conveniences: port defaults follow the TLS choice."""

from cotkit.client import TlsConfig
from cotkit.ots import OTS_SSL_PORT, OTS_TCP_PORT, ots_client, ots_sender


def test_plaintext_defaults_to_8088():
    assert ots_client("h").port == OTS_TCP_PORT == 8088
    assert ots_sender("h").port == 8088


def test_tls_defaults_to_8089():
    tls = TlsConfig(insecure=True)
    assert ots_client("h", tls=tls).port == OTS_SSL_PORT == 8089
    assert ots_sender("h", tls=tls).port == 8089
    assert ots_client("h", tls=tls).tls is tls


def test_explicit_port_wins():
    assert ots_client("h", port=9999).port == 9999
    assert ots_sender("h", tls=TlsConfig(), port=1234).port == 1234


def test_kwargs_pass_through():
    client = ots_client("h", keepalive_interval=10.0)
    assert client.keepalive_interval == 10.0
