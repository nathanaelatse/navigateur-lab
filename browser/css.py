"""Étape 3 : CSS (texte CSS -> règles -> style calculé de chaque élément).

    texte CSS ──► parse_stylesheet ──► règles ──┐
                                                ├─► compute_styles ──► element.style
    arbre HTML (étape 2) ───────────────────────┘     cascade + héritage + défauts

Trois sous-étapes :
  3a  le parseur : règles, sélecteurs (balise, .classe, #id, composés `p.a#b`, `*`)
  3b  la cascade : quelle déclaration gagne quand plusieurs règles visent le même élément
  3c  l'héritage et les valeurs par défaut (dont une feuille de style navigateur minimale)

Usage à la main :
    python -m browser.css page.html          # arbre HTML + styles qui diffèrent des défauts

Simplifications assumées :
- sélecteurs : un seul « compound » (`p.intro#x`). Pas de combinateurs (`div p`, `a > b`),
  d'attributs (`[x]`) ni de pseudo-classes (`:hover`) : un tel sélecteur est ignoré, les autres
  sélecteurs de la même liste (`a, div p`) restent actifs ;
- pas de `!important` (le mot-clé est lu puis ignoré) ni d'attribut `style=""` ;
- seuls `margin`, `padding`, `border-width` et `border` sont développés (`margin: 1px 2px` -> 4
  propriétés). Pour la bordure, style et couleur sont communs aux 4 côtés ; `border-top: ...`,
  `background`, `font`... restent des propriétés brutes ;
- les valeurs sont des chaînes non validées, sauf `font-size` (px, em, %, pt) qui est
  converti en pixels pour que l'héritage des tailles relatives fonctionne ;
- @media, @import, @font-face... sont sautés ; <link rel=stylesheet> n'est pas téléchargé.
"""

import argparse
import re
from dataclasses import dataclass

from browser.html_parser import Element, Text, dump_tree, load_source, parse


# --------------------------------------------------------------------------
# Sous-étape 3a : les structures et le parseur
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Selector:
    """Un sélecteur simple composé : `p.intro#top` -> tag="p", ids=("top",), classes=("intro",).

    `tag=None` veut dire « n'importe quelle balise » (`*`, ou `.intro` tout court).
    """
    tag: str = None
    ids: tuple = ()
    classes: tuple = ()

    def matches(self, element):
        if self.tag is not None and element.tag != self.tag:
            return False
        if any(element.attributes.get("id") != i for i in self.ids):
            return False
        element_classes = element.attributes.get("class", "").split()
        return all(c in element_classes for c in self.classes)


@dataclass
class Rule:
    selector: Selector       # UN sélecteur : `a, b {...}` donne deux Rule
    declarations: dict       # propriété -> valeur (chaînes)
    origin: str = "author"   # "user-agent" ou "author"


@dataclass
class Stylesheet:
    rules: list
    origin: str = "author"


_SELECTOR_RE = re.compile(r"(\*|[A-Za-z][\w-]*)?((?:[.#][A-Za-z_][\w-]*)*)")
_SIMPLE_PART_RE = re.compile(r"([.#])([\w-]+)")


def parse_selector(text):
    """'p.a#x' -> Selector ; None si le sélecteur n'est pas supporté ou est invalide."""
    text = text.strip()
    m = _SELECTOR_RE.fullmatch(text)
    if not text or not m:  # `div p`, `a>b`, `[x]`, `:hover`... : hors périmètre
        return None
    tag, rest = m.groups()
    parts = _SIMPLE_PART_RE.findall(rest)
    return Selector(
        tag=None if tag in (None, "*") else tag.lower(),  # HTML : balises insensibles à la casse
        ids=tuple(name for kind, name in parts if kind == "#"),
        classes=tuple(name for kind, name in parts if kind == "."),  # les classes, elles, sont sensibles
    )


def parse_selector_list(text):
    """'p, .a, #x' -> [Selector, ...] ; None si UN des sélecteurs n'est pas supporté (l'appelant décide).

    Contrairement à `parse_stylesheet` (qui ignore le sélecteur fautif et garde les autres, comme
    le fait CSS), `querySelectorAll` veut une réponse tout ou rien.
    """
    selectors = [parse_selector(part) for part in _split_top(text, ",")]
    return None if any(s is None for s in selectors) else selectors


def _skip_string(text, i):
    """`text[i]` est un guillemet ; renvoie l'index juste après la chaîne fermée."""
    quote = text[i]
    i += 1
    while i < len(text) and text[i] != quote:
        i += 2 if text[i] == "\\" else 1
    return i + 1


