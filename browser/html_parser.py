"""Étape 2 : le parseur HTML (texte brut -> arbre de nœuds).

Deux étages, comme dans un vrai navigateur :

    texte HTML ──► Tokenizer ──► TreeBuilder ──► arbre de Text / Element
                   (machine à    (pile des
                    états sur     éléments
                    les caractères) ouverts)

- Le `Tokenizer` lit le HTML caractère par caractère. Il ne sait RIEN de la
  structure : il reconnaît seulement « une balise ouvrante », « une balise
  fermante », « du texte ». Son état courant (`State`) dit où il en est.
- Le `TreeBuilder` reçoit ces événements et maintient la pile des éléments
  encore ouverts. C'est lui qui devine les balises oubliées (`<p>`, `<li>`...).

Usage à la main :
    python -m browser.html_parser page.html
    python -m browser.html_parser http://example.org/
    echo '<p>a<p>b' | python -m browser.html_parser -

Simplifications assumées (le vrai algorithme HTML5 fait ~80 états et des
centaines de règles) :
- pas de <html>/<head>/<body> ajoutés automatiquement : la racine est un
  nœud factice "#document" ;
- commentaires, <!doctype> et instructions <? ?> sont lus puis jetés
  (l'arbre ne contient que des Text et des Element) ;
- `<x/>` est traité comme auto-fermant pour N'IMPORTE QUELLE balise (HTML5
  ne le fait que pour les éléments « void ») ;
- pas de réouverture des balises de formatage (`<b><p>x</b>y` : le `y` n'est
  pas remis en gras) ;
- seules `<script>` et `<style>` ont un contenu « brut » ;
- les entités (`&amp;`...) sont décodées avec `html.unescape` de la stdlib.
"""

import argparse
import html
import json
import sys
from enum import Enum, auto


# --------------------------------------------------------------------------
# Les nœuds de l'arbre
# --------------------------------------------------------------------------

class Node:
    parent = None  # rempli quand le nœud est rattaché à un Element


class Text(Node):
    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return f"Text({self.text!r})"


class Element(Node):
    def __init__(self, tag, attributes=None):
        self.tag = tag
        self.attributes = dict(attributes or {})
        self.children = []
        self.style = {}  # styles calculés : rempli par browser.css.compute_styles (étape 3)

    def append(self, child):
        child.parent = self
        self.children.append(child)

    def __repr__(self):
        return f"Element({self.tag!r}, {self.attributes!r}, {len(self.children)} enfants)"


# --------------------------------------------------------------------------
# Sous-étape 2a-2c : le tokenizer (machine à états)
# --------------------------------------------------------------------------

WHITESPACE = " \t\n\r\f"
RAW_TEXT_TAGS = {"script", "style"}  # contenu = texte brut jusqu'à </script>


class State(Enum):
    DATA = auto()                    # du texte normal
    TAG_OPEN = auto()                # on vient de lire '<'
    END_TAG_OPEN = auto()            # on vient de lire '</'
    TAG_NAME = auto()                # dans le nom de la balise
    BEFORE_ATTR_NAME = auto()        # entre deux attributs
    ATTR_NAME = auto()               # dans un nom d'attribut
    AFTER_ATTR_NAME = auto()         # après le nom, on attend '=' ou la suite
    BEFORE_ATTR_VALUE = auto()       # après '='
    ATTR_VALUE_DOUBLE = auto()       # dans "..."
    ATTR_VALUE_SINGLE = auto()       # dans '...'
    ATTR_VALUE_UNQUOTED = auto()     # dans une valeur sans guillemets
    AFTER_ATTR_VALUE_QUOTED = auto() # juste après le guillemet fermant
    SELF_CLOSING = auto()            # on vient de lire '/' dans une balise
    MARKUP_DECLARATION = auto()      # après '<!' : commentaire ou doctype ?
    COMMENT = auto()                 # dans <!-- ... -->
    BOGUS_COMMENT = auto()           # <!doctype ...>, <? ... ?>, </ ... > : ignoré jusqu'à '>'
    RAW_TEXT = auto()                # contenu de <script> / <style>


