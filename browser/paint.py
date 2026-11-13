"""Étape 5a : la display list (arbre de layout -> liste de commandes de dessin).

    arbre de layout ──► build_display_list ──► DisplayList ──► execute(…, surface) ──► pixels
    (étape 4 : OÙ ?)    (étape 5 : QUOI ?)     des DONNÉES     (Tk, ou un enregistreur de test)

── Séparation layout / paint ───────────────────────────────────────────────────────────────

Le layout répond à « où est chaque chose et quelle taille a-t-elle ? » : il ne dessine rien.
La peinture répond à « de quelle couleur, avec quelle police, dans quel ordre ? » : elle ne
déplace rien, elle LIT les boîtes déjà placées. Ce que la peinture produit n'est pas une image
mais une liste de commandes très bêtes (`DrawRect`, `DrawText`) en coordonnées du DOCUMENT.

Ce découpage rapporte beaucoup :
  - le code qui sait ce qu'est une marge ou une ligne ne connaît pas Tk, et inversement : on
    teste toute la peinture sans ouvrir de fenêtre (comparer des listes de dataclasses) ;
  - pour changer de moteur de rendu (Tk, PDF, PNG...), on réécrit `execute`, pas le layout ;
  - le COÛT de chaque changement est minimal :
        scroll              -> on réexécute la liste avec un autre décalage (pas de layout)
        redimensionnement   -> layout + nouvelle display list (le texte se recoupe)
        changement de couleur (:hover...) -> nouvelle display list seulement (rien ne bouge)
  - la liste peut être filtrée avant exécution : on n'envoie au Canvas que les commandes
    visibles dans la fenêtre (`DisplayList.visible`).

La display list mémorise aussi les zones cliquables (`LinkRegion`) : le layout sait où est chaque
mot, la peinture sait lesquels sont des liens, donc c'est ici qu'on fabrique la table de
« clic -> URL ».

Usage à la main (sans fenêtre) :
    python -m browser.paint page.html --width 400

Simplifications assumées :
- seuls `background-color` (pas le raccourci `background`), `color` et `border-color` colorent ;
  les couleurs : noms de base, #rgb, #rrggbb, rgb(), rgba() (l'opacité est ignorée sauf 0) ;
- seuls les BLOCS ont fond et bordures : `span { background-color: ... }` ne colore rien ;
- toutes les bordures se dessinent « solid », même `dashed` ou `dotted` ;
- l'arrière-plan de <body> ne se propage pas au canvas entier (comme le ferait un navigateur) ;
- soulignement/barré : un ancêtre inline (`<a><b>x</b></a>`) les transmet, un ancêtre bloc non.
"""

import argparse
import re
from dataclasses import dataclass

from browser.css import style_document
from browser.html_parser import Element, load_source, parse
from browser.layout import ASCENT, default_measure, font_size_of, layout_document


# --------------------------------------------------------------------------
# Les commandes
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FontSpec:
    family: str   # première famille du CSS, telle quelle : "serif", "monospace", "Arial"...
    size: float   # pixels
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True)
class DrawRect:
    """Rectangle plein : fond, bordure, soulignement... tout ce qui n'est pas du texte."""
    x: float
    y: float
    width: float
    height: float
    color: str   # "#rrggbb"


@dataclass(frozen=True)
class DrawText:
    """Texte dont (x, y) est le coin haut-gauche de la boîte du texte (hauteur = taille de police)."""
    x: float
    y: float
    width: float
    height: float
    text: str
    font: FontSpec
    color: str


@dataclass(frozen=True)
class LinkRegion:
    """Zone cliquable : un lien sur UNE ligne (un lien coupé sur 2 lignes donne 2 zones)."""
    href: str
    x: float
    y: float
    width: float
    height: float

    def contains(self, px, py):
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height


@dataclass
class DisplayList:
    commands: list   # dans l'ordre de dessin : ce qui vient après recouvre ce qui vient avant
    links: list      # [LinkRegion]
    height: float    # hauteur du document, pour savoir jusqu'où on peut scroller

    def visible(self, top, bottom):
        """Commandes qui touchent la bande verticale [top, bottom) du document (la fenêtre)."""
        return [c for c in self.commands if c.y < bottom and c.y + c.height > top]

    def link_at(self, x, y):
        """Zone de lien sous le point (x, y) du DOCUMENT, ou None. La plus récente gagne."""
        for region in reversed(self.links):
            if region.contains(x, y):
                return region
        return None


