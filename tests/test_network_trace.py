"""Tests de la version "trace" : elle doit (1) afficher chaque étape, (2) se comporter comme network.py."""

import re

import pytest

from browser import network, network_trace
from browser.network_trace import Cache, NetworkError, TooManyRedirects, fetch
from conftest import make_response, request_path


def labels(output):
    """Liste ordonnée des étapes affichées : ['CACHE', 'HOP', 'URL', ...] (hors lignes de détail)."""
    return [m.group(1) for m in re.finditer(r"^\[\d+\] \+\s*[\d.]+ ms  (\S+)", output, re.MULTILINE)]


def test_http_request_shows_every_step_in_order(serve, capsys):
    server = serve(lambda req: make_response(headers={"X-Test": "yes"}, body="hello"))
    fetch(server.url("/x"), cache=Cache())
    out = capsys.readouterr().out

    # On fusionne les étapes consécutives identiques (PARSE s'affiche plusieurs fois d'affilée).
    steps = [s for i, s in enumerate(labels(out)) if i == 0 or s != labels(out)[i - 1]]
    assert steps == ["CACHE", "HOP", "URL", "REQUEST", "DNS+TCP", "TLS", "SEND", "RECV",
                     "PARSE", "HOP", "CACHE", "DONE"]

    assert "MISS" in out                          # cache consulté, vide
    assert "GET /x HTTP/1.1\\r\\n" in out          # la requête brute, avec les \r\n visibles
    assert "Connection: close\\r\\n" in out
    assert "x-test = yes" in out                  # en-têtes parsés
    assert "Content-Length: 5" in out             # stratégie de fin de corps
    assert "NON stocké" in out                    # pas de Cache-Control → pas de cache
    assert "ignoré (schéma http" in out          # pas de TLS en http


def test_every_step_line_is_numbered_and_timed(serve, capsys):
    server = serve(lambda req: make_response(body="x"))
    fetch(server.url(), cache=None)
    out = capsys.readouterr().out
    numbers = [int(n) for n in re.findall(r"^\[(\d+)\] \+", out, re.MULTILINE)]
    assert numbers == list(range(1, len(numbers) + 1))  # 1, 2, 3… sans trou


def test_chunked_is_reported(serve, capsys):
    raw = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nHello\r\n7\r\n, world\r\n0\r\n\r\n"
    server = serve(lambda req: raw)
    r = fetch(server.url(), cache=None)
    out = capsys.readouterr().out
    assert r.body == b"Hello, world"
    assert "Transfer-Encoding: chunked" in out
    assert "2 chunk(s) de [5, 7] octets" in out


def test_https_shows_tls_and_certificate(serve, tls_contexts, capsys):
    server_ctx, client_ctx = tls_contexts
    server = serve(lambda req: make_response(body="secure"), ssl_context=server_ctx)
    fetch(server.url("/", scheme="https"), cache=None, ssl_context=client_ctx)
    out = capsys.readouterr().out
    assert re.search(r"handshake terminé .*TLSv1\.[23]", out)
    assert "sujet=localhost" in out
    assert "SNI envoyé : localhost" in out


def test_redirect_steps_are_shown(serve, capsys):
    def handler(req):
        if request_path(req) == "/old":
            return make_response(301, "Moved Permanently", {"Location": "/new"})
        return make_response(body="final")
    server = serve(handler)
    r = fetch(server.url("/old"), cache=None)
    out = capsys.readouterr().out
    assert r.text == "final"
    assert "requête n°1" in out and "requête n°2" in out
    assert "Location: '/new'" in out
    assert f"= '{server.url('/new')}'" in out     # le résultat d'urljoin
    assert "redirection 1/5" in out
    assert out.count("TCP") >= 2                  # une connexion par saut


def test_redirect_limit_still_enforced_and_reported(serve, capsys):
    server = serve(lambda req: make_response(302, "Found", {"Location": "/"}))
    with pytest.raises(TooManyRedirects):
        fetch(server.url(), cache=None)
    assert "limite atteinte" in capsys.readouterr().out
    assert len(server.requests) == 6


def test_cache_miss_then_hit_shows_no_network_on_hit(serve, capsys):
    server = serve(lambda req: make_response(headers={"Cache-Control": "max-age=60"}, body="v1"))
    cache = Cache()
    fetch(server.url(), cache=cache)
    first = capsys.readouterr().out
    fetch(server.url(), cache=cache)
    second = capsys.readouterr().out

    assert "MISS" in first and "STOCKÉ pour 60 s" in first
    assert "HIT" in second and "AUCUN accès réseau" in second
    assert "DNS+TCP" not in second and "SEND" not in second   # le réseau n'est pas touché
    assert len(server.requests) == 1


def test_cache_expiry_is_reported(serve, capsys):
    now = [0]
    server = serve(lambda req: make_response(headers={"Cache-Control": "max-age=10"}))
    cache = Cache(clock=lambda: now[0])
    fetch(server.url(), cache=cache)
    now[0] = 25
    fetch(server.url(), cache=cache)
    assert "PÉRIMÉ depuis 15 s" in capsys.readouterr().out
    assert len(server.requests) == 2


def test_network_errors_are_reported_and_raised(capsys):
    with pytest.raises(NetworkError):
        fetch("http://localhost:1/", cache=None)
    assert "ERROR" in capsys.readouterr().out


# ---- la copie doit rester fidèle à l'original --------------------------------

@pytest.mark.parametrize("make_handler", [
    lambda: (lambda req: make_response(200, "OK", {"Content-Type": "text/html; charset=utf-8"}, "héllo")),
    lambda: (lambda req: make_response(404, "Not Found", body="nope")),
    lambda: (lambda req: b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n"),
    lambda: (lambda req: b"HTTP/1.1 200 OK\r\n\r\nuntil close"),
])
def test_same_result_as_network_py(serve, make_handler, capsys):
    server = serve(make_handler())
    original = network.fetch(server.url(), cache=None)
    traced = network_trace.fetch(server.url(), cache=None)
    assert (traced.status, traced.reason, traced.headers, traced.body) == \
           (original.status, original.reason, original.headers, original.body)
    # Et les deux envoient exactement la même requête.
    assert server.requests[0] == server.requests[1]


def test_same_request_bytes_as_network_py():
    url = "http://example.org:8080/p?q=1"
    assert network_trace.build_request(network_trace.parse_url(url)) == \
           network.build_request(network.parse_url(url))