def _find_top(text, start, targets):
    """Premier caractère de `targets` hors guillemets et hors parenthèses (`url(a;b)`), sinon -1."""
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and c in targets:
            return i
        i += 1
    return -1


def _split_top(text, sep):
    parts, start = [], 0
    while True:
        j = _find_top(text, start, sep)
        if j == -1:
            parts.append(text[start:])
            return parts
        parts.append(text[start:j])
        start = j + 1


def _block_end(text, open_index):
    """Index de l'accolade fermante qui correspond à `text[open_index] == '{'` (blocs imbriqués OK)."""
    depth = 0
    i = open_index
    while i < len(text):
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)  # bloc jamais fermé : il court jusqu'à la fin, comme dans les navigateurs


_COMMENT_RE = re.compile(r"/\*.*?(\*/|$)", re.DOTALL)
_IMPORTANT_RE = re.compile(r"\s*!\s*important\s*$", re.IGNORECASE)
_PROPERTY_RE = re.compile(r"-?[a-z][a-z0-9-]*")
SIDES = ("top", "right", "bottom", "left")


def _expand_box_shorthand(prop, value, suffix=""):
    """`margin: 1px 2px` -> {margin-top: 1px, margin-right: 2px, margin-bottom: 1px, margin-left: 2px}.

    `suffix` sert à `border-width` : ("border", "-width") -> border-top-width, border-right-width...
    """
    v = value.split()
    if not 1 <= len(v) <= 4:
        return {}
    top, right, bottom, left = {
        1: lambda: (v[0], v[0], v[0], v[0]),
        2: lambda: (v[0], v[1], v[0], v[1]),
        3: lambda: (v[0], v[1], v[2], v[1]),
        4: lambda: (v[0], v[1], v[2], v[3]),
    }[len(v)]()
    return dict(zip((f"{prop}-{side}{suffix}" for side in SIDES), (top, right, bottom, left)))


BORDER_STYLES = {"none", "hidden", "dotted", "dashed", "solid", "double", "groove", "ridge", "inset", "outset"}
_BORDER_WIDTH_RE = re.compile(r"(\d*\.?\d+(px|em|pt)?|thin|medium|thick)")


def _expand_border(value):
    """`border: 1px solid red` -> largeur des 4 côtés + style + couleur (communs aux 4 côtés).

    Comme en CSS, un raccourci REMET À ZÉRO ce qu'il ne mentionne pas : sans style
    (`border: 1px red`) le style vaut `none` et la bordure ne s'affiche pas.
    """
    width, style, color = "medium", "none", "currentcolor"
    for token in _split_top(" ".join(value.split()), " "):
        t = token.lower()
        if t in BORDER_STYLES:
            style = t
        elif _BORDER_WIDTH_RE.fullmatch(t):
            width = t
        elif t:
            color = token
    declarations = {f"border-{side}-width": width for side in SIDES}
    declarations["border-style"] = style
    declarations["border-color"] = color
    return declarations


def parse_declarations(body):
    """'color: red; margin: 0 auto' -> {'color': 'red', 'margin-top': '0', ...}.

    Une déclaration invalide est sautée SANS gêner les suivantes (récupération d'erreur).
    """
    declarations = {}
    for chunk in _split_top(body, ";"):
        name, colon, value = chunk.partition(":")
        name = name.strip().lower()
        value = _IMPORTANT_RE.sub("", value.strip())
        if not colon or not value or not _PROPERTY_RE.fullmatch(name):
            continue
        if name in ("margin", "padding"):
            declarations.update(_expand_box_shorthand(name, value))
        elif name == "border-width":
            declarations.update(_expand_box_shorthand("border", value, "-width"))
        elif name == "border":
            declarations.update(_expand_border(value))
        else:
            declarations[name] = value  # une déclaration plus tardive du même bloc écrase l'autre
    return declarations


def parse_stylesheet(text, origin="author"):
    text = _COMMENT_RE.sub("", text)
    rules = []
    i = 0
    while i < len(text):
        stop = _find_top(text, i, "{;")
        if stop == -1:
            break  # reste de texte sans bloc : ignoré
        prelude = text[i:stop].strip()
        if text[stop] == ";":  # `@import url(x);`, `@charset "utf-8";`
            i = stop + 1
            continue
        end = _block_end(text, stop)
        body = text[stop + 1:end]
        i = end + 1
        if prelude.startswith("@"):
            continue  # @media { ... } et consorts : bloc entier sauté
        declarations = parse_declarations(body)
        for selector_text in _split_top(prelude, ","):
            selector = parse_selector(selector_text)
            if selector is not None:
                # Chaque sélecteur reçoit sa propre copie : `a, b {...}` = deux règles indépendantes.
                rules.append(Rule(selector, dict(declarations), origin))
    return Stylesheet(rules, origin)


