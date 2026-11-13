"""Étape 5b : l'onglet = tout le navigateur SAUF la fenêtre.

Un `Tab` enchaîne les étapes 1 à 5 et garde l'état qui change quand l'utilisateur agit :

    load(url)   réseau (1) ─► parse HTML (2) ─► styles (3) ─► layout (4) ─► display list (5)
    resize(w,h) ............................................► layout (4) ─► display list (5)
    scroll_by(dy) ............................................................ rien à recalculer
    click(x,y)  display list.link_at ─► load(lien)
    tick()/evaluate(js) .... JS modifie le DOM ─► styles (3) ─► NOUVEL arbre de boîtes (4) ─► display list (5)

Il ne dépend pas de Tk : la fenêtre (gui.py) se contente de lui transmettre les événements
(touche, molette, clic, redimensionnement) et de dessiner `visible_commands()`. Du coup tout
ceci se teste sans écran, avec un faux `fetcher`.

Les coordonnées de la display list sont celles du DOCUMENT (y = 0 en haut de la page).
`scroll_y` dit quelle ligne du document est en haut de la fenêtre ; `click(x, y)` reçoit des
coordonnées FENÊTRE et ajoute `scroll_y` pour retrouver le point du document.

Simplifications assumées :
- <link rel=stylesheet> n'est pas téléchargé (seuls les <style> comptent) ;
- les <script> s'exécutent une fois la page parsée, avant le premier calcul de styles (voir js.py) ;
- tout contenu est parsé comme du HTML, quel que soit son Content-Type ;
- un lien `#ancre` ne fait rien (pas de scroll vers l'élément) ; mailto:, javascript:... sont ignorés ;
- « Précédent » recharge la page (le cache de l'étape 1 évite parfois le réseau) et revient en haut.
"""

import html as html_lib
import time
from urllib.parse import urljoin, urlsplit

from browser import js
from browser.css import style_document
from browser.html_parser import Element, Text, parse
from browser.layout import default_measure, layout, build_layout_tree
from browser.network import NetworkError, fetch
from browser.paint import build_display_list

START_URL = "about:start"
START_HTML = """<!doctype html>
<title>Page de démarrage</title>
<style>
  body { margin: 24px }
  h1 { color: navy }
  .card { border: 2px solid gray; padding: 10px; margin-top: 12px; background-color: #f5f5f5 }
  .small { font-size: 12px; color: gray }
</style>
<h1>MiniBrowser</h1>
<p>Un navigateur écrit pas à pas : <b>réseau</b>, <i>HTML</i>, CSS, layout et rendu.</p>
<div class=card>
  Saisissez une adresse dans la barre du haut, ou suivez un lien :
  <a href="http://example.org/">example.org</a>.
</div>
<p class=small>Molette, flèches, Page précédente/suivante, Début et Fin font défiler.
Redimensionnez la fenêtre : le texte se recoupe (reflow).</p>
<p class=small>JavaScript : <span id=js-status>désactivé</span></p>
<script>
  var out = document.getElementById("js-status");
  out.textContent = "actif, ce texte a été écrit par un script";
  setTimeout(function () { out.textContent = "actif, et ce texte a changé 1 seconde plus tard (reflow)"; }, 1000);
</script>
"""

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def normalize_url(text):
    """Ce que l'utilisateur a tapé -> URL complète. `example.org` -> https://example.org."""
    text = text.strip()
    if text == START_URL or "://" in text:
        return text
    host = urlsplit("//" + text).hostname or ""
    scheme = "http" if host in LOCAL_HOSTS else "https"  # en local, pas de TLS
    return f"{scheme}://{text}"


def error_page(url, message):
    return (f"<!doctype html><title>Erreur</title><h1>Impossible de charger la page</h1>"
            f"<p>{html_lib.escape(url)}</p><p>{html_lib.escape(message)}</p>")