class Tokenizer:
    """Lit le HTML et appelle `builder.text / start_tag / end_tag`."""

    def __init__(self, source, builder):
        self.src = source
        self.builder = builder
        self.i = 0                    # position du caractère en cours
        self.state = State.DATA
        self.text = []                # texte accumulé, pas encore envoyé au builder
        self.raw_tag = None           # "script" pendant qu'on lit son contenu brut
        self._reset_tag()
        self._handlers = {
            State.DATA: self._data,
            State.TAG_OPEN: self._tag_open,
            State.END_TAG_OPEN: self._end_tag_open,
            State.TAG_NAME: self._tag_name,
            State.BEFORE_ATTR_NAME: self._before_attr_name,
            State.ATTR_NAME: self._attr_name,
            State.AFTER_ATTR_NAME: self._after_attr_name,
            State.BEFORE_ATTR_VALUE: self._before_attr_value,
            State.ATTR_VALUE_DOUBLE: self._attr_value_quoted('"'),
            State.ATTR_VALUE_SINGLE: self._attr_value_quoted("'"),
            State.ATTR_VALUE_UNQUOTED: self._attr_value_unquoted,
            State.AFTER_ATTR_VALUE_QUOTED: self._after_attr_value_quoted,
            State.SELF_CLOSING: self._self_closing,
            State.MARKUP_DECLARATION: self._markup_declaration,
            State.COMMENT: self._comment,
            State.BOGUS_COMMENT: self._bogus_comment,
            State.RAW_TEXT: self._raw_text,
        }

    # -- boucle principale --------------------------------------------------

    def run(self):
        # Un seul `while` : à chaque tour, le handler de l'état courant regarde UN
        # caractère, change éventuellement `self.state`, puis on avance.
        # Un handler qui veut « relire » le caractère (reconsume) fait `self.i -= 1`.
        while self.i < len(self.src):
            self._handlers[self.state](self.src[self.i])
            self.i += 1
        # Fin du fichier : une balise commencée mais jamais fermée (`<div cla`) est
        # abandonnée, mais le texte qui la précédait est gardé.
        if self.state is State.TAG_OPEN:
            self.text.append("<")  # un '<' final n'était que du texte
        self._flush_text()

    # -- la balise en cours de construction ----------------------------------

    def _reset_tag(self):
        self.tag_name = []
        self.is_end_tag = False
        self.attrs = {}
        self.attr_name = None   # attribut commencé mais pas encore enregistré
        self.attr_value = []

    def _start_tag(self, first_char, is_end):
        self._reset_tag()
        self.is_end_tag = is_end
        self.tag_name.append(first_char.lower())  # HTML ne distingue pas la casse des balises
        self.state = State.TAG_NAME

    def _start_attr(self, first_char):
        self._finish_attr()
        self.attr_name = [first_char.lower()]
        self.attr_value = []

    def _finish_attr(self):
        if self.attr_name is None:
            return
        name = "".join(self.attr_name)
        # Doublon (`<a x=1 x=2>`) : le premier gagne, comme dans les navigateurs.
        self.attrs.setdefault(name, html.unescape("".join(self.attr_value)))
        self.attr_name = None
        self.attr_value = []

    def _emit_tag(self, self_closing=False):
        self._finish_attr()
        self._flush_text()  # le texte lu avant la balise passe AVANT elle dans l'arbre
        name = "".join(self.tag_name)
        if self.is_end_tag:
            self.builder.end_tag(name)
            self.raw_tag = None
            self.state = State.DATA
        else:
            self.builder.start_tag(name, self.attrs, self_closing)
            if name in RAW_TEXT_TAGS and not self_closing:
                self.raw_tag = name
                self.state = State.RAW_TEXT
            else:
                self.state = State.DATA
        self._reset_tag()

    def _flush_text(self):
        if self.text:
            raw = "".join(self.text)
            self.text = []
            # Dans <script>/<style>, `&lt;` reste `&lt;` : pas de décodage.
            self.builder.text(raw if self.raw_tag else html.unescape(raw))

    # -- les états ---------------------------------------------------------

    def _data(self, c):
        if c == "<":
            self.state = State.TAG_OPEN
        else:
            self.text.append(c)

    def _tag_open(self, c):
        if c == "!":
            self.state = State.MARKUP_DECLARATION
        elif c == "/":
            self.state = State.END_TAG_OPEN
        elif c == "?":
            self.state = State.BOGUS_COMMENT
        elif c.isalpha():
            self._start_tag(c, is_end=False)
        else:
            # `1 < 2` : ce '<' n'ouvrait rien, c'est du texte. On relit `c` en DATA.
            self.text.append("<")
            self.state = State.DATA
            self.i -= 1

    def _end_tag_open(self, c):
        if c.isalpha():
            self._start_tag(c, is_end=True)
        elif c == ">":
            self.state = State.DATA  # `</>` : ignoré
        else:
            self.state = State.BOGUS_COMMENT

    def _tag_name(self, c):
        if c in WHITESPACE:
            self.state = State.BEFORE_ATTR_NAME
        elif c == "/":
            self.state = State.SELF_CLOSING
        elif c == ">":
            self._emit_tag()
        else:
            self.tag_name.append(c.lower())

    def _before_attr_name(self, c):
        if c in WHITESPACE:
            return
        if c == "/":
            self.state = State.SELF_CLOSING
        elif c == ">":
            self._emit_tag()
        else:
            self._start_attr(c)
            self.state = State.ATTR_NAME

    def _attr_name(self, c):
        if c in WHITESPACE:
            self.state = State.AFTER_ATTR_NAME
        elif c in "/>":
            self.state = State.AFTER_ATTR_NAME
            self.i -= 1  # AFTER_ATTR_NAME sait gérer '/' et '>'
        elif c == "=":
            self.state = State.BEFORE_ATTR_VALUE
        else:
            self.attr_name.append(c.lower())

    def _after_attr_name(self, c):
        if c in WHITESPACE:
            return
        if c == "=":
            self.state = State.BEFORE_ATTR_VALUE
        elif c == "/":
            self._finish_attr()  # attribut booléen : `<input disabled/>`
            self.state = State.SELF_CLOSING
        elif c == ">":
            self._emit_tag()
        else:
            self._start_attr(c)  # `<input disabled name=x>` : un nouvel attribut commence
            self.state = State.ATTR_NAME

    def _before_attr_value(self, c):
        if c in WHITESPACE:
            return
        if c == '"':
            self.state = State.ATTR_VALUE_DOUBLE
        elif c == "'":
            self.state = State.ATTR_VALUE_SINGLE
        elif c == ">":
            self._emit_tag()  # `<a href=>` : valeur vide
        else:
            self.state = State.ATTR_VALUE_UNQUOTED
            self.i -= 1

    def _attr_value_quoted(self, quote):
        # Fabrique le handler de "..." ou '...' : seul le guillemet de fin change.
        def handler(c):
            if c == quote:
                self.state = State.AFTER_ATTR_VALUE_QUOTED
            else:
                self.attr_value.append(c)
        return handler

    def _attr_value_unquoted(self, c):
        if c in WHITESPACE:
            self._finish_attr()
            self.state = State.BEFORE_ATTR_NAME
        elif c == ">":
            self._emit_tag()
        else:
            self.attr_value.append(c)

    def _after_attr_value_quoted(self, c):
        self._finish_attr()
        if c in WHITESPACE:
            self.state = State.BEFORE_ATTR_NAME
        elif c == "/":
            self.state = State.SELF_CLOSING
        elif c == ">":
            self._emit_tag()
        else:
            self.state = State.BEFORE_ATTR_NAME  # `a="1"b="2"` : on relit `b` comme un attribut
            self.i -= 1

    def _self_closing(self, c):
        if c == ">":
            self._emit_tag(self_closing=True)
        else:
            self.state = State.BEFORE_ATTR_NAME  # un '/' isolé dans la balise : on l'ignore
            self.i -= 1

    def _markup_declaration(self, c):
        if self.src.startswith("--", self.i):
            self.i += 1  # on consomme le 2e '-' : `<!--`
            self.state = State.COMMENT
        else:
            self.state = State.BOGUS_COMMENT  # <!doctype html>, <![CDATA[ ... ]]>

    def _comment(self, c):
        if self.src.startswith("-->", self.i):
            self.i += 2
            self.state = State.DATA

    def _bogus_comment(self, c):
        if c == ">":
            self.state = State.DATA

    def _raw_text(self, c):
        # Dans <script>, '<' n'est PAS une balise, sauf la balise fermante `</script`.
        if c == "<" and self._at_raw_end_tag():
            self.i += 1  # on saute '<' puis (via la boucle) '/' : on reprend sur le nom de la balise
            self.state = State.END_TAG_OPEN
        else:
            self.text.append(c)

    def _at_raw_end_tag(self):
        closing = "</" + self.raw_tag
        end = self.i + len(closing)
        if self.src[self.i:end].lower() != closing:
            return False
        # `</scripty>` n'est pas la fin de <script> : il faut un séparateur ensuite.
        return end >= len(self.src) or self.src[end] in WHITESPACE + "/>"


