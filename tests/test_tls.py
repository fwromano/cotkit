"""TLS paths against a TLS-wrapped FakeTakServer.

Regression suite for the class of bug where the TLS path diverges from
the plain-TCP path (e.g. MSG_PEEK is illegal on SSLSocket). Requires
the ``openssl`` binary to mint a throwaway self-signed cert; skipped
where unavailable.
"""

import shutil
import ssl
import subprocess

import pytest

from cotkit.build import build_event
from cotkit.client import Backoff, SaIdentity, TakClient, TakSender, TlsConfig
from cotkit.testing import FakeTakServer, wait_for

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl binary not available")


@pytest.fixture(scope="module")
def server_ctx(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    key, cert = d / "key.pem", d / "cert.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-subj", "/CN=127.0.0.1"],
        check=True, capture_output=True,
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return ctx


def tls_config():
    # 127.0.0.1 is a local literal, so the local exemption applies; being
    # explicit keeps the test's intent readable.
    return TlsConfig(insecure=True)


def test_sender_multiple_sends_over_tls(server_ctx):
    """THE regression: second send runs the liveness probe on an
    SSLSocket, which must not raise."""
    with FakeTakServer(ssl_context=server_ctx) as server:
        with TakSender(server.host, server.port, tls=tls_config()) as sender:
            sender.send(build_event("tls-1", "a-f-G", 0.0, 0.0))
            sender.send(build_event("tls-2", "a-f-G", 0.0, 0.0))
            sender.send(build_event("tls-3", "a-f-G", 0.0, 0.0))
            assert wait_for(lambda: len(server.received) == 3)


def test_sender_tls_recovers_after_drop(server_ctx):
    with FakeTakServer(ssl_context=server_ctx) as server:
        with TakSender(server.host, server.port, tls=tls_config()) as sender:
            sender.send(build_event("a", "a-f-G", 0.0, 0.0))
            assert wait_for(lambda: len(server.received) == 1)
            server.drop_all_connections()
            assert wait_for(lambda: server.connection_count == 0)
            delivered = False
            for attempt in range(5):
                try:
                    sender.send(build_event(f"b{attempt}", "a-f-G", 0.0, 0.0))
                except OSError:
                    continue  # retry path may still see the dying socket
                if wait_for(lambda: any(f'uid="b{attempt}"' in x
                                        for x in server.received), timeout=1.0):
                    delivered = True
                    break
            assert delivered


def test_client_roundtrip_over_tls(server_ctx):
    with FakeTakServer(ssl_context=server_ctx) as server:
        seen = []
        client = TakClient(
            server.host, server.port, tls=tls_config(),
            identity=SaIdentity(uid="tls-client", callsign="TLS"),
            on_event=seen.append, read_timeout=0.05,
            backoff=Backoff(initial=0.05, maximum=0.1, jitter=0.0),
        )
        with client:
            assert wait_for(lambda: server.connection_count == 1)
            assert wait_for(
                lambda: any('uid="tls-client"' in x for x in server.received))
            server.broadcast(build_event("from-server", "a-f-G", 30.0, -96.0))
            assert wait_for(lambda: len(seen) == 1)
            assert seen[0].uid == "from-server"
            client.send(build_event("up", "a-f-G", 1.0, 1.0))
            assert wait_for(lambda: any('uid="up"' in x for x in server.received))
