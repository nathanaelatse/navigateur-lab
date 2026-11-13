"""Étape 4 : le layout (DOM stylé -> boîtes avec une position et une taille en pixels).

    DOM + styles (étapes 2-3) ──► build_layout_tree ──► arbre de boîtes (sans géométrie)
                                                              │
                                  largeur de la fenêtre ──► layout() ──► x, y, largeur, hauteur partout

Deux phases séparées, et c'est voulu :
  1. `build_layout_tree` décide QUELLES boîtes existent (qui est un bloc, qui est du texte en
     ligne, qui est caché). Elle ne dépend pas de la largeur de la fenêtre.
  2. `layout` décide OÙ elles sont. Elle dépend de la largeur, et on peut la relancer sur le
     même arbre autant de fois qu'on veut.

── Block vs inline ────────────────────────────────────────────────────────────────────────

Un élément `display: block` (div, p, h1, ul...) est une BOÎTE : il prend toute la largeur
disponible de son parent et se place SOUS le précédent. Sa hauteur vient de son contenu.
Il a des marges, un padding et une bordure qui se calculent autour de lui.

Un élément `display: inline` (span, b, a, em...) n'est PAS une boîte : c'est un style appliqué
à des morceaux de texte qui COULENT dans la ligne courante, l'un derrière l'autre, de gauche à
droite. Quand la ligne est pleine, on passe à la suivante. Un `<b>` au milieu d'une phrase peut
donc être coupé en deux lignes ; un `<div>` ne le peut jamais.

Conséquence : le contenu d'un bloc est SOIT une pile de blocs, SOIT des lignes de texte, jamais
un mélange. Si le HTML mélange les deux (`<div>texte<p>bloc</p>suite</div>`), chaque suite
de texte est enveloppée dans une boîte « anonyme » : div[anonyme[texte], p, anonyme[suite]].

    Block <div>              ← pile verticale de blocs
      Block <p>              ← contient des lignes
        Line                 ← une ligne = des fragments de texte côte à côte
          "Hello" "world"    ← chaque fragment garde le style de son élément (gras, taille...)

── Pourquoi une nouvelle largeur déclenche un reflow ─────────────────────────────────────

Une largeur de bloc est calculée à partir de celle de son parent : la fenêtre donne la largeur du
document, qui donne celle de <body>, qui donne celle de chaque <p>... Or la largeur d'un
paragraphe décide où les lignes se coupent, donc combien il y a de lignes, donc sa HAUTEUR. Cette
hauteur décale tout ce qui est en dessous (le bloc suivant est posé à `y` = bas du précédent),
donc leur position, et ainsi de suite jusqu'au bas de la page. Rien n'est « mis en cache » : changer
la largeur, c'est recalculer toutes les lignes puis tous les `y`. C'est ce recalcul qu'on appelle
reflow. Ici : `layout(root, nouvelle_largeur)` sur le MÊME arbre de boîtes.
(Redimensionner la fenêtre en hauteur ne change rien : rien ne dépend de la hauteur.)

Usage à la main :
    python -m browser.layout page.html
    python -m browser.layout --width 300 http://example.org/

Simplifications assumées :
- pas de polices réelles : la largeur d'un texte vient d'une fonction `measure(texte, style)`
  injectable ; par défaut `0,6 x taille` par caractère (police à chasse fixe fictive) ;
- `white-space: pre` n'est pas géré (tout est collapsé) ; `<br>` force un retour à la ligne ;
- un mot plus large que la ligne déborde au lieu d'être coupé ; pas de césure ;
- `display: inline-block` est traité comme `inline` ; flex, grid, table... comme `block` ;
- un élément inline qui CONTIENT un bloc (`<a><div>..</div></a>`) est traité comme un bloc ;
- les marges verticales de deux frères se superposent (la plus grande gagne) comme dans
  un navigateur, mais pas celles d'un parent avec son premier/dernier enfant, ni celles
  d'un bloc vide : elles s'additionnent ;
- `width`/`height` en px, em, % (% de la largeur du parent) ; box-sizing: content-box seulement ;
- pas de float, position, min/max-width, images, puces des <li>, ni de marges en %.
"""

import argparse
import re
from dataclasses import dataclass, field