def execute(commands, surface, scroll_y=0.0):
    """Exécute les commandes sur `surface` (draw_rect / draw_text), décalées de `scroll_y` vers le haut.

    C'est tout ce qu'un moteur de rendu doit savoir faire : `surface` peut être un Canvas Tk
    (gui.TkSurface), un enregistreur dans un test, un fichier SVG...
    """
    for c in commands:
        if isinstance(c, DrawRect):
            surface.draw_rect(c.x, c.y - scroll_y, c.width, c.height, c.color)
        else:
            surface.draw_text(c.x, c.y - scroll_y, c.text, c.font, c.color)


# --------------------------------------------------------------------------
# Couleurs et polices : valeurs CSS -> valeurs prêtes à dessiner
# --------------------------------------------------------------------------

NAMED_COLORS = {
    "black": "#000000", "silver": "#c0c0c0", "gray": "#808080", "grey": "#808080", "white": "#ffffff",
    "maroon": "#800000", "red": "#ff0000", "purple": "#800080", "fuchsia": "#ff00ff", "magenta": "#ff00ff",
    "green": "#008000", "lime": "#00ff00", "olive": "#808000", "yellow": "#ffff00", "navy": "#000080",
    "blue": "#0000ff", "teal": "#008080", "aqua": "#00ffff", "cyan": "#00ffff", "orange": "#ffa500",
    "brown": "#a52a2a", "pink": "#ffc0cb", "gold": "#ffd700", "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3",
    "darkgray": "#a9a9a9", "darkgrey": "#a9a9a9", "whitesmoke": "#f5f5f5", "crimson": "#dc143c",
}
_HEX_RE = re.compile(r"#([0-9a-f]{3}|[0-9a-f]{6})")
_NUM = r"(-?[\d.]+%?)"
_RGB_RE = re.compile(rf"rgba?\(\s*{_NUM}\s*,\s*{_NUM}\s*,\s*{_NUM}\s*(?:,\s*{_NUM}\s*)?\)")


def _channel(text):
    value = float(text[:-1]) * 255 / 100 if text.endswith("%") else float(text)
    return max(0, min(255, round(value)))


def parse_color(value, current=None):
    """'red' / '#f00' / '#ff0000' / 'rgb(255, 0, 0)' -> '#ff0000'. None = ne rien dessiner.

    `currentcolor` renvoie `current` (la couleur du texte de l'élément). `transparent`, une
    opacité nulle ou une valeur illisible renvoient None.
    """
    v = value.strip().lower()
    if v == "currentcolor":
        return current
    if v in NAMED_COLORS:
        return NAMED_COLORS[v]
    m = _HEX_RE.fullmatch(v)
    if m:
        digits = m.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return "#" + digits
    m = _RGB_RE.fullmatch(v)
    if m:
        alpha = m.group(4)
        if alpha is not None and float(alpha.rstrip("%")) == 0:
            return None
        return "#{:02x}{:02x}{:02x}".format(*(_channel(g) for g in m.groups()[:3]))
    return None


def font_spec(style):
    """Style calculé -> FontSpec. La même fonction sert au layout (mesure) et au dessin."""
    family = style.get("font-family", "serif").split(",")[0].strip().strip("'\"") or "serif"
    weight = style.get("font-weight", "normal").strip().lower()
    bold = weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600)
    italic = style.get("font-style", "normal").strip().lower() in ("italic", "oblique")
    return FontSpec(family, font_size_of(style), bold, italic)


# --------------------------------------------------------------------------
# Construction de la display list
# --------------------------------------------------------------------------

def _enclosing_link(text_node):
    node = text_node.parent
    while node is not None:
        if isinstance(node, Element) and node.tag == "a" and "href" in node.attributes:
            return node
        node = node.parent
    return None


def _decorations(text_node):
    """Soulignement / barré applicables à ce texte : les siens et ceux des ancêtres inline."""
    found = set()
    node = text_node.parent
    while isinstance(node, Element) and node.style.get("display", "inline") in ("inline", "inline-block"):
        found.update(node.style.get("text-decoration", "none").split())
        node = node.parent
    return found & {"underline", "line-through"}