# --------------------------------------------------------------------------
# Sous-étape 2a-2d : le constructeur d'arbre (pile des éléments ouverts)
# --------------------------------------------------------------------------

# Éléments « void » : jamais de contenu, jamais de balise fermante (`<br>`, `<img src=x>`).
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "source", "track", "wbr"}

# Balises qui, en s'ouvrant, ferment un <p> resté ouvert (`<p>a<div>` : le div n'est pas dans le p).
CLOSES_P = {"p", "address", "article", "aside", "blockquote", "details", "div", "dl",
            "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
            "h5", "h6", "header", "hr", "li", "dt", "dd", "main", "menu", "nav", "ol",
            "pre", "section", "table", "ul"}
P_BOUNDARIES = {"table", "td", "th", "caption", "button"}  # on ne cherche pas un <p> au-delà

# nouvelle balise -> (balises qu'elle ferme si elles sont ouvertes, frontières de la recherche).
# Les frontières évitent de fermer l'<li> d'une AUTRE liste : dans
# `<ul><li>a<ul><li>b`, le 2e <li> s'arrête au <ul> et n'attaque pas le 1er <li>.
IMPLIED_END = {
    "li":     ({"li"}, {"ul", "ol"}),
    "dt":     ({"dt", "dd"}, {"dl"}),
    "dd":     ({"dt", "dd"}, {"dl"}),
    "tr":     ({"tr"}, {"table"}),
    "td":     ({"td", "th"}, {"tr", "table"}),
    "th":     ({"td", "th"}, {"tr", "table"}),
    "thead":  ({"thead", "tbody", "tfoot"}, {"table"}),
    "tbody":  ({"thead", "tbody", "tfoot"}, {"table"}),
    "tfoot":  ({"thead", "tbody", "tfoot"}, {"table"}),
    "option": ({"option"}, {"select"}),
}


