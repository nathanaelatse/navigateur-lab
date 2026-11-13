"""Étape 6 (bonus) : JavaScript (interpréteur dukpy + un `document` minimal).

    <script> ─► JSRuntime.run ─► moteur JS (Duktape)  ──call_python──►  ponts Python ──► DOM (étape 2)
                                  son propre tas mémoire   JSON + entiers         │
                                                                                   ▼
        Tab : si `runtime.dirty` ─► styles (3) ─► arbre de boîtes + layout (4) ─► display list (5)

── Pourquoi le JS tourne dans un contexte ISOLÉ ───────────────────────────────────────────

Un script de page vient du serveur d'à côté : c'est du code qu'on n'a pas écrit et auquel on ne
fait pas confiance. L'exécuter « dans » le navigateur, avec accès à ses objets Python, à ses
fichiers et à son réseau, reviendrait à laisser n'importe quel site faire ce qu'il veut sur la
machine de l'utilisateur. On l'exécute donc dans un bac à sable à trois étages :

  1. Un tas mémoire séparé. Le moteur (Duktape) a ses propres objets ; aucune référence ne
     traverse la frontière. Le JS ne peut pas atteindre un objet Python (donc pas non plus
     `obj.__class__.__subclasses__()` et les échappements classiques des bacs à sable Python).
  2. Des capacités explicites. Le JS ne peut faire QUE ce que `JSRuntime` exporte (lire un attribut,
     modifier `innerHTML`...). Pas de fichiers, pas de réseau, pas de processus.
  3. Des copies, pas des références. Ce qui traverse la frontière est du JSON ; un nœud du DOM
     n'est JAMAIS donné au JS, seulement une POIGNÉE (un entier) que le Python sait retraduire.
     Une poignée inconnue ou périmée donne `null`, jamais un plantage ni un accès.
  4. Un contexte par page. Chaque chargement crée un nouvel interpréteur : variables globales,
     timers et nœuds d'une page ne passent pas à la suivante.

L'isolation n'est pas gratuite : elle dépend de ce que l'hôte laisse dans le contexte. Mesuré avec
dukpy 0.6, un contexte neuf expose `require` (qui LIT n'importe quel fichier et en fuit le contenu
dans les messages d'erreur) et `process.env` (toutes les variables d'environnement du navigateur).
`bootstrap` les supprime, et `run` retire `call_python` avant chaque script (dukpy le réinjecte à
chaque `evaljs`). Les tests vérifient chacune de ces fermetures.

Limites connues : pas de limite de temps ni de mémoire (un `while(true){}` gèle le navigateur ;
un vrai navigateur isole en plus par processus et par système d'exploitation).

Simplifications assumées :
- les scripts s'exécutent après le parsing complet : ils voient tout le DOM (un navigateur les
  exécute pendant le parsing, et `document.write` n'existe pas) ;
- API : document.querySelectorAll / querySelector / getElementById / body, et sur un nœud :
  getAttribute / setAttribute / querySelectorAll / querySelector / innerHTML / textContent /
  tagName ; console.log/info/warn/error ; setTimeout / clearTimeout (pas de setInterval) ;
- pas d'événements (onclick, addEventListener), pas de `document.createElement` ni de `fetch` ;
- les sélecteurs sont ceux de l'étape 3 (balise, .classe, #id, composés) ; un sélecteur non supporté
  renvoie une liste vide et écrit un avertissement dans la console ;
- un script inséré par `innerHTML` ne s'exécute pas (comme dans un navigateur).
"""

import json
import time

from browser.css import parse_selector_list
from browser.html_parser import RAW_TEXT_TAGS, Element, Text, inner_html, parse

try:
    import dukpy
except ImportError:  # pip install dukpy
    dukpy = None

AVAILABLE = dukpy is not None

# Ce que le moteur ajoute de lui-même et qu'une page web ne doit JAMAIS voir.
REMOVED_GLOBALS = ("require", "process", "_dukpy_cjs_source_compiles", "_dukpy_eval_cjs_source")

# dukpy réinjecte ces globaux avant CHAQUE évaluation : on les supprime juste avant le code de la page.
HIDE_BRIDGE = "delete this.call_python; delete this.dukpy; "

