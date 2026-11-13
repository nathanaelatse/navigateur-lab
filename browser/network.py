"""Étape 1 : la couche réseau du navigateur.

Usage à la main :
    python -m browser.network http://example.org/
    python -m browser.network -v https://example.org/     # montre la requête brute
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
TIMEOUT = 10  # secondes ; sans timeout, un serveur muet bloquerait le navigateur à vie
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


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
    path: str  # chemin + query, tel qu'il sera écrit dans la requête

    @property
    def host_header(self):
        # Le port n'est écrit dans Host que s'il n'est pas celui par défaut du schéma.
        default = 443 if self.scheme == "https" else 80
        return self.host if self.port == default else f"{self.host}:{self.port}"


def parse_url(url):
    # On délègue le découpage à urlsplit : parser une URL à la main est un
    # exercice piégeux (IPv6, userinfo...) qui n'apprend rien sur HTTP.
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise NetworkError(f"schéma non supporté : {parts.scheme!r}")
    if not parts.hostname:
        raise NetworkError(f"pas d'hôte dans l'URL : {url!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    # Le fragment (#ancre) n'est jamais envoyé : il ne concerne que le navigateur.
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return URL(parts.scheme, parts.hostname, port, path)


def build_request(url):
    """Écrit la requête HTTP/1.1 à la main. Chaque ligne se termine par \\r\\n."""
    lines = [
        f"GET {url.path} HTTP/1.1",
        # Host : obligatoire en HTTP/1.1. Une même IP héberge souvent des dizaines
        # de sites ; c'est ce en-tête qui dit au serveur lequel on veut.
        f"Host: {url.host_header}",
        # Connection: close : "ferme la connexion quand tu as fini". Sans ça HTTP/1.1
        # est en keep-alive : le serveur garde le tuyau ouvert et notre lecture
        # "jusqu'à la fin" ne se terminerait jamais.
        "Connection: close",
        # User-Agent : qui nous sommes. Purement informatif, mais certains
        # serveurs refusent les requêtes qui n'en ont pas.
        f"User-Agent: {USER_AGENT}",
        # Accept-Encoding: identity : "pas de compression". On ne sait pas encore
        # décompresser gzip ; on le dit donc explicitement au serveur.
        "Accept-Encoding: identity",
    ]
    # La ligne vide finale (\r\n\r\n) marque la fin des en-têtes.
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


@dataclass
class Response:
    url: str
    status: int
    reason: str
    headers: dict  # noms en minuscules : les en-têtes HTTP sont insensibles à la casse
    body: bytes

    @property
    def text(self):
        # Content-Type: text/html; charset=utf-8 -> on cherche le charset, sinon utf-8.
        charset = "utf-8"
        for param in self.headers.get("content-type", "").split(";")[1:]:
            name, _, value = param.strip().partition("=")
            if name.lower() == "charset":
                charset = value.strip('"')
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:  # charset inconnu
            return self.body.decode("utf-8", errors="replace")


def decode_chunked(data):
    """Décode Transfer-Encoding: chunked.

    Quand un serveur génère la page à la volée, il ne connaît pas sa taille à
    l'avance (donc pas de Content-Length). Il envoie alors des morceaux :
        <taille en hexa>\\r\\n<données>\\r\\n ... 0\\r\\n\\r\\n
    """
    body = bytearray()
    pos = 0
    while True:
        eol = data.find(b"\r\n", pos)
        if eol == -1:
            raise NetworkError("corps chunked tronqué")
        size_line = data[pos:eol].split(b";")[0]  # ignore les "chunk extensions"
        try:
            size = int(size_line, 16)
        except ValueError:
            raise NetworkError(f"taille de chunk invalide : {size_line!r}") from None
        if size == 0:
            return bytes(body)  # on ignore les trailers éventuels
        start = eol + 2
        if start + size > len(data):
            raise NetworkError("corps chunked tronqué")
        body += data[start:start + size]
        pos = start + size + 2  # saute le \r\n après les données


def parse_response(raw, url):
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        raise NetworkError("réponse HTTP incomplète (pas de fin d'en-têtes)")
    # latin-1 décode n'importe quel octet : le plus sûr pour des en-têtes.
    lines = head.decode("iso-8859-1").split("\r\n")

    # Ligne de statut : "HTTP/1.1 200 OK". La raison peut contenir des espaces
    # (ou manquer), d'où le split limité à 2.
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise NetworkError(f"ligne de statut invalide : {lines[0]!r}")
    status = int(parts[1])
    reason = parts[2] if len(parts) == 3 else ""

    headers = {}
    for line in lines[1:]:
        name, colon, value = line.partition(":")
        if not colon:
            continue
        name = name.strip().lower()
        value = value.strip()
        # Un même en-tête peut apparaître plusieurs fois (ex. Set-Cookie) ;
        # la RFC autorise de les fusionner avec des virgules.
        headers[name] = f"{headers[name]}, {value}" if name in headers else value

    # Comment sait-on où s'arrête le corps ? Trois cas, par ordre de priorité.
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = decode_chunked(body)
    elif "content-length" in headers:
        try:
            length = int(headers["content-length"])
        except ValueError:
            raise NetworkError("Content-Length invalide") from None
        if len(body) < length:
            raise NetworkError("réponse tronquée (moins d'octets que Content-Length)")
        body = body[:length]
    # sinon : le corps va jusqu'à la fermeture de la connexion (ce qu'on a lu).

    return Response(url, status, reason, headers, body)


def _recv_all(sock):
    # Grâce à "Connection: close", le serveur ferme la connexion à la fin de sa
    # réponse : recv() renvoie alors b"" et on sait qu'on a tout reçu.
    chunks = []
    while True:
        data = sock.recv(4096)
        if not data:
            return b"".join(chunks)
        chunks.append(data)


def _request_once(url_str, ssl_context, verbose):
    """Une seule requête GET, sans suivre les redirections."""
    url = parse_url(url_str)
    request = build_request(url)
    if verbose:
        print(f"--> {url_str}\n{request.decode('ascii')}", file=sys.stderr)
    try:
        # create_connection : résolution DNS (nom -> IP) + handshake TCP.
        with socket.create_connection((url.host, url.port), timeout=TIMEOUT) as raw:
            if url.scheme == "https":
                # Sous-étape 1b : on enveloppe le socket TCP dans une couche TLS.
                # Tout le reste du code (sendall/recv) est identique : TLS est
                # transparent pour HTTP.
                context = ssl_context or ssl.create_default_context()
                # server_hostname fait deux choses : SNI (dire au serveur quel
                # site on veut, avant même HTTP) et vérifier que le certificat
                # correspond bien à ce nom (sinon n'importe qui pourrait se faire
                # passer pour le site).
                with context.wrap_socket(raw, server_hostname=url.host) as tls:
                    tls.sendall(request)
                    data = _recv_all(tls)
            else:
                raw.sendall(request)
                data = _recv_all(raw)
    except OSError as e:  # DNS, connexion refusée, timeout, erreur TLS : tous des OSError
        raise NetworkError(f"{url_str} : {e}") from e

    response = parse_response(data, url_str)
    if verbose:
        print(f"<-- {response.status} {response.reason}", file=sys.stderr)
    return response


# --------------------------------------------------------------------------
# Sous-étape 1d : le cache (défini avant fetch qui l'utilise)
# --------------------------------------------------------------------------

def _max_age(response):
    """Durée de validité en secondes selon Cache-Control, ou None si non cachable."""
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
    """Cache mémoire : url -> (réponse, instant d'expiration).

    `clock` est injectable pour pouvoir tester l'expiration sans attendre.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._entries = {}

    def get(self, url):
        entry = self._entries.get(url)
        if entry is None:
            return None
        response, expires_at = entry
        if self._clock() >= expires_at:
            del self._entries[url]  # périmé : on l'oublie
            return None
        return response

    def put(self, url, response):
        ttl = _max_age(response)
        # On ne garde que les 200 avec un max-age explicite > 0 (voir "simplifications").
        if response.status != 200 or not ttl or ttl <= 0:
            return
        self._entries[url] = (response, self._clock() + ttl)

    def clear(self):
        self._entries.clear()


default_cache = Cache()


# --------------------------------------------------------------------------
# Sous-étape 1c : fetch = requête + redirections + cache
# --------------------------------------------------------------------------

def fetch(url, *, max_redirects=MAX_REDIRECTS, cache=default_cache,
          ssl_context=None, verbose=False):
    """Télécharge `url` et renvoie une Response.

    Un statut 404 ou 500 n'est PAS une erreur réseau : on renvoie la Response,
    c'est à l'appelant (le navigateur) de décider quoi afficher.
    `cache=None` désactive le cache.
    """
    if cache is not None:
        cached = cache.get(url)
        if cached is not None:
            if verbose:
                print(f"(cache) {url}", file=sys.stderr)
            return cached

    current = url
    redirects = 0
    while True:
        response = _request_once(current, ssl_context, verbose)
        if response.status not in REDIRECT_STATUSES:
            break
        # Location : où aller ensuite. Souvent relatif ("/login") : urljoin le
        # résout par rapport à l'URL courante.
        location = response.headers.get("location")
        if not location:
            raise NetworkError(f"redirection {response.status} sans en-tête Location")
        if redirects == max_redirects:
            # Protège des boucles (A -> B -> A -> ...) qui bloqueraient le navigateur.
            raise TooManyRedirects(f"plus de {max_redirects} redirections depuis {url}")
        redirects += 1
        current = urljoin(current, location)

    if cache is not None:
        cache.put(url, response)
    return response


# --------------------------------------------------------------------------
# Lancement à la main
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Télécharge une URL avec le mini-navigateur.")
    parser.add_argument("url")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="affiche la requête brute envoyée")
    parser.add_argument("--chars", type=int, default=500,
                        help="nombre de caractères du corps à afficher (défaut : 500)")
    args = parser.parse_args(argv)

    try:
        response = fetch(args.url, verbose=args.verbose)
    except NetworkError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1

    print(f"{response.status} {response.reason}  ({response.url})")
    for name, value in response.headers.items():
        print(f"{name}: {value}")
    print()
    print(response.text[:args.chars])
    return 0


if __name__ == "__main__":
    sys.exit(main())