from browser.css import INHERITED, INITIAL, style_document
from browser.html_parser import Element, Text, load_source, parse

ASCENT = 0.8              # part de la taille de police au-dessus de la ligne de base
NORMAL_LINE_HEIGHT = 1.2  # `line-height: normal` = 1,2 x la taille de police
CHAR_WIDTH = 0.6          # largeur d'un caractère, en fraction de la taille de police
EPSILON = 1e-9
BORDER_KEYWORDS = {"thin": 1.0, "medium": 3.0, "thick": 5.0}


# --------------------------------------------------------------------------
# Petites briques : longueurs, polices, géométrie
# --------------------------------------------------------------------------

_LENGTH_RE = re.compile(r"(-?\d*\.?\d+)(px|em|pt|%)?")
_NUMBER_RE = re.compile(r"\d*\.?\d+")


def parse_length(value, font_size, percent_base=None):
    """'10px' / '2em' / '12pt' / '0' / '50%' -> pixels (float), ou None (`auto`, illisible, % sans base)."""
    m = _LENGTH_RE.fullmatch(value.strip().lower())
    if not m:
        return None
    number, unit = float(m.group(1)), m.group(2) or "px"
    if unit == "px":
        return number
    if unit == "em":
        return number * font_size
    if unit == "pt":
        return number * 4 / 3
    return None if percent_base is None else number * percent_base / 100


def font_size_of(style):
    return float(style.get("font-size", "16px")[:-2])  # la taille calculée est toujours "<n>px"


def line_height_px(style, font_size):
    value = style.get("line-height", "normal").strip().lower()
    if value == "normal":
        return NORMAL_LINE_HEIGHT * font_size
    if _NUMBER_RE.fullmatch(value):  # `1.5` sans unité : multiplicateur de la taille de police
        return float(value) * font_size
    px = parse_length(value, font_size, percent_base=font_size)
    return px if px is not None else NORMAL_LINE_HEIGHT * font_size


def default_measure(text, style):
    """Largeur de `text` en pixels. Une vraie police (Tk, Pillow...) se branche ici."""
    return len(text) * CHAR_WIDTH * font_size_of(style)


def collapse(a, b):
    """Superposition de deux marges verticales adjacentes : la plus grande l'emporte.

    Deux marges positives -> le max. Positive et négative -> leur somme. Deux négatives -> la plus négative.
    """
    return max(a, b, 0.0) + min(a, b, 0.0)


@dataclass
class Edges:
    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0
    left: float = 0.0

    @property
    def horizontal(self):
        return self.left + self.right

    @property
    def vertical(self):
        return self.top + self.bottom

    def __iter__(self):
        return iter((self.top, self.right, self.bottom, self.left))


@dataclass
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height


# --------------------------------------------------------------------------
# Sous-étape 4a : contenu en ligne (mots -> lignes)
# --------------------------------------------------------------------------

@dataclass
class Piece:
    """Un morceau de texte d'UN élément. « foo<b>bar</b> » = un mot, deux pièces."""
    text: str
    style: dict
    node: Text = None


@dataclass
class Word:
    """Suite de pièces sans espace entre elles : l'unité insécable du retour à la ligne."""
    pieces: list = field(default_factory=list)

    @property
    def text(self):
        return "".join(p.text for p in self.pieces)


class _LineBreak:
    def __repr__(self):
        return "BREAK"


BREAK = _LineBreak()  # <br> : retour à la ligne forcé

_WHITESPACE = re.compile(r"([ \t\n\r\f]+)")  # pas \s : l'espace insécable (&nbsp;) ne se coupe pas


@dataclass
class TextFragment:
    text: str
    x: float = 0.0
    y: float = 0.0       # haut de la boîte du texte (hauteur = taille de police)
    width: float = 0.0
    height: float = 0.0
    style: dict = field(default_factory=dict)
    node: Text = None


@dataclass
class LineBox:
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0   # largeur occupée par le texte (pas celle du bloc)
    height: float = 0.0
    fragments: list = field(default_factory=list)

    @property
    def text(self):
        out = ""
        previous = None
        for f in self.fragments:
            if previous is not None and f.x > previous.x + previous.width + EPSILON:
                out += " "
            out += f.text
            previous = f
        return out


