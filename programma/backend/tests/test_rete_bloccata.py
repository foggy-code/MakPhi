"""Blocco della rete nei test (fixture `_no_real_network` di conftest.py): senza il marcatore `network` nessun test
esce dal computer. Le connessioni verso host non locali falliscono subito con un messaggio che spiega cosa fare;
MockTransport, TestClient e i server su 127.0.0.1 funzionano come prima. I test verso i servizi veri (test_remote.py)
hanno il marcatore e restano esclusi di default.
"""

from __future__ import annotations

import http.server
import socket
import threading

import httpx
import pytest

from . import test_remote
from .conftest import NETWORK_HELP, is_local_host


def test_real_connections_fail_at_once_with_a_clear_message(_no_real_network):
    guard = _no_real_network
    with pytest.raises(httpx.ConnectError, match="Connessione di rete reale bloccata") as err:
        httpx.get("https://rest.uniprot.org/uniprotkb/P69905.json")
    assert "rest.uniprot.org" in str(err.value) and NETWORK_HELP in str(err.value)
    with pytest.raises(socket.gaierror, match="risoluzione del nome files.rcsb.org"):
        socket.getaddrinfo("files.rcsb.org", 443)
    with pytest.raises(ConnectionRefusedError, match="socket verso 192.0.2.1:443"):
        socket.create_connection(("192.0.2.1", 443), timeout=1)  # TEST-NET-1: indirizzo di documentazione
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(ConnectionRefusedError, match="socket verso 198.51.100.7:25"):
            sock.connect_ex(("198.51.100.7", 25))  # TEST-NET-2
    assert len(guard.attempts) == 4
    guard.attempts.clear()  # tentativi voluti: altrimenti il test fallirebbe alla chiusura, come deve un test vero


def test_mock_transports_and_local_servers_still_work():
    mock = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=request.url.host)))
    assert mock.get("https://rest.uniprot.org/x").text == "rest.uniprot.org"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (nome imposto da http.server)
            body = b"locale"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        assert httpx.get(f"http://127.0.0.1:{port}/").text == "locale"
        assert httpx.get(f"http://localhost:{port}/").text == "locale"  # anche per nome
        assert socket.getaddrinfo("localhost", port)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("host, local", [
    ("127.0.0.1", True), ("127.0.1.1", True), ("::1", True), ("[::1]", True), ("::ffff:127.0.0.1", True),
    ("0.0.0.0", True), ("localhost", True), ("LOCALHOST.", True), ("makphi.localhost", True), (None, True),
    (b"localhost", True), ("", True),
    ("192.168.1.10", False), ("10.0.0.1", False), ("8.8.8.8", False), ("2001:db8::1", False),
    ("rest.uniprot.org", False), ("qmean", False), ("localhost.example.org", False),
])
def test_only_this_computer_counts_as_local(host, local):
    assert is_local_host(host) is local


def test_network_marker_is_declared_and_excluded_by_default(pytestconfig):
    assert any(line.startswith("network:") for line in pytestconfig.getini("markers"))
    assert "not network" in pytestconfig.getini("addopts")  # pyproject.toml: addopts = "-m 'not network'"
    assert test_remote.pytestmark.name == "network"  # i test verso i servizi veri hanno il marcatore


@pytest.mark.network
def test_network_marked_tests_are_not_blocked():
    """Gira solo con `pytest -m network`: lì il blocco non c'è."""
    assert "connect" not in vars(socket.socket) and "handle_request" in vars(httpx.HTTPTransport)
