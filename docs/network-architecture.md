# Architecture de `browser/network.py`

Le module a **une seule porte d'entrée publique : `fetch(url)`**. Tout le reste
est un bloc qu'elle assemble. Le fichier est découpé en blocs qui correspondent
aux sous-étapes de l'étape 1.

## 1. Vue d'ensemble : qui appelle qui

```
                         ┌───────────────────────────────┐
   main() (CLI)  ──────► │            fetch(url)         │ ◄────── (étapes 2+ : le navigateur)
                         │  1c : cache + redirections    │
                         └───┬───────────────────┬───────┘
                             │                   │
              1d : lit/écrit │                   │ une requête par saut
                             ▼                   ▼
                    ┌──────────────┐     ┌───────────────────┐
                    │    Cache     │     │  _request_once()  │
                    │ get / put    │     │  1a + 1b          │
                    └──────┬───────┘     └──┬──────┬──────┬──┘
                           │                │      │      │
                           ▼                ▼      ▼      ▼
                      _max_age()     parse_url  build_  parse_response()
                      (Cache-         (→ URL)   request       │
                       Control)                  (→ bytes)    ▼
                                                         decode_chunked()
                                                         (si chunked)
                                    │
                                    ▼
                    socket.create_connection ──► [ssl.wrap_socket si https] ──► sendall / _recv_all
```

Lecture : **`fetch` orchestre, `_request_once` fait UN aller-retour réseau,
et les petites fonctions pures (`parse_url`, `build_request`, `parse_response`,
`decode_chunked`, `_max_age`) font le travail sur des données sans toucher au réseau.**
C'est ce qui permet de tester chaque morceau isolément.

## 2. Les trois structures de données

| Type | Champs | Créé par | Consommé par |
|---|---|---|---|
| `URL` | `scheme`, `host`, `port`, `path` (chemin + query), propriété `host_header` | `parse_url` | `build_request`, `_request_once` |
| `Response` | `url`, `status`, `reason`, `headers` (clés en minuscules), `body` (bytes), propriété `text` | `parse_response` | `fetch`, `Cache`, et plus tard `html.py` (via `.text`) |
| `Cache` | dict interne `url → (Response, instant d'expiration)` | module (`default_cache`) | `fetch` |