BOOTSTRAP = """
(function (global, call, rootHandle, removed) {
  removed.forEach(function (name) { delete global[name]; });

  var nodes = {};   // poignée -> objet JS : une même poignée donne toujours le MÊME objet (a === b)
  var timers = {};
  var nextTimer = 1;

  function wrap(handle) {
    if (handle === undefined || handle === null) return null;
    return nodes[handle] || (nodes[handle] = new Node(handle));
  }
  function Node(handle) {
    // Non énumérable : invisible dans `for (k in node)`. Ce n'est qu'un numéro, pas une référence.
    Object.defineProperty(this, "__handle", { value: handle });
  }
  Node.prototype.getAttribute = function (name) {
    var value = call("get_attribute", this.__handle, String(name));
    return value === undefined ? null : value;   // le pont renvoie undefined pour None
  };
  Node.prototype.setAttribute = function (name, value) {
    call("set_attribute", this.__handle, String(name), String(value));
  };
  Node.prototype.querySelectorAll = function (selector) {
    return call("query_all", this.__handle, String(selector)).map(function (h) { return wrap(h); });
  };
  Node.prototype.querySelector = function (selector) {
    return this.querySelectorAll(selector)[0] || null;
  };
  function accessor(name, bridge) {
    Object.defineProperty(Node.prototype, name, {
      get: function () { return call(bridge, this.__handle); },
      set: function (value) { call("set_" + bridge.replace(/^get_/, ""), this.__handle, String(value)); }
    });
  }
  accessor("innerHTML", "get_inner_html");
  accessor("textContent", "get_text_content");
  Object.defineProperty(Node.prototype, "tagName", {
    get: function () { return call("tag_name", this.__handle); }
  });

  var document = {
    querySelectorAll: function (selector) {
      return call("query_all", rootHandle, String(selector)).map(function (h) { return wrap(h); });
    },
    querySelector: function (selector) { return document.querySelectorAll(selector)[0] || null; },
    getElementById: function (id) { return wrap(call("get_by_id", String(id))); }
  };
  Object.defineProperty(document, "body", { get: function () { return document.querySelector("body"); } });

  function logger(level) {
    return function () {
      call("console", level, Array.prototype.map.call(arguments, String).join(" "));
    };
  }
  global.console = { log: logger("log"), info: logger("info"), warn: logger("warn"), error: logger("error") };

  global.setTimeout = function (fn, ms) {
    var id = nextTimer++;
    timers[id] = { fn: fn, args: Array.prototype.slice.call(arguments, 2) };
    call("set_timeout", id, Number(ms) || 0);
    return id;
  };
  global.clearTimeout = function (id) { delete timers[id]; call("clear_timeout", id); };
  global.__fireTimer = function (id) {   // appelée par Python quand l'échéance arrive
    var timer = timers[id];
    delete timers[id];
    if (timer && typeof timer.fn === "function") timer.fn.apply(null, timer.args);
  };

  global.document = document;
  global.window = global;
})(this, call_python, __ROOT__, __REMOVED__);
"""