def _is_hidden(node):
    return isinstance(node, Element) and node.style.get("display", "inline") == "none"


def _flatten(nodes, out=None):
    """Nœuds -> suite de Text et de BREAK : les éléments inline disparaissent, leur texte reste."""
    out = [] if out is None else out
    for node in nodes:
        if isinstance(node, Text):
            out.append(node)
        elif _is_hidden(node):
            continue
        elif node.tag == "br":
            out.append(BREAK)
        else:
            _flatten(node.children, out)
    return out


def _make_words(flat):
    """Texte brut -> Word / BREAK. Les espaces (et retours à la ligne) se réduisent à UN séparateur."""
    items = []
    current = None
    for item in flat:
        if item is BREAK:
            items.append(BREAK)
            current = None
            continue
        style = item.parent.style
        for token in _WHITESPACE.split(item.text):
            if not token:
                continue
            if _WHITESPACE.fullmatch(token):
                current = None  # espace : le prochain morceau commence un nouveau mot
                continue
            if current is None:  # sinon on prolonge le mot, même à travers un changement d'élément
                current = Word()
                items.append(current)
            current.pieces.append(Piece(token, style, item))
    return items


def _line_metrics(style):
    """(au-dessus de la ligne de base, en dessous) d'un texte, interlignage réparti de part et d'autre."""
    fs = font_size_of(style)
    half_leading = (line_height_px(style, fs) - fs) / 2
    return ASCENT * fs + half_leading, (1 - ASCENT) * fs + half_leading


class _Line:
    """Une ligne en cours de remplissage."""

    def __init__(self, container_style):
        # La « strut » : même une ligne vide a la hauteur de la police du bloc (cas de `<br><br>`).
        self.above, self.below = _line_metrics(container_style)
        self.items = []         # (pièce, x relatif au début de la ligne, largeur)
        self.width = 0.0
        self.last_style = None  # style de la dernière pièce : c'est lui qui mesure l'espace suivant

    def add_word(self, word, widths, space_width):
        x = self.width + space_width
        for piece, w in zip(word.pieces, widths):
            self.items.append((piece, x, w))
            above, below = _line_metrics(piece.style)
            self.above, self.below = max(self.above, above), max(self.below, below)
            self.last_style = piece.style
            x += w
        self.width = x


def _flow_inline(box, measure):
    """Remplit `box.lines` (positions absolues) ; renvoie la hauteur totale des lignes."""
    available = box.width
    align = box.style.get("text-align", "left")
    y = box.y
    line = _Line(box.style)

    def finish():
        nonlocal y, line
        height = line.above + line.below
        offset = {"center": (available - line.width) / 2, "right": available - line.width}.get(align, 0.0)
        offset = max(0.0, offset)
        baseline = y + line.above
        fragments = []
        for piece, x, width in line.items:
            fs = font_size_of(piece.style)
            fragments.append(TextFragment(
                text=piece.text, x=box.x + offset + x, y=baseline - ASCENT * fs,
                width=width, height=fs, style=piece.style, node=piece.node))
        box.lines.append(LineBox(box.x + offset, y, line.width, height, fragments))
        y += height
        line = _Line(box.style)

    for item in box.inline_items:
        if item is BREAK:
            finish()  # même une ligne vide : `a<br><br>b` doit laisser une ligne blanche
            continue
        widths = [measure(p.text, p.style) for p in item.pieces]
        word_width = sum(widths)
        space = measure(" ", line.last_style) if line.items else 0.0
        if line.items and line.width + space + word_width > available + EPSILON:
            finish()  # le mot ne tient pas : on passe à la ligne et il en prend la première place
            space = 0.0
        line.add_word(item, widths, space)  # un mot plus large que la ligne déborde, seul sur la sienne
    if line.items:  # pas de ligne vide après un `<br>` final
        finish()
    return y - box.y


# --------------------------------------------------------------------------
# Sous-étape 4b-4c : boîtes de bloc, empilement vertical, margin / padding / border
# --------------------------------------------------------------------------

def _anonymous_style(parent_style):
    """Une boîte anonyme n'a pas d'élément : elle n'a que ce qui s'hérite de son parent."""
    return {**INITIAL, **{p: v for p, v in parent_style.items() if p in INHERITED}}