Deux exceptions : `NetworkError` (tout problème réseau/protocole) et
`TooManyRedirects` (sous-classe de `NetworkError`, donc un seul `except NetworkError`
suffit à l'appelant).

## 3. Les blocs, dans l'ordre du fichier

### Bloc 1a : de l'URL à la réponse (fonctions pures)

| Fonction | Entrée → sortie | Rôle |
|---|---|---|
| `parse_url(str)` | `"http://h:8080/p?q#f"` → `URL` | Découpe, applique les ports par défaut (80/443), jette le fragment, refuse les schémas autres que http/https. |
| `build_request(URL)` | `URL` → `bytes` | Écrit à la main la requête `GET … HTTP/1.1` avec `Host`, `Connection: close`, `User-Agent`, `Accept-Encoding`. |
| `parse_response(bytes, url)` | octets bruts → `Response` | Coupe à la première ligne vide (`\r\n\r\n`), lit la ligne de statut, construit le dict d'en-têtes, puis détermine le corps (voir §5). |
| `decode_chunked(bytes)` | corps chunked → corps décodé | Appelée **uniquement** par `parse_response` quand `Transfer-Encoding: chunked`. |
| `_recv_all(sock)` | socket → `bytes` | Boucle `recv()` jusqu'à `b""` (le serveur a fermé grâce à `Connection: close`). |

### Bloc 1a + 1b : `_request_once(url, ssl_context, verbose)`

Le seul endroit qui touche au réseau. Il enchaîne :

1. `parse_url` puis `build_request` (données pures) ;
2. `socket.create_connection` : DNS + TCP ;
3. **si `https`** (c'est toute la sous-étape 1b) : `context.wrap_socket(raw, server_hostname=host)` enveloppe le socket dans TLS ;
4. `sendall(request)` puis `_recv_all(...)` : ces deux lignes sont **identiques** que le socket soit TCP nu ou TLS, car TLS est transparent pour HTTP ;
5. `parse_response` sur les octets reçus.

Toute `OSError` (DNS, refus, timeout, certificat invalide : `ssl.SSLError` en est une) est convertie en `NetworkError`.
Ne fait **aucune** redirection ni cache : un appel = une requête.

### Bloc 1d : le cache (défini avant `fetch` car `fetch` l'utilise)

| Élément | Rôle |
|---|---|
| `_max_age(Response)` | Lit `Cache-Control` ; renvoie la durée en secondes, ou `None` si `no-store`, `no-cache`, ou pas de `max-age`. |
| `Cache.put(url, resp)` | Stocke seulement si `status == 200` **et** `_max_age` > 0. Calcule l'expiration avec l'horloge. |
| `Cache.get(url)` | Renvoie la réponse si fraîche ; sinon supprime l'entrée périmée et renvoie `None`. |
| `clock` (paramètre du constructeur) | Horloge injectable : les tests la remplacent pour simuler le temps. |
| `default_cache` | Instance globale partagée, valeur par défaut du paramètre `cache` de `fetch`. |

### Bloc 1c : `fetch(url, …)`, l'orchestrateur

C'est le seul bloc qui connaît à la fois le cache **et** les redirections.
Voir le déroulé ci-dessous.

### Bloc CLI : `main()`

Parse les arguments (`url`, `-v`, `--chars`), appelle `fetch`, imprime statut + en-têtes + début du corps.
Il convertit `NetworkError` en message + code de sortie 1. N'est utilisé que par
`python -m browser.network`.

## 4. Déroulé d'un `fetch("http://github.com/")`

```
fetch(url)
 │
 ├─ cache is not None ? ── cache.get(url) ── HIT ──► return la Response en cache  (fin)
 │                                          MISS
 ▼
 boucle (current = url, redirects = 0)
 │
 │   ┌──► _request_once(current)
 │   │        parse_url → build_request → TCP → [TLS] → send → recv → parse_response
 │   │
 │   │    status ∈ {301,302,303,307,308} ?
 │   │      non ──► sortie de boucle
 │   │      oui ──► pas de Location ?            → NetworkError
 │   │              redirects == max_redirects ? → TooManyRedirects
 │   │              redirects += 1
 │   │              current = urljoin(current, Location)   (gère les Location relatifs)
 │   └──────────────┘
 ▼
 cache.put(url_initiale, response_finale)     ← clé = l'URL demandée, valeur = réponse finale
 return response
```

Points à retenir :

- **Le cache est consulté une fois, avant la boucle, et écrit une fois, après.** Les sauts intermédiaires ne sont pas cachés ; c'est la réponse *finale* qui l'est, sous l'URL *initiale*. C'est pourquoi un deuxième `fetch("/old")` ne refait aucune requête, même si `/old` redirigeait vers `/new`.
- **Limite de 5** : le test `redirects == max_redirects` est fait *avant* d'incrémenter. Donc 5 redirections suivies passent, la 6ᵉ lève `TooManyRedirects` (soit 6 requêtes au maximum).
- `Response.url` contient l'URL du **dernier** saut, pas celle demandée.

## 5. Comment `parse_response` trouve la fin du corps

Trois cas, testés dans cet ordre :

```
Transfer-Encoding: chunked ?  ──► decode_chunked(body)
sinon Content-Length présent ? ──► body[:length]   (erreur si on a reçu moins)
sinon                          ──► tout ce qu'on a lu jusqu'à la fermeture
```

## 6. Où naissent les erreurs

| Situation | Résultat |
|---|---|
| DNS, connexion refusée, timeout, certificat TLS invalide | `NetworkError` (cause d'origine dans `__cause__`) |
| Réponse qui n'est pas du HTTP, corps tronqué, chunk invalide | `NetworkError` (levée par `parse_response` / `decode_chunked`) |
| Redirection sans `Location` | `NetworkError` |
| Plus de 5 redirections / boucle | `TooManyRedirects` |
| **404, 500…** | **Pas une erreur** : `fetch` renvoie la `Response`. C'est au navigateur de décider quoi afficher. |

## 7. Ce que verront les étapes suivantes

Le reste du navigateur n'a besoin que de :

```python
from browser.network import fetch, NetworkError

response = fetch(url)      # → Response
response.status            # 200, 404…
response.headers["content-type"]
response.text              # corps décodé en str  → entrée de html.py (étape 2)
```

Tout ce qui est préfixé par `_` (`_request_once`, `_recv_all`, `_max_age`) est interne et
peut changer sans toucher aux autres modules.
