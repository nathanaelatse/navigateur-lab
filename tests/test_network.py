import pytest

from browser.network import (
    Cache, NetworkError, Response, TooManyRedirects,
    build_request, decode_chunked, fetch, parse_url,
)
from conftest import make_response, request_path


# ---- 1a : URL, requête, parsing -------------------------------------------

def test_parse_url_defaults():
    assert parse_url("http://example.org").port == 80
    assert parse_url("https://example.org").port == 443
    assert parse_url("http://example.org").path == "/"


def test_parse_url_port_path_query_and_fragment():
    url = parse_url("http://example.org:8080/a/b?x=1#ancre")
    assert (url.host, url.port, url.path) == ("example.org", 8080, "/a/b?x=1")  # fragment non envoyé


@pytest.mark.parametrize("bad", ["ftp://example.org", "example.org", "http://"])
def test_parse_url_rejects_unsupported(bad):
    with pytest.raises(NetworkError):
        parse_url(bad)


def test_build_request_is_handwritten_http11():
    raw = build_request(parse_url("http://example.org/page?q=1")).decode()
    lines = raw.split("\r\n")
    assert lines[0] == "GET /page?q=1 HTTP/1.1"
    assert "Host: example.org" in lines
    assert "Connection: close" in lines
    assert any(line.startswith("User-Agent: ") for line in lines)
    assert raw.endswith("\r\n\r\n")  # ligne vide = fin des en-têtes


def test_host_header_includes_non_default_port_only():
    assert "Host: example.org:8080\r\n" in build_request(parse_url("http://example.org:8080/")).decode()
    assert "Host: example.org\r\n" in build_request(parse_url("https://example.org:443/")).decode()


def test_fetch_sends_expected_request_and_parses_response(serve):
    server = serve(lambda req: make_response(
        200, "OK", {"Content-Type": "text/html; charset=utf-8", "X-Test": "yes"}, "héllo"))
    r = fetch(server.url("/hello"), cache=None)
    assert (r.status, r.reason, r.text) == (200, "OK", "héllo")
    assert r.headers["x-test"] == "yes"
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    sent = server.requests[0]
    assert sent.startswith("GET /hello HTTP/1.1\r\n")
    assert f"Host: localhost:{server.port}\r\n" in sent
    assert "Connection: close\r\n" in sent


def test_headers_are_case_insensitive_via_lowercase_keys(serve):
    server = serve(lambda req: make_response(headers={"CONTENT-TYPE": "text/plain"}))
    assert fetch(server.url(), cache=None).headers["content-type"] == "text/plain"


def test_http_error_status_is_returned_not_raised(serve):
    server = serve(lambda req: make_response(404, "Not Found", body="nope"))
    r = fetch(server.url(), cache=None)
    assert (r.status, r.reason) == (404, "Not Found")


def test_body_until_connection_close_when_no_content_length(serve):
    server = serve(lambda req: b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nbody to the end")
    assert fetch(server.url(), cache=None).body == b"body to the end"


def test_chunked_body(serve):
    raw = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
           b"5\r\nHello\r\n7\r\n, world\r\n0\r\n\r\n")
    server = serve(lambda req: raw)
    assert fetch(server.url(), cache=None).body == b"Hello, world"


def test_decode_chunked_hex_sizes_and_extensions():
    assert decode_chunked(b"A;ext=1\r\n0123456789\r\n0\r\n\r\n") == b"0123456789"


