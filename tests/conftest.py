"""Faux serveur HTTP local pour tester le réseau sans internet.

Le serveur est écrit avec des sockets bruts, comme le client : on voit
exactement les octets échangés, ce qui est l'objectif pédagogique.
"""

import shutil
import socket
import ssl
import subprocess
import threading

import pytest


def make_response(status=200, reason="OK", headers=None, body=b""):
    """Fabrique les octets d'une réponse HTTP (Content-Length ajouté par défaut)."""
    headers = dict(headers or {})
    if isinstance(body, str):
        body = body.encode()
    headers.setdefault("Content-Length", str(len(body)))
    head = f"HTTP/1.1 {status} {reason}\r\n"
    head += "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    return head.encode() + b"\r\n" + body


def request_path(request_text):
    """'GET /a?b=1 HTTP/1.1\\r\\n...' -> '/a?b=1'"""
    return request_text.split(" ")[1]


class FakeServer:
    def __init__(self, handler, ssl_context=None):
        self.handler = handler  # str (requête brute) -> bytes (réponse brute)
        self.ssl_context = ssl_context
        self.requests = []  # toutes les requêtes reçues, pour les assertions
        self._stop = threading.Event()
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))  # port 0 : l'OS choisit un port libre
        self._listener.listen()
        self._listener.settimeout(0.1)  # pour pouvoir s'arrêter proprement
        self.port = self._listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def url(self, path="/", scheme="http"):
        # "localhost" (et non 127.0.0.1) : le certificat de test est émis pour ce nom.
        return f"{scheme}://localhost:{self.port}{path}"

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self._handle(conn)

    def _handle(self, conn):
        try:
            if self.ssl_context:
                conn = self.ssl_context.wrap_socket(conn, server_side=True)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            request = data.decode("iso-8859-1")
            self.requests.append(request)
            conn.sendall(self.handler(request))
        except OSError:
            pass  # ex. client qui refuse notre certificat : le test le vérifiera côté client
        finally:
            conn.close()

    def close(self):
        self._stop.set()
        self._listener.close()


@pytest.fixture
def serve():
    servers = []

    def _serve(handler, ssl_context=None):
        server = FakeServer(handler, ssl_context)
        servers.append(server)
        return server

    yield _serve
    for server in servers:
        server.close()


@pytest.fixture(scope="session")
def tls_contexts(tmp_path_factory):
    """(contexte serveur, contexte client) avec un certificat auto-signé pour localhost."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl introuvable : impossible de générer un certificat de test")
    d = tmp_path_factory.mktemp("certs")
    cert, key = d / "cert.pem", d / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
        check=True, capture_output=True,
    )
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)
    # Le client fait confiance UNIQUEMENT à ce certificat : la vérification reste active.
    client_ctx = ssl.create_default_context(cafile=str(cert))
    return server_ctx, client_ctx


@pytest.fixture
def constant_specificity(monkeypatch):
    """Remplace `specificity` par une valeur constante : seul l'ORDRE des règles compte.

    Les tests des étapes 4-5 (layout, peinture, onglet) n'ont pas à dépendre de la fonction
    que tu écris à l'étape 3 ; on l'active donc explicitement, fichier par fichier
    (`pytestmark = pytest.mark.usefixtures("constant_specificity")`), jamais globalement :
    les tests de specificity.py doivent, eux, tester la vraie.
    """
    import browser.css
    monkeypatch.setattr(browser.css, "specificity", lambda selector: (0, 0, 0))