def extract_stylesheets(root):
    """Les feuilles de style écrites dans les <style> du document, dans l'ordre du document."""
    sheets = []

    def walk(node):
        if not isinstance(node, Element):
            return
        if node.tag == "style":
            css = "".join(c.text for c in node.children if isinstance(c, Text))
            sheets.append(parse_stylesheet(css))
        for child in node.children:
            walk(child)

    walk(root)
    return sheets


# --------------------------------------------------------------------------
# Sous-étape 3b : la cascade
# --------------------------------------------------------------------------

def specificity(selector):
    """Spécificité d'un `Selector` : un tuple (a, b, c) comparé de gauche à droite.

        a = nombre d'identifiants   (#id)
        b = nombre de classes       (.classe)
        c = nombre de balises       (p, div...)      -- `*` ne compte pour rien

    `#a` (1, 0, 0) bat n'importe quel nombre de classes (0, 9, 0), qui bat n'importe quel nombre
    de balises (0, 0, 9). C'est pourquoi on renvoie un TUPLE et pas un nombre « a*100 + b*10 + c »
    (qui se tromperait à 10 classes). Python compare les tuples dans l'ordre : `(0, 1, 0) > (0, 0, 9)`.

    Un `Selector` stocke déjà ses ingrédients : `tag` vaut None pour `*` (et pour `.a` sans balise),
    donc il suffit de les compter.
    """
    return (len(selector.ids), len(selector.classes), 1 if selector.tag is not None else 0)


ORIGIN_RANK = {"user-agent": 0, "author": 1}  # le site l'emporte toujours sur le navigateur


def cascade(element, rules):
    """Déclarations qui s'appliquent à `element`, une fois les conflits résolus.

    On trie les règles qui correspondent de la plus faible à la plus forte priorité
    (origine, puis spécificité, puis ordre d'apparition) et on les applique dans cet ordre :
    la dernière écrase les précédentes. Pas besoin de comparer propriété par propriété.
    """
    matched = []
    for order, rule in enumerate(rules):  # `order` = position dans la liste : à égalité, la dernière gagne
        if rule.selector.matches(element):
            matched.append(((ORIGIN_RANK[rule.origin], specificity(rule.selector), order), rule))
    matched.sort(key=lambda pair: pair[0])
    cascaded = {}
    for _, rule in matched:
        cascaded.update(rule.declarations)
    return cascaded


# --------------------------------------------------------------------------
# Sous-étape 3c : héritage, valeurs par défaut, feuille de style du navigateur
# --------------------------------------------------------------------------

# Valeur initiale de chaque propriété connue.
INITIAL = {
    "display": "inline",
    "color": "black",
    "background-color": "transparent",
    "font-family": "serif",
    "font-size": "16px",
    "font-style": "normal",
    "font-weight": "normal",
    "line-height": "normal",
    "text-align": "left",
    "text-decoration": "none",
    "visibility": "visible",
    "white-space": "normal",
    "width": "auto",
    "height": "auto",
    **{f"margin-{side}": "0" for side in SIDES},
    **{f"padding-{side}": "0" for side in SIDES},
    **{f"border-{side}-width": "medium" for side in SIDES},  # sans effet tant que border-style vaut none
    "border-style": "none",
    "border-color": "currentcolor",
}

# Propriétés qui passent du parent à l'enfant quand l'enfant ne dit rien.
# (La couleur d'un paragraphe vient de <body>, mais sa marge ne se propage pas.)
INHERITED = {"color", "font-family", "font-size", "font-style", "font-weight",
             "line-height", "text-align", "visibility", "white-space"}