class TreeBuilder:
    def __init__(self):
        self.root = Element("#document")
        self.stack = [self.root]  # éléments ouverts ; le dernier est le « courant »

    @property
    def current(self):
        return self.stack[-1]

    def text(self, data):
        # Deux morceaux de texte consécutifs (séparés par un commentaire jeté) ne font qu'un nœud.
        children = self.current.children
        if children and isinstance(children[-1], Text):
            children[-1].text += data
        else:
            self.current.append(Text(data))

    def start_tag(self, tag, attributes, self_closing):
        self._close_forgotten(tag)
        element = Element(tag, attributes)
        self.current.append(element)
        if tag not in VOID_TAGS and not self_closing:
            self.stack.append(element)  # les void et `<x/>` n'ont pas d'enfants : pas empilés

    def end_tag(self, tag):
        # On cherche la balise ouverte correspondante ; tout ce qui est au-dessus avait
        # été oublié (`<div><b>x</div>`) et se ferme avec elle.
        for i in range(len(self.stack) - 1, 0, -1):  # 0 exclu : on ne ferme jamais #document
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
        # Aucune correspondance (`</p>` orphelin, `</br>`) : ignorée.

    def _close_forgotten(self, tag):
        if tag in CLOSES_P:
            self._pop_to_nearest({"p"}, P_BOUNDARIES)
        rule = IMPLIED_END.get(tag)
        if rule:
            self._pop_to_nearest(*rule)

    def _pop_to_nearest(self, targets, boundaries):
        for i in range(len(self.stack) - 1, 0, -1):
            name = self.stack[i].tag
            if name in targets:
                del self.stack[i:]  # ferme la cible ET tout ce qui était ouvert dedans
                return
            if name in boundaries:
                return