def test_truncated_response_raises(serve):
    server = serve(lambda req: b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nshort")
    with pytest.raises(NetworkError):
        fetch(server.url(), cache=None)


def test_garbage_response_raises(serve):
    server = serve(lambda req: b"not http at all")
    with pytest.raises(NetworkError):
        fetch(server.url(), cache=None)


def test_connection_refused_raises_network_error():
    with pytest.raises(NetworkError):
        fetch("http://localhost:1/", cache=None)  # le port 1 est (quasi) toujours fermé


def test_response_text_falls_back_to_utf8_and_handles_bad_charset():
    assert Response("u", 200, "OK", {}, "é".encode()).text == "é"
    assert Response("u", 200, "OK", {"content-type": "text/html; charset=nope"}, b"ok").text == "ok"
    latin = Response("u", 200, "OK", {"content-type": "text/html; charset=iso-8859-1"}, "é".encode("latin-1"))
    assert latin.text == "é"


# ---- 1b : HTTPS -------------------------------------------------------------

def test_https_fetch_with_trusted_certificate(serve, tls_contexts):
    server_ctx, client_ctx = tls_contexts
    server = serve(lambda req: make_response(body="secure"), ssl_context=server_ctx)
    r = fetch(server.url("/", scheme="https"), cache=None, ssl_context=client_ctx)
    assert r.status == 200 and r.text == "secure"


def test_https_rejects_untrusted_certificate(serve, tls_contexts):
    server_ctx, _ = tls_contexts
    server = serve(lambda req: make_response(body="secure"), ssl_context=server_ctx)
    # Pas de ssl_context fourni : le contexte par défaut ne connaît pas notre certificat.
    with pytest.raises(NetworkError, match="CERTIFICATE_VERIFY_FAILED"):
        fetch(server.url("/", scheme="https"), cache=None)


# ---- 1c : redirections -------------------------------------------------------

def redirecting_handler(hops):
    """/0 -> /1 -> ... -> /hops (qui répond 200)."""
    def handler(req):
        n = int(request_path(req).lstrip("/") or 0)
        if n >= hops:
            return make_response(body="final")
        return make_response(301, "Moved Permanently", {"Location": f"/{n + 1}"})
    return handler


def test_follows_relative_redirect(serve):
    server = serve(redirecting_handler(1))
    r = fetch(server.url("/0"), cache=None)
    assert r.text == "final"
    assert r.url == server.url("/1")  # l'URL finale, pas celle demandée


def test_follows_absolute_redirect(serve):
    server = serve(lambda req: make_response(body="ok") if request_path(req) == "/b"
                   else make_response(302, "Found", {"Location": f"http://localhost:{server.port}/b"}))
    assert fetch(server.url("/a"), cache=None).text == "ok"


def test_five_redirects_are_allowed(serve):
    server = serve(redirecting_handler(5))
    assert fetch(server.url("/0"), cache=None).text == "final"


def test_sixth_redirect_raises(serve):
    server = serve(redirecting_handler(6))
    with pytest.raises(TooManyRedirects):
        fetch(server.url("/0"), cache=None)


def test_redirect_loop_raises(serve):
    server = serve(lambda req: make_response(302, "Found", {"Location": "/"}))
    with pytest.raises(TooManyRedirects):
        fetch(server.url("/"), cache=None)
    assert len(server.requests) == 6  # 1 requête initiale + 5 redirections suivies


def test_redirect_without_location_raises(serve):
    server = serve(lambda req: make_response(302, "Found"))
    with pytest.raises(NetworkError, match="Location"):
        fetch(server.url(), cache=None)


# ---- 1d : cache --------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


def test_cache_hit_avoids_second_request(serve):
    server = serve(lambda req: make_response(headers={"Cache-Control": "max-age=60"}, body="v1"))
    cache = Cache()
    first = fetch(server.url(), cache=cache)
    second = fetch(server.url(), cache=cache)
    assert first.text == second.text == "v1"
    assert len(server.requests) == 1


def test_cache_entry_expires(serve):
    server = serve(lambda req: make_response(headers={"Cache-Control": "max-age=60"}))
    clock = FakeClock()
    cache = Cache(clock=clock)
    fetch(server.url(), cache=cache)
    clock.now = 59
    fetch(server.url(), cache=cache)
    assert len(server.requests) == 1  # encore frais
    clock.now = 60
    fetch(server.url(), cache=cache)
    assert len(server.requests) == 2  # périmé : on retourne au réseau


@pytest.mark.parametrize("cache_control", [None, "no-store", "no-cache", "max-age=0", "max-age=abc"])
def test_non_cacheable_responses_are_refetched(serve, cache_control):
    headers = {"Cache-Control": cache_control} if cache_control else {}
    server = serve(lambda req: make_response(headers=headers))
    cache = Cache()
    fetch(server.url(), cache=cache)
    fetch(server.url(), cache=cache)
    assert len(server.requests) == 2


def test_errors_are_not_cached(serve):
    server = serve(lambda req: make_response(404, "Not Found", {"Cache-Control": "max-age=60"}))
    cache = Cache()
    fetch(server.url(), cache=cache)
    fetch(server.url(), cache=cache)
    assert len(server.requests) == 2


def test_cache_none_disables_caching(serve):
    server = serve(lambda req: make_response(headers={"Cache-Control": "max-age=60"}))
    fetch(server.url(), cache=None)
    fetch(server.url(), cache=None)
    assert len(server.requests) == 2


def test_cache_stores_final_response_of_redirect_chain(serve):
    def handler(req):
        if request_path(req) == "/old":
            return make_response(301, "Moved Permanently", {"Location": "/new"})
        return make_response(headers={"Cache-Control": "max-age=60"}, body="new")
    server = serve(handler)
    cache = Cache()
    fetch(server.url("/old"), cache=cache)
    assert fetch(server.url("/old"), cache=cache).text == "new"
    assert len(server.requests) == 2  # /old puis /new, une seule fois