USER_AGENT_CSS = """
/* Ce que le navigateur fait quand la page ne dit rien : une version minuscule des
   feuilles de style par défaut de Firefox/Chrome. */
head, script, style, title, meta, link { display: none }

html, body, div, p, h1, h2, h3, h4, h5, h6, ul, ol, pre, blockquote,
header, footer, section, article, nav, main, form, table { display: block }
li { display: list-item }

body { margin: 8px }
p { margin-top: 16px; margin-bottom: 16px }
ul, ol { margin-top: 16px; margin-bottom: 16px; padding-left: 40px }
blockquote { margin: 16px 40px }

h1 { font-size: 2em;    font-weight: bold; margin-top: 21px; margin-bottom: 21px }
h2 { font-size: 1.5em;  font-weight: bold; margin-top: 20px; margin-bottom: 20px }
h3 { font-size: 1.17em; font-weight: bold; margin-top: 19px; margin-bottom: 19px }
h4 { font-size: 1em;    font-weight: bold; margin-top: 21px; margin-bottom: 21px }
h5 { font-size: 0.83em; font-weight: bold; margin-top: 22px; margin-bottom: 22px }
h6 { font-size: 0.67em; font-weight: bold; margin-top: 25px; margin-bottom: 25px }

b, strong { font-weight: bold }
i, em, cite { font-style: italic }
a { color: blue; text-decoration: underline }
pre, code { font-family: monospace }
pre { white-space: pre }
"""

USER_AGENT_SHEET = parse_stylesheet(USER_AGENT_CSS, origin="user-agent")


_FONT_SIZE_RE = re.compile(r"(\d*\.?\d+)(px|em|%|pt)?")


def resolve_font_size(value, parent_px):
    """'2em' / '150%' / '12pt' / '20px' -> pixels (float), ou None si illisible.

    Les unités relatives se calculent PAR RAPPORT à la taille du parent, c'est pour ça que
    la taille « calculée » est stockée en pixels : un `2em` dans un `2em` fait bien 4x.
    """
    m = _FONT_SIZE_RE.fullmatch(value.strip().lower())
    if not m:
        return None
    number, unit = float(m.group(1)), m.group(2) or "px"
    if unit == "px":
        return number
    if unit == "pt":
        return number * 4 / 3
    return number * parent_px * (1 if unit == "em" else 0.01)


def _px(value):
    return f"{value:g}px"


def _compute_one(element, parent_style, rules):
    cascaded = cascade(element, rules)

    # 1. point de départ : hérité du parent pour les propriétés héritables, sinon valeur initiale.
    style = {prop: parent_style[prop] if prop in INHERITED else initial
             for prop, initial in INITIAL.items()}

    # 2. ce que la cascade a décidé écrase ce point de départ.
    for prop, value in cascaded.items():
        if value.strip().lower() == "inherit":  # « prends celle de mon parent », même si non héritable
            style[prop] = parent_style.get(prop, INITIAL.get(prop))
        elif prop == "font-size":
            px = resolve_font_size(value, float(parent_style["font-size"][:-2]))
            if px is not None:  # illisible : déclaration ignorée, la taille héritée reste
                style[prop] = _px(px)
        else:
            style[prop] = value
    return style


def compute_styles(root, stylesheets=()):
    """Remplit `element.style` (dict propriété -> valeur calculée) pour tous les éléments sous `root`.

    `stylesheets` : feuilles de l'auteur (ex. `extract_stylesheets(root)`), dans l'ordre du document ;
    la feuille du navigateur est ajoutée en tête.
    """
    rules = [rule for sheet in (USER_AGENT_SHEET, *stylesheets) for rule in sheet.rules]
    root.style = dict(INITIAL)  # la racine "#document" n'a pas de parent : tout est par défaut

    def walk(node, parent_style):
        if not isinstance(node, Element):
            return  # un Text n'a pas de style propre : il utilise celui de son parent
        node.style = _compute_one(node, parent_style, rules)
        for child in node.children:
            walk(child, node.style)

    for child in root.children:
        walk(child, root.style)


def style_document(root):
    """Raccourci : lit les <style> du document puis calcule tous les styles."""
    compute_styles(root, extract_stylesheets(root))


# --------------------------------------------------------------------------
# Débogage
# --------------------------------------------------------------------------

def dump_styled_tree(root, *, show_whitespace=False):
    """Arbre HTML où chaque élément montre ses propriétés qui diffèrent de la valeur initiale."""
    def note(element):
        changed = [f"{p}: {v}" for p, v in element.style.items() if INITIAL.get(p) != v]
        return "{" + "; ".join(changed) + "}" if changed else ""
    return dump_tree(root, show_whitespace=show_whitespace, annotate=note)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Affiche l'arbre HTML avec les styles calculés.")
    ap.add_argument("source", help="URL http(s), chemin de fichier, ou '-' pour stdin")
    ap.add_argument("-w", "--whitespace", action="store_true", help="afficher aussi les textes blancs")
    args = ap.parse_args(argv)
    root = parse(load_source(args.source))
    style_document(root)
    print(dump_styled_tree(root, show_whitespace=args.whitespace))


if __name__ == "__main__":
    main()
