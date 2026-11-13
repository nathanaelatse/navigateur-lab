# MiniBrowser : un navigateur web écrit pas à pas

Un navigateur minuscule, écrit en Python **sans bibliothèque de rendu** : sockets bruts pour HTTP,
parseur HTML et CSS faits main, moteur de mise en page, rendu dans une fenêtre Tk, et même un peu
de JavaScript. Le but n'est pas de remplacer Firefox, c'est de **voir ce qui se passe** entre
« j'écris une URL » et « une page s'affiche ».

Chaque étape est un module court, lisible d'une traite, avec des commentaires qui expliquent le
*pourquoi*, et une suite de tests qui sert de spécification (431 tests).

```
 URL ──► 1. réseau ──► 2. HTML ──► 3. CSS ──► 4. layout ──► 5. rendu ──► pixels
         texte HTTP    arbre DOM   styles      boîtes + x,y   commandes     (Tk)
                          ▲                                    de dessin
                          └──────────── 6. JavaScript modifie le DOM ─────┐
                                        puis on refait 3, 4 et 5 ◄────────┘
```

## Démarrage rapide

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # dukpy (JavaScript) et pytest
pytest                                   # 431 tests, moins d'une seconde

python -m browser.gui                    # la fenêtre (voir « Tk » ci-dessous)
python -m browser.gui http://example.org/
```

**Tk** (la fenêtre) n'est pas toujours fourni avec Python :
macOS (Homebrew) `brew install python-tk@3.14`, Debian/Ubuntu `sudo apt install python3-tk`.
Sans Tk, tout le reste fonctionne : chaque étape a une commande qui affiche son résultat dans le
terminal (voir plus bas). Testé avec Python 3.14.

Dans la fenêtre : barre d'URL, bouton ← , molette / flèches / Page↑ Page↓ / Début / Fin pour
défiler, clic sur les liens. Redimensionnez la fenêtre : le texte se recoupe en direct.

## Les six étapes

Pour chaque étape : **ce qu'elle fait**, **où lire**, **quoi lancer** pour la voir fonctionner.

### 1. Réseau : de l'URL aux octets

Ouvre une connexion TCP (et TLS pour `https`), écrit la requête HTTP **à la main**, lit la réponse,
suit les redirections, décode le `Transfer-Encoding: chunked`, garde un cache en mémoire
(`Cache-Control: max-age`).

- Lire : [browser/network.py](browser/network.py) et le plan détaillé dans
  [docs/network-architecture.md](docs/network-architecture.md).
- Lancer : `python -m browser.network_trace http://example.org/` affiche **chaque étape**
  (DNS, TLS, requête brute, redirections, cache) ; le second `fetch` passe par le cache.
  `python -m browser.network -v <url>` montre la requête brute.
- À retenir : un statut 404 n'est pas une erreur réseau, c'est une réponse comme une autre.

### 2. Parser le HTML : du texte à l'arbre