def _elements(root):
    """Tous les éléments sous `root` (lui inclus), dans l'ordre du document."""
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, Element):
            yield node
            stack.extend(reversed(node.children))


def _is_javascript(script):
    return script.attributes.get("type", "").strip().lower() in ("", "text/javascript", "application/javascript")


class Tab:
    def __init__(self, viewport_width=800, viewport_height=600, measure=default_measure, fetcher=fetch,
                 javascript=True, clock=time.monotonic):
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.measure = measure    # (texte, style) -> largeur en px : la fenêtre y branche les vraies polices
        self.fetcher = fetcher    # url -> Response : remplaçable dans les tests
        self.javascript = javascript and js.AVAILABLE  # sans dukpy, les <script> sont ignorés
        self.clock = clock        # horloge des setTimeout : remplaçable dans les tests
        self.console_messages = []  # [(niveau, message)] : console.log et erreurs du JS de la page courante
        self._runtime = None      # interpréteur JS de la page courante (un par chargement, voir js.py)
        self.url = None
        self.history = []
        self.history_index = -1
        self.title = ""
        self.dom = None
        self.layout_root = None
        self.display_list = None
        self.scroll_y = 0.0

    # -- navigation -----------------------------------------------------------------------------

    def navigate(self, text):
        """Ce que tape l'utilisateur dans la barre d'URL."""
        self.load(normalize_url(text))

    def load(self, url, *, record_history=True):
        if url == START_URL:
            final_url, source = url, START_HTML
        else:
            try:
                response = self.fetcher(url)
                final_url, source = response.url, response.text  # url FINALE : après les redirections
            except NetworkError as error:
                final_url, source = url, error_page(url, str(error))
        if record_history:
            del self.history[self.history_index + 1:]  # naviguer après un « précédent » efface le futur
            self.history.append(final_url)
            self.history_index = len(self.history) - 1
        self.url = final_url
        self._render(source)
        self.scroll_y = 0.0

    def can_go_back(self):
        return self.history_index > 0

    def back(self):
        if self.can_go_back():
            self.history_index -= 1
            self.load(self.history[self.history_index], record_history=False)

    def _render(self, source):
        """Étapes 2 à 5 sur du HTML déjà téléchargé (le JS s'intercale entre le parsing et les styles)."""
        self.dom = parse(source)
        self._runtime = None  # nouvelle page = nouveau contexte JS : rien ne survit au changement de page
        self.console_messages = []
        if self.javascript:
            self._run_scripts()
        self._restyle_and_relayout()

    def _restyle_and_relayout(self):
        """Étapes 3 à 5 : ce qu'il faut refaire dès que le DOM a changé."""
        style_document(self.dom)
        self.title = self._find_title()
        self.layout_root = build_layout_tree(self.dom)  # nouveaux éléments = nouvelles boîtes : on reconstruit
        self._relayout()

    def _find_title(self):
        def walk(node):
            if isinstance(node, Element):
                if node.tag == "title":
                    return " ".join("".join(c.text for c in node.children if isinstance(c, Text)).split())
                for child in node.children:
                    found = walk(child)
                    if found is not None:
                        return found
        return walk(self.dom) or ""

    def _relayout(self):
        """Étapes 4 et 5 : la seule chose qui dépend de la largeur de la fenêtre."""
        layout(self.layout_root, self.viewport_width, self.measure)
        self.display_list = build_display_list(self.layout_root)
        self.scroll_y = min(self.scroll_y, self.max_scroll)  # la page a pu raccourcir

    # -- JavaScript ----------------------------------------------------------------------------------------

    def _ensure_runtime(self):
        if self._runtime is None:
            self._runtime = js.JSRuntime(self.dom, clock=self.clock,
                                         console_sink=lambda level, text: self.console_messages.append((level, text)))
        return self._runtime

    def _run_scripts(self):
        scripts = [e for e in _elements(self.dom) if e.tag == "script" and _is_javascript(e)]
        if not scripts:
            return  # pas de script : on ne crée même pas d'interpréteur
        runtime = self._ensure_runtime()
        for script in scripts:  # liste figée : un script ajouté par innerHTML n'est pas exécuté
            code = self._script_source(script, runtime)
            if code is not None:
                runtime.run(code)
        runtime.dirty = False  # la page n'a pas encore été rendue : rien à rafraîchir

    def _script_source(self, script, runtime):
        src = script.attributes.get("src")
        if src is None:
            return "".join(c.text for c in script.children if isinstance(c, Text))
        try:
            return self.fetcher(urljoin(self.url, src)).text
        except NetworkError as error:
            runtime.log("error", f"script {src} : {error}")
            return None

    def tick(self):
        """À appeler régulièrement (la fenêtre le fait toutes les 50 ms) : exécute les setTimeout échus.

        Renvoie True si le DOM a changé et que la page a été redessinée (il faut alors redessiner l'écran).
        """
        if self._runtime is None:
            return False
        self._runtime.tick()
        return self._flush_js_changes()

    def evaluate(self, code):
        """Exécute du JS dans le contexte de la page courante (une « console »). Renvoie sa valeur."""
        if not self.javascript or self.dom is None:
            return None
        value = self._ensure_runtime().run(code)
        self._flush_js_changes()
        return value

    def _flush_js_changes(self):
        if self._runtime is None or not self._runtime.dirty:
            return False
        self._runtime.dirty = False
        self._restyle_and_relayout()  # le scroll est conservé (et recadré si la page a raccourci)
        return True

    def drain_console(self):
        messages, self.console_messages = self.console_messages, []
        return messages

    # -- fenêtre ----------------------------------------------------------------------------------

    def resize(self, width, height):
        width_changed = width != self.viewport_width
        self.viewport_width, self.viewport_height = width, height
        if self.layout_root is None:
            return
        if width_changed:
            self._relayout()  # reflow : le même arbre de boîtes, autre largeur
        else:
            self.scroll_y = min(self.scroll_y, self.max_scroll)  # la hauteur seule ne change aucune boîte

    # -- scroll -------------------------------------------------------------------------------------

    @property
    def document_height(self):
        return self.display_list.height if self.display_list else 0.0

    @property
    def max_scroll(self):
        return max(0.0, self.document_height - self.viewport_height)

    def scroll_to(self, y):
        self.scroll_y = max(0.0, min(float(y), self.max_scroll))

    def scroll_by(self, dy):
        self.scroll_to(self.scroll_y + dy)

    def scrollbar_range(self):
        """(début, fin) de la partie visible, entre 0 et 1, au format de tkinter.Scrollbar.set."""
        height = self.document_height
        if height <= self.viewport_height:
            return 0.0, 1.0
        return self.scroll_y / height, (self.scroll_y + self.viewport_height) / height

    def visible_commands(self):
        """Seulement ce qu'il faut envoyer au Canvas pour la position de scroll actuelle."""
        if self.display_list is None:
            return []
        return self.display_list.visible(self.scroll_y, self.scroll_y + self.viewport_height)

    # -- liens ----------------------------------------------------------------------------------------

    def link_at(self, x, y):
        """URL absolue du lien sous le point (x, y) de la FENÊTRE, ou None (pas un lien, ou un lien sans effet)."""
        if self.display_list is None:
            return None
        region = self.display_list.link_at(x, y + self.scroll_y)
        if region is None or region.href.strip().startswith("#"):  # `#ancre` : même page, rien à charger
            return None
        target = urljoin(self.url, region.href)  # `/a`, `b.html`, `../c` : relatifs à la page courante
        return target if urlsplit(target).scheme in ("http", "https") else None  # exclut #ancre, mailto:, javascript:

    def click(self, x, y):
        """Clic à (x, y) dans la fenêtre. Renvoie l'URL chargée, ou None si rien ne s'est passé."""
        target = self.link_at(x, y)
        if target is not None:
            self.load(target)
        return target