class BlockBox:
    """Une boîte de bloc. Soit `children` (blocs empilés), soit `inline_items` (-> `lines`), jamais les deux.

    Géométrie (remplie par `layout`) : x, y, width, height décrivent le CONTENU. Autour :
        margin-box  ⊃  border-box  ⊃  padding-box  ⊃  content
    """

    def __init__(self, node, style, anonymous=False):
        self.node = node
        self.style = style
        self.anonymous = anonymous
        self.children = []      # [BlockBox]
        self.inline_items = []  # [Word | BREAK]
        self.lines = []         # [LineBox], produites par layout()
        self.x = self.y = self.width = self.height = 0.0

        fs = font_size_of(style)
        self.padding = Edges(*(max(0.0, parse_length(style.get(f"padding-{s}", "0"), fs) or 0.0)
                               for s in ("top", "right", "bottom", "left")))
        self.border = Edges(*(self._border_width(style, s, fs) for s in ("top", "right", "bottom", "left")))
        specified = [style.get(f"margin-{s}", "0").strip().lower() for s in ("top", "right", "bottom", "left")]
        self.margin_auto_left = specified[3] == "auto"
        self.margin_auto_right = specified[1] == "auto"
        # `auto` vaut 0 ici ; layout() calcule la vraie valeur horizontale (centrage) selon la largeur.
        self.specified_margin = Edges(*(parse_length(v, fs) or 0.0 for v in specified))
        self.margin = Edges(*self.specified_margin)  # valeurs utilisées, mises à jour par layout()

    @staticmethod
    def _border_width(style, side, font_size):
        if style.get("border-style", "none") in ("none", "hidden"):
            return 0.0  # sans style, pas de bordure : `border-width: 3px` seul ne prend pas de place
        value = style.get(f"border-{side}-width", "medium").strip().lower()
        if value in BORDER_KEYWORDS:
            return BORDER_KEYWORDS[value]
        return max(0.0, parse_length(value, font_size) or 0.0)

    @property
    def padding_box(self):
        return Rect(self.x - self.padding.left, self.y - self.padding.top,
                    self.width + self.padding.horizontal, self.height + self.padding.vertical)

    @property
    def border_box(self):
        p = self.padding_box
        return Rect(p.x - self.border.left, p.y - self.border.top,
                    p.width + self.border.horizontal, p.height + self.border.vertical)

    @property
    def margin_box(self):
        b = self.border_box
        return Rect(b.x - self.margin.left, b.y - self.margin.top,
                    b.width + self.margin.horizontal, b.height + self.margin.vertical)

    def __repr__(self):
        name = "anonymous" if self.anonymous else self.node.tag
        return f"BlockBox({name}, {len(self.children)} blocs, {len(self.lines)} lignes)"


def _is_block_like(element):
    display = element.style.get("display", "inline")
    if display == "none":
        return False
    if display not in ("inline", "inline-block"):
        return True
    return any(isinstance(c, Element) and _is_block_like(c) for c in element.children)


def _fill(box, element):
    children = [n for n in element.children if not _is_hidden(n)]
    if not any(isinstance(n, Element) and _is_block_like(n) for n in children):
        box.inline_items = _make_words(_flatten(children))  # que du texte en ligne : des lignes
        return

    run = []  # suite de nœuds inline consécutifs, à envelopper dans une boîte anonyme

    def flush():
        items = _make_words(_flatten(run))
        run.clear()
        if items:  # une suite faite uniquement d'espaces (indentation entre deux blocs) disparaît
            anonymous = BlockBox(None, _anonymous_style(box.style), anonymous=True)
            anonymous.inline_items = items
            box.children.append(anonymous)

    for node in children:
        if isinstance(node, Element) and _is_block_like(node):
            flush()
            child = BlockBox(node, node.style)
            _fill(child, node)
            box.children.append(child)
        else:
            run.append(node)
    flush()


def build_layout_tree(dom_root):
    """DOM déjà stylé (`compute_styles`) -> arbre de BlockBox, sans positions."""
    box = BlockBox(dom_root, dom_root.style)
    _fill(box, dom_root)
    return box