Une **machine à états** lit le HTML caractère par caractère (17 états : « dans un nom de balise »,
« dans une valeur d'attribut entre guillemets »…) et émet des événements ; un second étage
(`TreeBuilder`) tient la pile des éléments ouverts et construit l'arbre `Element` / `Text`.
Il devine les balises oubliées (`<p>a<p>b`, `<li>`, `<td>`…), comme le font les navigateurs.

- Lire : [browser/html_parser.py](browser/html_parser.py)
- Lancer : `echo '<p>un<p>deux <b>gras<br>fin' | python -m browser.html_parser -`
- À retenir : le HTML n'est **jamais invalide** pour un navigateur. Il y a toujours un arbre.

### 3. CSS : quel style pour quel élément ?

Parseur de règles et de sélecteurs (`balise`, `.classe`, `#id`, `p.a#b`), puis calcul des styles
en trois couches : **la cascade** (qui gagne quand deux règles se contredisent), **l'héritage**
(la couleur du `<body>` descend aux paragraphes, pas la marge), et **les valeurs par défaut**
(une mini feuille de style du navigateur : les `<h1>` sont gros et gras sans que la page le dise).

- Lire : [browser/css.py](browser/css.py)
- Lancer : `python -m browser.css page.html` affiche l'arbre avec les styles de chaque élément.
- À retenir : la **spécificité** est un tuple `(ids, classes, balises)` comparé de gauche à droite.
  Un `#id` bat n'importe quel nombre de classes, ce qu'un calcul « a×100 + b×10 + c » ne garantit pas.

### 4. Layout : où va chaque chose ?

Transforme le DOM stylé en **boîtes** avec une position et une taille en pixels : retour à la ligne
du texte selon la largeur de la fenêtre, blocs empilés, puis `margin` / `padding` / `border`.

- Lire : [browser/layout.py](browser/layout.py) (la théorie est dans sa docstring)
- Lancer : `python -m browser.layout --width 300 page.html`, puis refaire avec `--width 120`.

**Block vs inline.** Un élément `block` (`div`, `p`, `h1`…) est une **boîte** : elle prend toute la
largeur disponible et se place *sous* la précédente. Un élément `inline` (`span`, `b`, `a`…) n'est
**pas** une boîte : c'est un style appliqué à des morceaux de texte qui *coulent* dans la ligne,
de gauche à droite, et passent à la ligne suivante quand elle est pleine. Un `<b>` peut donc être
coupé sur deux lignes, un `<div>` jamais. Conséquence : un bloc contient soit des blocs, soit des
lignes de texte, jamais un mélange (sinon on enveloppe le texte dans une boîte « anonyme »).

**Pourquoi un changement de largeur déclenche un reflow.** La largeur d'un bloc dépend de celle de
son parent, donc de la fenêtre. La largeur d'un paragraphe décide où les lignes se coupent, donc
combien il y en a, donc sa **hauteur** ; cette hauteur décale le bloc suivant, et ainsi de suite
jusqu'en bas de la page. Rien n'est mis en cache : tout est recalculé. Ici, c'est
`layout(racine, nouvelle_largeur)` relancé sur le **même** arbre de boîtes.

### 5. Rendu : dessiner

Le rendu ne calcule rien de géométrique : il *lit* les boîtes déjà placées et produit une
**display list**, c'est-à-dire une liste de commandes très simples (`DrawRect`, `DrawText`) en
coordonnées du document. Puis `execute()` la rejoue sur un Canvas Tk. Le scroll, la barre d'URL et
les liens cliquables sont dans l'onglet (`Tab`), qui ne dépend pas de Tk.

- Lire : [browser/paint.py](browser/paint.py), [browser/tab.py](browser/tab.py),
  [browser/gui.py](browser/gui.py)
- Lancer : `python -m browser.paint page.html --width 400` montre la liste de commandes, sans fenêtre.

**Pourquoi séparer layout et paint ?** Le layout répond à « où ? et quelle taille ? », la peinture à
« de quelle couleur, avec quelle police, dans quel ordre ? ». Séparés, ils rendent chaque
événement bon marché :

| Événement | Ce qui est recalculé |
|---|---|
| scroll | rien : on rejoue la display list avec un autre décalage |
| redimensionnement | layout puis display list (le DOM et les styles restent) |
| changement de couleur | display list seulement (rien ne bouge) |
| le DOM change (JS) | styles, **nouvel** arbre de boîtes, layout, display list |

Autres bénéfices : toute la peinture se teste sans écran (on compare des listes de données), on ne
dessine que les commandes visibles, et changer de moteur de rendu (PDF, SVG…) ne touche pas au layout.

### 6. JavaScript (bonus)

Intègre l'interpréteur [dukpy](https://github.com/amol-/dukpy) (Duktape) et expose un `document`
minimal : `querySelectorAll`, `querySelector`, `getElementById`, `getAttribute`, `setAttribute`,
`innerHTML`, `textContent`, `console.log`, `setTimeout`. Quand un script modifie le DOM, la page
est restylée, remise en page et redessinée.

- Lire : [browser/js.py](browser/js.py) (l'explication complète est dans sa docstring)
- Lancer : `python -m browser.gui` : la page de démarrage contient un script et un `setTimeout`.
- Optionnel : sans `dukpy`, les `<script>` sont simplement ignorés.

**Pourquoi le JS tourne dans un contexte isolé.** Un script de page vient d'un site à qui on ne fait
pas confiance. Il tourne donc dans un bac à sable :

1. **Tas mémoire séparé** : le JS ne peut atteindre aucun objet Python.
2. **Capacités explicites** : il ne peut faire que ce que le navigateur exporte (lire un attribut,
   modifier `innerHTML`…), jamais de fichiers ni de réseau.
3. **Des copies, pas des références** : un nœud du DOM n'est jamais donné au JS, seulement une
   *poignée* (un entier) que le Python sait retraduire.
4. **Un contexte par page** : variables et timers ne survivent pas à un changement de page.

> **L'isolation n'est pas gratuite.** Mesuré avec dukpy 0.6, un contexte neuf expose `require` (qui
> **lit n'importe quel fichier** et en fuit le contenu dans les messages d'erreur) et `process.env`
> (**toutes les variables d'environnement** du navigateur). Le projet les supprime, et des tests
> vérifient chaque fermeture. Essayez en Python :
> `from browser.tab import Tab; t = Tab(); t.load("about:start"); t.evaluate("typeof require")`
> renvoie `"undefined"`.
> Limite connue : pas de limite de temps ni de mémoire, un `while(true){}` gèle le navigateur.

## Essayez vous-même

Enregistrez ceci dans `demo.html` :

```html
<style>
  .box { border: 2px solid red; padding: 6px; margin: 10px auto; width: 60% }
</style>
<h1>Titre de la page</h1>
<p>Un paragraphe avec du <b>gras</b> et un texte assez long pour passer à la ligne.</p>
<div class=box>Une boîte centrée</div>
```

1. `python -m browser.layout --width 300 demo.html` puis `--width 120` : comparez le nombre de lignes
   et la position de la boîte (c'est le reflow).
2. Supprimez un `</p>` ou ajoutez `<p>` sans le fermer : `python -m browser.html_parser demo.html`
   montre que l'arbre reste cohérent.
3. Dans la feuille de style, ajoutez `p { color: red }` puis `.box { color: blue }` : lequel gagne
   sur un `<p class=box>` ? Pourquoi ? (`python -m browser.css demo.html`)
4. `python -m browser.paint demo.html` : repérez les 4 rectangles de la bordure et le texte.
5. Ouvrez-la dans la fenêtre avec un petit serveur : `python -m http.server 8000` dans le dossier,
   puis `python -m browser.gui http://localhost:8000/demo.html`.

## Organisation du dépôt

```
browser/
  network.py        étape 1  sockets, TLS, redirections, cache
  network_trace.py  étape 1  la même chose, en affichant chaque pas
  html_parser.py    étape 2  tokenizer à états + constructeur d'arbre
  css.py            étape 3  règles, sélecteurs, cascade, héritage
  layout.py         étape 4  lignes, blocs, margin/padding/border
  paint.py          étape 5  display list et zones de liens
  tab.py            étape 5  onglet : chargement, historique, scroll, clics
  gui.py            étape 5  fenêtre Tk (colle entre Tk et Tab)
  js.py             étape 6  dukpy, document, isolation
tests/              un fichier par module
docs/               plan détaillé de l'étape 1
```

Le fil conducteur : **chaque étape est une fonction pure ou presque**, ce qui la rend testable sans
réseau ni écran. Les tests injectent ce qui est « du monde extérieur » : un faux serveur HTTP local
(sockets bruts, comme le client), une horloge simulée (cache et `setTimeout`), une fonction de mesure
du texte (`measure`), un faux `fetcher`, un « enregistreur » à la place du Canvas.

## Ce qui n'est volontairement pas là

Pour rester lisible, chaque module liste ses simplifications en tête de fichier. Les principales :

- **CSS** : un seul sélecteur composé (pas de `div p`, de `>`, de `:hover`), pas de `!important`,
  pas d'attribut `style=""`, pas de `<link rel=stylesheet>`.
- **Layout** : pas de float, de `position`, de flex, d'images, de `white-space: pre` ; les marges d'un
  parent et de son premier enfant s'additionnent au lieu de se superposer.
- **Rendu** : fonds et bordures seulement sur les blocs ; toutes les bordures sont « solid ».
- **JS** : pas d'événements (`onclick`), pas de `createElement` ni de `fetch`.
- **Sécurité** : c'est un jouet pédagogique. Ne l'utilisez pas pour naviguer sur des sites auxquels
  vous ne faites pas confiance.

## Aller plus loin

Ce projet suit l'esprit du livre gratuit
[Web Browser Engineering](https://browser.engineering/) : si une étape vous intrigue, le chapitre
correspondant va plus loin. Idées d'extensions : sélecteurs descendants (`div p`), `<img>`, les
puces des `<li>`, `onclick`, les marges qui se superposent entre parent et enfant, un mode « lecture ».