# --------------------------------------------------------------------------
# API publique + affichage
# --------------------------------------------------------------------------

def parse(source):
    """HTML (str) -> Element racine "#document" dont les enfants sont l'arbre."""
    builder = TreeBuilder()
    Tokenizer(source, builder).run()
    return builder.root


def to_html(node):
    """Nœud -> HTML (le contraire de `parse`). Sert à `element.innerHTML` côté JavaScript.

    Le texte est ré-échappé (`<` -> `&lt;`), sauf dans <script>/<style> dont le contenu est brut.
    Les éléments sans contenu (`<br>`) n'ont pas de balise fermante.
    """
    if isinstance(node, Text):
        raw = node.parent is not None and node.parent.tag in RAW_TEXT_TAGS
        return node.text if raw else html.escape(node.text, quote=False)
    attrs = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in node.attributes.items())
    if node.tag in VOID_TAGS:
        return f"<{node.tag}{attrs}>"
    return f"<{node.tag}{attrs}>{inner_html(node)}</{node.tag}>"


def inner_html(element):
    return "".join(to_html(child) for child in element.children)


def dump_tree(node, *, show_whitespace=False, annotate=None):
    """Représentation indentée de l'arbre, pour déboguer.

    #document
      <ul class="menu">
        <li>
          "Accueil"

    Les textes uniquement faits d'espaces (indentation du fichier source) sont
    masqués sauf si `show_whitespace=True`. `annotate(element) -> str` permet
    d'ajouter un commentaire en fin de ligne d'un élément (l'étape 3 y affiche les styles).
    """
    lines = []

    def walk(n, depth):
        pad = "  " * depth
        if isinstance(n, Text):
            if show_whitespace or n.text.strip():
                lines.append(f"{pad}{json.dumps(n.text, ensure_ascii=False)}")
            return
        if n.tag == "#document":
            lines.append(f"{pad}#document")
        else:
            attrs = "".join(f' {k}="{v}"' for k, v in n.attributes.items())
            note = annotate(n) if annotate else ""
            lines.append(f"{pad}<{n.tag}{attrs}>{'  ' + note if note else ''}")
        for child in n.children:
            walk(child, depth + 1)

    walk(node, 0)
    return "\n".join(lines)


def load_source(source):
    """URL http(s), chemin de fichier ou '-' (stdin) -> texte HTML."""
    if source == "-":
        return sys.stdin.read()
    if source.startswith(("http://", "https://")):
        from browser.network import fetch
        # Simplification : on suppose de l'UTF-8 (l'étape 1 ne lit pas le charset).
        return fetch(source).body.decode("utf-8", errors="replace")
    with open(source, encoding="utf-8", errors="replace") as f:
        return f.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Affiche l'arbre HTML d'une page, d'un fichier ou de stdin.")
    ap.add_argument("source", help="URL http(s), chemin de fichier, ou '-' pour stdin")
    ap.add_argument("-w", "--whitespace", action="store_true", help="afficher aussi les textes blancs")
    args = ap.parse_args(argv)
    print(dump_tree(parse(load_source(args.source)), show_whitespace=args.whitespace))


if __name__ == "__main__":
    main()