def _specified_size(style, prop, font_size, percent_base):
    value = style.get(prop, "auto").strip().lower()
    if value == "auto":
        return None
    px = parse_length(value, font_size, percent_base)
    return None if px is None else max(0.0, px)


def _layout_block(box, x, border_top, available, measure):
    """Place `box`. `x` et `available` : zone libre du parent ; `border_top` : y du bord haut de la boîte."""
    fs = font_size_of(box.style)
    pad, border = box.padding, box.border
    extras = pad.horizontal + border.horizontal
    margin_left, margin_right = box.specified_margin.left, box.specified_margin.right

    # -- largeur : auto = tout l'espace restant ; sinon la valeur demandée, et les marges `auto` absorbent le reste
    specified_width = _specified_size(box.style, "width", fs, available)
    if specified_width is None:
        width = max(0.0, available - margin_left - margin_right - extras)
    else:
        width = specified_width
        free = max(0.0, available - width - extras - margin_left - margin_right)
        if box.margin_auto_left and box.margin_auto_right:
            margin_left = margin_right = free / 2
        elif box.margin_auto_left:
            margin_left = free
        elif box.margin_auto_right:
            margin_right = free
    box.margin = Edges(box.specified_margin.top, margin_right, box.specified_margin.bottom, margin_left)
    box.width = width
    box.x = x + margin_left + border.left + pad.left
    box.y = border_top + border.top + pad.top

    # -- contenu
    box.lines = []
    if box.children:
        previous = None
        for child in box.children:
            if previous is None:
                child_top = box.y + child.specified_margin.top
            else:  # entre frères : les deux marges se superposent
                child_top = previous.border_box.bottom + collapse(previous.margin.bottom, child.specified_margin.top)
            _layout_block(child, box.x, child_top, box.width, measure)
            previous = child
        content_bottom = previous.border_box.bottom + previous.margin.bottom
    elif box.inline_items:
        content_bottom = box.y + _flow_inline(box, measure)
    else:
        content_bottom = box.y

    specified_height = _specified_size(box.style, "height", fs, None)  # `height: 50%` : ignoré (auto)
    box.height = specified_height if specified_height is not None else content_bottom - box.y


def layout(root, viewport_width, measure=default_measure):
    """Calcule x, y, width, height de toutes les boîtes sous `root`. Relançable : c'est le reflow."""
    _layout_block(root, 0.0, 0.0, float(viewport_width), measure)
    return root


def layout_document(dom_root, viewport_width, measure=default_measure):
    """Raccourci : arbre de boîtes + premier layout. `dom_root.style` doit exister (étape 3)."""
    return layout(build_layout_tree(dom_root), viewport_width, measure)


# --------------------------------------------------------------------------
# Débogage
# --------------------------------------------------------------------------

def dump_layout(root):
    """Arbre de layout indenté : chaque bloc avec sa border-box, chaque ligne avec son texte."""
    out = []

    def n(value):
        return f"{value:.2f}".rstrip("0").rstrip(".")

    def edges(label, e):
        return f" {label}={'/'.join(n(v) for v in e)}" if any(e) else ""

    def walk(box, depth):
        pad = "  " * depth
        b = box.border_box
        name = "(anonyme)" if box.anonymous else ("#document" if box.node.tag == "#document" else f"<{box.node.tag}>")
        out.append(f"{pad}Block {name} x={n(b.x)} y={n(b.y)} w={n(b.width)} h={n(b.height)}"
                   f"{edges('margin', box.margin)}{edges('padding', box.padding)}{edges('border', box.border)}")
        for line in box.lines:
            out.append(f'{pad}  Line y={n(line.y)} h={n(line.height)} "{line.text}"')
        for child in box.children:
            walk(child, depth + 1)

    walk(root, 0)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Affiche l'arbre de layout d'une page pour une largeur de fenêtre.")
    ap.add_argument("source", help="URL http(s), chemin de fichier, ou '-' pour stdin")
    ap.add_argument("--width", type=float, default=800, help="largeur de la fenêtre en pixels (défaut 800)")
    args = ap.parse_args(argv)
    dom = parse(load_source(args.source))
    style_document(dom)
    print(dump_layout(layout_document(dom, args.width)))


if __name__ == "__main__":
    main()
