"""Étape 1 (version "trace") : network.py, mais chaque étape s'affiche.

C'est une COPIE de browser/network.py à laquelle on a ajouté des affichages.
La logique est la même ; les lignes `_say(...)` sont les seuls ajouts. Pour
comprendre le vrai code, lisez network.py ; pour VOIR ce qu'il fait, lancez celui-ci.

Usage à la main :
    python -m browser.network_trace http://example.org/          # 2 fetch : le 2e passe par le cache
    python -m browser.network_trace https://github.com/ --repeat 1
"""

import argparse
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

USER_AGENT = "MiniBrowser/0.1 (projet pedagogique)"
MAX_REDIRECTS = 5
TIMEOUT = 10
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


# --------------------------------------------------------------------------
# Outil d'affichage (le seul ajout par rapport à network.py)
# --------------------------------------------------------------------------

class _Trace:
    """Numérote les étapes et affiche le temps écoulé depuis le début du fetch."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.n = 0
        self.t0 = time.perf_counter()

    def ms(self):
        return (time.perf_counter() - self.t0) * 1000


_trace = _Trace()


def _say(label, first_line, *more_lines):
    """Une étape : '[03] +  12.3 ms  TCP      message', puis des lignes de détail alignées."""
    _trace.n += 1
    prefix = f"[{_trace.n:02d}] +{_trace.ms():8.1f} ms  {label:<12}"
    print(prefix + first_line)
    for line in more_lines:
        print(" " * len(prefix) + line)


def _show_raw(data):
    """Rend \\r\\n visibles : c'est ce qui distingue vraiment HTTP d'un texte quelconque."""
    return [line + "\\r\\n" for line in data.replace("\r\n", "\n").split("\n")]


class NetworkError(Exception):
    """Tout ce qui peut mal tourner entre nous et le serveur."""


class TooManyRedirects(NetworkError):
    pass


# --------------------------------------------------------------------------
# Sous-étape 1a : URL -> requête -> réponse
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class URL:
    scheme: str
    host: str
    port: int
    path: str

    @property
    def host_header(self):
        default = 443 if self.scheme == "https" else 80
        return self.host if self.port == default else f"{self.host}:{self.port}"


def parse_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise NetworkError(f"schéma non supporté : {parts.scheme!r}")
    if not parts.hostname:
        raise NetworkError(f"pas d'hôte dans l'URL : {url!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return URL(parts.scheme, parts.hostname, port, path)


def build_request(url):
    lines = [
        f"GET {url.path} HTTP/1.1",
        f"Host: {url.host_header}",
        "Connection: close",
        f"User-Agent: {USER_AGENT}",
        "Accept-Encoding: identity",
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


@dataclass
class Response:
    url: str
    status: int
    reason: str
    headers: dict
    body: bytes

    @property
    def text(self):
        charset = "utf-8"
        for param in self.headers.get("content-type", "").split(";")[1:]:
            name, _, value = param.strip().partition("=")
            if name.lower() == "charset":
                charset = value.strip('"')
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def decode_chunked(data):
    body = bytearray()
    sizes = []
    pos = 0
    while True:
        eol = data.find(b"\r\n", pos)
        if eol == -1:
            raise NetworkError("corps chunked tronqué")
        size_line = data[pos:eol].split(b";")[0]
        try:
            size = int(size_line, 16)
        except ValueError:
            raise NetworkError(f"taille de chunk invalide : {size_line!r}") from None
        if size == 0:
            _say("CHUNKS", f"chunk final '0' : {len(sizes)} chunk(s) de {sizes[:6]}{'…' if len(sizes) > 6 else ''} octets"
                     f" → {len(body)} octets de corps")
            return bytes(body)
        start = eol + 2
        if start + size > len(data):
            raise NetworkError("corps chunked tronqué")
        body += data[start:start + size]
        sizes.append(size)
        pos = start + size + 2


def parse_response(raw, url):
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        raise NetworkError("réponse HTTP incomplète (pas de fin d'en-têtes)")
    head_text = head.decode("iso-8859-1")
    lines = head_text.split("\r\n")

    _say("PARSE", "on coupe la réponse à la 1re ligne vide (\\r\\n\\r\\n) :",
         f"→ {len(head)} octets d'en-têtes | {len(body)} octets de corps (bruts)",
         "en-têtes bruts :", *("    " + l for l in _show_raw(head_text)))

    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise NetworkError(f"ligne de statut invalide : {lines[0]!r}")
    status = int(parts[1])
    reason = parts[2] if len(parts) == 3 else ""
    _say("PARSE", "ligne de statut :", f"version={parts[0]}  status={status}  reason={reason!r}")

    headers = {}
    for line in lines[1:]:
        name, colon, value = line.partition(":")
        if not colon:
            continue
        name = name.strip().lower()
        value = value.strip()
        headers[name] = f"{headers[name]}, {value}" if name in headers else value
    _say("PARSE", f"{len(headers)} en-têtes (noms passés en minuscules) :",
         *(f"    {k} = {v}" for k, v in headers.items()))

    if headers.get("transfer-encoding", "").lower() == "chunked":
        _say("PARSE", "fin du corps ? Transfer-Encoding: chunked → on décode les chunks")
        body = decode_chunked(body)
    elif "content-length" in headers:
        try:
            length = int(headers["content-length"])
        except ValueError:
            raise NetworkError("Content-Length invalide") from None
        _say("PARSE", f"fin du corps ? Content-Length: {length} → on garde {length} octets "
                      f"(reçus : {len(body)})")
        if len(body) < length:
            raise NetworkError("réponse tronquée (moins d'octets que Content-Length)")
        body = body[:length]
    else:
        _say("PARSE", "fin du corps ? ni chunked ni Content-Length → "
                      "tout ce qu'on a lu jusqu'à la fermeture de la connexion")

    response = Response(url, status, reason, headers, body)
    preview = response.text[:70].replace("\n", " ")
    _say("PARSE", f"Response construite : {len(body)} octets de corps",
         f"aperçu : {preview!r}{'…' if len(response.text) > 70 else ''}")
    return response


def _recv_all(sock):
    chunks = []
    first_byte_at = None
    start = time.perf_counter()
    while True:
        data = sock.recv(4096)
        if first_byte_at is None:
            first_byte_at = (time.perf_counter() - start) * 1000
        if not data:
            total = sum(len(c) for c in chunks)
            _say("RECV", f"{total} octets reçus en {len(chunks)} appel(s) recv(), "
                         f"fin = recv() a renvoyé b'' (le serveur a fermé : Connection: close)",
                 f"premier octet reçu {first_byte_at:.1f} ms après l'envoi (≈ TTFB dans les DevTools)")
            return b"".join(chunks)
        chunks.append(data)


def _describe_cert(tls):
    cert = tls.getpeercert()  # non vide car la vérification est active
    subject = dict(x[0] for x in cert.get("subject", ()))
    issuer = dict(x[0] for x in cert.get("issuer", ()))
    sans = [v for _, v in cert.get("subjectAltName", ())]
    return [
        f"certificat : sujet={subject.get('commonName', '?')}  émis par={issuer.get('organizationName') or issuer.get('commonName', '?')}",
        f"valide jusqu'au {cert.get('notAfter', '?')}  |  noms couverts : {', '.join(sans[:4])}{'…' if len(sans) > 4 else ''}",
    ]


def _request_once(url_str, ssl_context, verbose):
    """Une seule requête GET, sans suivre les redirections."""
    url = parse_url(url_str)
    _say("URL", f"parse_url → scheme={url.scheme}  host={url.host}  port={url.port}  path={url.path!r}",
         f"(le Host envoyé sera {url.host_header!r} ; le #fragment, s'il y en avait un, n'est jamais envoyé)")

    request = build_request(url)
    _say("REQUEST", f"build_request → {len(request)} octets, écrits à la main :",
         *("    " + l for l in _show_raw(request.decode("ascii"))[:-1]))

    try:
        t = time.perf_counter()
        with socket.create_connection((url.host, url.port), timeout=TIMEOUT) as raw:
            peer = raw.getpeername()
            _say("DNS+TCP", f"{url.host} → {peer[0]}  |  connecté au port {peer[1]} "
                            f"({(time.perf_counter() - t) * 1000:.1f} ms pour DNS + handshake TCP)")
            if url.scheme == "https":
                context = ssl_context or ssl.create_default_context()
                t = time.perf_counter()
                with context.wrap_socket(raw, server_hostname=url.host) as tls:
                    _say("TLS", f"handshake terminé en {(time.perf_counter() - t) * 1000:.1f} ms : "
                                f"{tls.version()}, chiffrement {tls.cipher()[0]}",
                         f"SNI envoyé : {url.host}  → le certificat est vérifié contre ce nom",
                         *_describe_cert(tls))
                    _say("SEND", f"sendall() de {len(request)} octets (chiffrés par TLS en réalité)")
                    tls.sendall(request)
                    data = _recv_all(tls)
            else:
                _say("TLS", "ignoré (schéma http : tout circule en clair)")
                _say("SEND", f"sendall() de {len(request)} octets")
                raw.sendall(request)
                data = _recv_all(raw)
    except OSError as e:
        _say("ERROR", f"{type(e).__name__} : {e}")
        raise NetworkError(f"{url_str} : {e}") from e

    return parse_response(data, url_str)


# --------------------------------------------------------------------------
# Sous-étape 1d : le cache
# --------------------------------------------------------------------------

def _max_age(response):
    directives = [d.strip() for d in response.headers.get("cache-control", "").lower().split(",")]
    if "no-store" in directives or "no-cache" in directives:
        return None
    for directive in directives:
        if directive.startswith("max-age="):
            try:
                return int(directive[len("max-age="):])
            except ValueError:
                return None
    return None


class Cache:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._entries = {}

    def get(self, url):
        entry = self._entries.get(url)
        if entry is None:
            _say("CACHE", f"get({url!r}) → MISS  ({len(self._entries)} entrée(s) en cache, aucune pour cette URL)")
            return None
        response, expires_at = entry
        remaining = expires_at - self._clock()
        if remaining <= 0:
            del self._entries[url]
            _say("CACHE", f"get({url!r}) → PÉRIMÉ depuis {-remaining:.0f} s : entrée supprimée, on retourne au réseau")
            return None
        _say("CACHE", f"get({url!r}) → HIT  (encore frais pendant {remaining:.0f} s) : AUCUN accès réseau")
        return response

    def put(self, url, response):
        cc = response.headers.get("cache-control", "(absent)")
        ttl = _max_age(response)
        if response.status != 200:
            reason = f"statut {response.status} ≠ 200"
        elif ttl is None:
            reason = "pas de max-age exploitable (absent, no-store ou no-cache)"
        elif ttl <= 0:
            reason = "max-age ≤ 0"
        else:
            reason = None
        if reason:
            _say("CACHE", f"put() → NON stocké : {reason}", f"Cache-Control reçu : {cc}")
            return
        self._entries[url] = (response, self._clock() + ttl)
        _say("CACHE", f"put() → STOCKÉ pour {ttl} s sous la clé {url!r}", f"Cache-Control reçu : {cc}")

    def clear(self):
        self._entries.clear()

    def describe(self):
        if not self._entries:
            return ["(cache vide)"]
        return [f"{url}  → encore frais {exp - self._clock():.0f} s  ({len(r.body)} octets)"
                for url, (r, exp) in self._entries.items()]


default_cache = Cache()


# --------------------------------------------------------------------------
# Sous-étape 1c : fetch = requête + redirections + cache
# --------------------------------------------------------------------------

def fetch(url, *, max_redirects=MAX_REDIRECTS, cache=default_cache,
          ssl_context=None, verbose=False):
    """Même contrat que network.fetch ; `verbose` est accepté pour la compatibilité (tout est déjà affiché)."""
    _trace.reset()
    print(f"\n══ fetch({url!r}) ══")

    if cache is not None:
        cached = cache.get(url)
        if cached is not None:
            _say("DONE", f"réponse servie par le cache : {cached.status} {cached.reason}, {len(cached.body)} octets")
            return cached
    else:
        _say("CACHE", "désactivé (cache=None)")

    current = url
    redirects = 0
    while True:
        _say("HOP", f"requête n°{redirects + 1} vers {current}")
        response = _request_once(current, ssl_context, verbose)
        if response.status not in REDIRECT_STATUSES:
            _say("HOP", f"{response.status} n'est pas une redirection → on s'arrête")
            break
        location = response.headers.get("location")
        if not location:
            raise NetworkError(f"redirection {response.status} sans en-tête Location")
        if redirects == max_redirects:
            _say("REDIRECT", f"limite atteinte ({max_redirects} redirections déjà suivies) → abandon")
            raise TooManyRedirects(f"plus de {max_redirects} redirections depuis {url}")
        redirects += 1
        target = urljoin(current, location)
        _say("REDIRECT", f"{response.status} → Location: {location!r}",
             f"urljoin({current!r}, {location!r}) = {target!r}",
             f"redirection {redirects}/{max_redirects} ; on refait un GET sur la nouvelle URL")
        current = target

    if cache is not None:
        cache.put(url, response)

    _say("DONE", f"{response.status} {response.reason} depuis {response.url}",
         f"{len(response.body)} octets de corps, {redirects} redirection(s) suivie(s)")
    if cache is not None:
        print("    état du cache :")
        for line in cache.describe():
            print("      " + line)
    return response


# --------------------------------------------------------------------------
# Lancement à la main
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Télécharge une URL en affichant chaque étape.")
    parser.add_argument("url")
    parser.add_argument("--repeat", type=int, default=2,
                        help="nombre de fetch successifs (défaut : 2, pour voir le cache en action)")
    args = parser.parse_args(argv)

    try:
        for _ in range(args.repeat):
            fetch(args.url)
    except NetworkError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