class _Painter:
    def __init__(self):
        self.commands = []
        self.links = []

    def paint_box(self, box):
        style = box.style
        text_color = parse_color(style.get("color", "black")) or "#000000"
        visible = style.get("visibility", "visible") != "hidden"  # caché : il garde sa place, rien ne se dessine

        if visible:
            self._background_and_border(box, style, text_color)
        for child in box.children:
            self.paint_box(child)
        for line in box.lines:
            self._line(line, visible)

    def _background_and_border(self, box, style, text_color):
        b = box.border_box
        background = parse_color(style.get("background-color", "transparent"), current=text_color)
        if background and b.width > 0 and b.height > 0:
            self.commands.append(DrawRect(b.x, b.y, b.width, b.height, background))

        top, right, bottom, left = box.border
        if any(box.border):
            color = parse_color(style.get("border-color", "currentcolor"), current=text_color) or text_color
            middle = b.height - top - bottom
            for x, y, w, h in ((b.x, b.y, b.width, top),                              # haut
                               (b.x, b.bottom - bottom, b.width, bottom),             # bas
                               (b.x, b.y + top, left, middle),                        # gauche
                               (b.right - right, b.y + top, right, middle)):          # droite
                if w > 0 and h > 0:
                    self.commands.append(DrawRect(x, y, w, h, color))

    def _line(self, line, visible):
        if not visible:
            return
        fragments = line.fragments
        decorations = [_decorations(f.node) for f in fragments]
        for i, f in enumerate(fragments):
            color = parse_color(f.style.get("color", "black")) or "#000000"
            self.commands.append(DrawText(f.x, f.y, f.width, f.height, f.text, font_spec(f.style), color))
            baseline = f.y + ASCENT * f.height
            for kind in sorted(decorations[i]):
                # La décoration traverse aussi l'espace qui suit si le mot suivant est décoré pareil.
                width = f.width
                if i + 1 < len(fragments) and kind in decorations[i + 1]:
                    width = max(width, fragments[i + 1].x - f.x)
                thickness = max(1.0, f.height / 14)
                y = baseline + 0.1 * f.height if kind == "underline" else baseline - 0.3 * f.height
                self.commands.append(DrawRect(f.x, y, width, thickness, color))
        self._line_links(line)

    def _line_links(self, line):
        group = None  # [anchor, premier fragment, dernier fragment]
        for f in [*line.fragments, None]:  # le None final vide le dernier groupe
            anchor = _enclosing_link(f.node) if f is not None else None
            if group and anchor is group[0]:
                group[2] = f
                continue
            if group:
                first, last = group[1], group[2]
                self.links.append(LinkRegion(group[0].attributes["href"], first.x, line.y,
                                             last.x + last.width - first.x, line.height))
            group = [anchor, f, f] if anchor is not None else None


def build_display_list(root):
    """Arbre de layout (déjà positionné par `layout`) -> DisplayList. Ne modifie pas l'arbre."""
    painter = _Painter()
    painter.paint_box(root)
    return DisplayList(painter.commands, painter.links, root.border_box.bottom)


# --------------------------------------------------------------------------
# Débogage
# --------------------------------------------------------------------------

def dump_display_list(display_list):
    def n(v):
        return f"{v:.2f}".rstrip("0").rstrip(".")

    out = []
    for c in display_list.commands:
        if isinstance(c, DrawRect):
            out.append(f"RECT  ({n(c.x)}, {n(c.y)}) {n(c.width)}x{n(c.height)}  {c.color}")
        else:
            flags = ("B" if c.font.bold else "") + ("I" if c.font.italic else "")
            out.append(f'TEXT  ({n(c.x)}, {n(c.y)}) {c.font.family} {n(c.font.size)}px{" " + flags if flags else ""}'
                       f'  {c.color}  "{c.text}"')
    for link in display_list.links:
        out.append(f"LINK  ({n(link.x)}, {n(link.y)}) {n(link.width)}x{n(link.height)}  -> {link.href}")
    out.append(f"document height: {n(display_list.height)}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Affiche la display list d'une page (sans ouvrir de fenêtre).")
    ap.add_argument("source", help="URL http(s), chemin de fichier, ou '-' pour stdin")
    ap.add_argument("--width", type=float, default=800, help="largeur de la fenêtre en pixels (défaut 800)")
    args = ap.parse_args(argv)
    dom = parse(load_source(args.source))
    style_document(dom)
    print(dump_display_list(build_display_list(layout_document(dom, args.width, default_measure))))


if __name__ == "__main__":
    main()