class JSRuntime:
    """Un interpréteur JS lié à UN document. `dirty` passe à True dès que le JS modifie le DOM."""

    def __init__(self, dom_root, *, clock=time.monotonic, console_sink=None):
        if not AVAILABLE:
            raise RuntimeError("dukpy n'est pas installé (pip install dukpy)")
        self.dom_root = dom_root
        self.dirty = False
        self.console = []            # [(niveau, message)]
        self._console_sink = console_sink
        self._clock = clock
        self._timers = {}            # id -> instant d'échéance
        self._handle_of = {}         # Element -> poignée (identité : Element n'a pas de __eq__)
        self._element_of = {}        # poignée -> Element
        self._interp = dukpy.JSInterpreter()
        for name in ("get_attribute", "set_attribute", "query_all", "get_by_id", "get_inner_html",
                     "set_inner_html", "get_text_content", "set_text_content", "tag_name",
                     "console", "set_timeout", "clear_timeout"):
            self._interp.export_function(name, getattr(self, "_js_" + name))
        bootstrap = BOOTSTRAP.replace("__ROOT__", str(self._handle(dom_root))) \
                             .replace("__REMOVED__", json.dumps(REMOVED_GLOBALS))
        self._interp.evaljs(bootstrap)

    # -- exécuter du code ------------------------------------------------------------------------

    def run(self, code):
        """Exécute `code` dans le contexte de la page. Renvoie la valeur de la dernière expression.

        Une erreur JS (syntaxe, exception, ...) n'arrête PAS le navigateur : elle est écrite dans
        la console et `run` renvoie None, comme un script cassé n'empêche pas d'afficher la page.
        """
        try:
            return self._interp.evaljs(HIDE_BRIDGE + code)
        except dukpy.JSRuntimeError as error:
            self.log("error", str(error).strip().splitlines()[0])
            return None

    def log(self, level, message):
        self.console.append((level, message))
        if self._console_sink is not None:
            self._console_sink(level, message)

    # -- timers ------------------------------------------------------------------------------------

    def tick(self):
        """Exécute les `setTimeout` arrivés à échéance. Renvoie le nombre de callbacks exécutés."""
        now = self._clock()
        due = sorted((when, timer_id) for timer_id, when in self._timers.items() if when <= now)
        fired = 0
        for _, timer_id in due:
            if timer_id in self._timers:  # un callback précédent a pu l'annuler
                del self._timers[timer_id]
                self.run(f"__fireTimer({timer_id})")
                fired += 1
        return fired

    @property
    def pending_timers(self):
        return len(self._timers)

    # -- poignées : le seul moyen de désigner un nœud côté JS -----------------------------------------

    def _handle(self, element):
        handle = self._handle_of.get(element)
        if handle is None:
            handle = len(self._handle_of) + 1
            self._handle_of[element] = handle
            self._element_of[handle] = element
        return handle

    def _element(self, handle):
        try:
            return self._element_of.get(int(handle))
        except (TypeError, ValueError):
            return None  # poignée forgée (« abc », null...) : rien

    def _descendants(self, root):
        """Éléments sous `root` (lui exclu), dans l'ordre du document."""
        stack = list(reversed(root.children))
        while stack:
            node = stack.pop()
            if isinstance(node, Element):
                yield node
                stack.extend(reversed(node.children))

    # -- les ponts : tout ce que le JS peut demander à Python. Ils renvoient du JSON et ne lèvent pas. ----

    def _js_get_attribute(self, handle, name):
        element = self._element(handle)
        return None if element is None else element.attributes.get(str(name).lower())

    def _js_set_attribute(self, handle, name, value):
        element = self._element(handle)
        if element is not None:
            element.attributes[str(name).lower()] = str(value)
            self.dirty = True

    def _js_query_all(self, handle, selector):
        root = self._element(handle)
        if root is None:
            return []
        selectors = parse_selector_list(str(selector))
        if selectors is None:
            self.log("warn", f"sélecteur non supporté : {selector!r}")
            return []
        return [self._handle(e) for e in self._descendants(root) if any(s.matches(e) for s in selectors)]

    def _js_get_by_id(self, element_id):
        for element in self._descendants(self.dom_root):
            if element.attributes.get("id") == str(element_id):
                return self._handle(element)
        return None

    def _js_get_inner_html(self, handle):
        element = self._element(handle)
        return "" if element is None else inner_html(element)

    def _js_set_inner_html(self, handle, markup):
        element = self._element(handle)
        if element is None:
            return
        markup = str(markup)
        if element.tag in RAW_TEXT_TAGS:  # le contenu de <script>/<style> est du texte, pas du HTML
            self._replace_children(element, [Text(markup)] if markup else [])
        else:
            self._replace_children(element, parse(markup).children)

    def _js_get_text_content(self, handle):
        element = self._element(handle)
        if element is None:
            return ""
        return "".join(t.text for t in self._texts(element))

    def _js_set_text_content(self, handle, text):
        element = self._element(handle)
        if element is not None:
            self._replace_children(element, [Text(str(text))] if str(text) else [])

    def _texts(self, element):
        for child in element.children:
            if isinstance(child, Text):
                yield child
            else:
                yield from self._texts(child)

    def _replace_children(self, element, new_children):
        for old in element.children:
            old.parent = None  # détachés : ils ne font plus partie du document
        element.children = []
        for child in list(new_children):
            element.append(child)
        self.dirty = True

    def _js_tag_name(self, handle):
        element = self._element(handle)
        return None if element is None else element.tag.upper()

    def _js_console(self, level, message):
        self.log(str(level), str(message))

    def _js_set_timeout(self, timer_id, delay_ms):
        try:
            self._timers[int(timer_id)] = self._clock() + max(0.0, float(delay_ms)) / 1000
        except (TypeError, ValueError):
            pass

    def _js_clear_timeout(self, timer_id):
        try:
            self._timers.pop(int(timer_id), None)
        except (TypeError, ValueError):
            pass
