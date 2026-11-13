"""Tests de l'étape 5a : display list, couleurs, polices, zones de liens, exécution.

Repères : avec `* { font-size: 10px; margin: 0 }`, un caractère mesure 6px, une ligne fait 12px,
et la boîte d'un texte (hauteur 10) commence 1px sous le haut de sa ligne (ligne de base à y + 9).
"""

import pytest

from browser.css import compute_styles, parse_stylesheet
from browser.html_parser import parse
from browser.layout import build_layout_tree, layout, layout_document
from browser.paint import (
    DisplayList, DrawRect, DrawText, FontSpec, LinkRegion, build_display_list, dump_display_list,
    execute, font_spec, parse_color,
)

pytestmark = pytest.mark.usefixtures("constant_specificity")  # voir tests/conftest.py

BASE = "* { font-size: 10px; margin: 0 }"


def paint(html, css_text="", width=100):
    dom = parse(html)
    compute_styles(dom, [parse_stylesheet(BASE + css_text)])
    return build_display_list(layout_document(dom, width))


def rects(display_list):
    return [(c.x, round(c.y, 6), c.width, c.height, c.color) for c in display_list.commands if isinstance(c, DrawRect)]


def texts(display_list):
    return [c.text for c in display_list.commands if isinstance(c, DrawText)]


# ---- couleurs ------------------------------------------------------------------------------------

def test_named_hex_and_rgb_colors():
    assert parse_color("red") == "#ff0000" and parse_color(" Navy ") == "#000080"
    assert parse_color("#f00") == "#ff0000" and parse_color("#A1b2C3") == "#a1b2c3"
    assert parse_color("rgb(255, 0, 128)") == "#ff0080"


def test_rgb_percentages_and_clamping():
    assert parse_color("rgb(100%, 0%, 50%)") == "#ff0080"
    assert parse_color("rgb(300, -5, 0)") == "#ff0000"


def test_rgba_ignores_opacity_unless_fully_transparent():
    assert parse_color("rgba(0, 0, 255, 0.5)") == "#0000ff"
    assert parse_color("rgba(0, 0, 255, 0)") is None


def test_transparent_and_garbage_are_none():
    for value in ["transparent", "", "banana", "#12", "#gggggg", "rgb(1,2)", "hsl(0, 0%, 0%)"]:
        assert parse_color(value) is None, value


def test_currentcolor_uses_the_given_text_color():
    assert parse_color("currentcolor", current="#123456") == "#123456"
    assert parse_color("currentcolor") is None


# ---- polices -----------------------------------------------------------------------------------------

def test_font_spec_basics():
    assert font_spec({"font-family": "Arial, sans-serif", "font-size": "12px"}) == FontSpec("Arial", 12.0, False, False)
    assert font_spec({"font-family": '"Times New Roman", serif', "font-size": "10px"}).family == "Times New Roman"
    assert font_spec({"font-size": "16px"}).family == "serif"


def test_font_spec_bold_and_italic():
    s = {"font-size": "10px"}
    assert font_spec({**s, "font-weight": "bold"}).bold and font_spec({**s, "font-weight": "700"}).bold
    assert font_spec({**s, "font-weight": "600"}).bold and not font_spec({**s, "font-weight": "400"}).bold
    assert font_spec({**s, "font-style": "italic"}).italic and font_spec({**s, "font-style": "oblique"}).italic
    assert not font_spec({**s, "font-style": "normal"}).italic


# ---- texte et fonds ------------------------------------------------------------------------------------

def test_plain_text_command():
    dl = paint("<div>hi</div>", "div { color: red }")
    assert dl.commands == [DrawText(0, 1, 12, 10, "hi", FontSpec("serif", 10.0), "#ff0000")]
    assert dl.height == 12


def test_text_takes_the_style_of_its_own_element():
    dl = paint("<div>a <b>b</b> <i>c</i></div>")
    fonts = [c.font for c in dl.commands]
    assert [(f.bold, f.italic) for f in fonts] == [(False, False), (True, False), (False, True)]


def test_background_is_drawn_before_the_text_on_the_border_box():
    dl = paint("<div>hi</div>", "div { background-color: #eee; padding: 5px }")
    assert dl.commands[0] == DrawRect(0, 0, 100, 22, "#eeeeee")
    assert dl.commands[1] == DrawText(5, 6, 12, 10, "hi", FontSpec("serif", 10.0), "#000000")


def test_background_of_an_inline_element_is_not_painted():
    assert rects(paint("<div>a <span>b</span></div>", "span { background-color: red }")) == []


def test_transparent_or_missing_background_draws_nothing():
    assert rects(paint("<div>hi</div>")) == []
    assert rects(paint("<div>hi</div>", "div { background-color: transparent }")) == []
    assert rects(paint("<div></div>", "div { background-color: red }")) == []  # boîte de 0 de haut


def test_parent_is_painted_before_child():
    dl = paint("<div class=p><div class=c>t</div></div>", ".p { background-color: red } .c { background-color: blue }")
    assert [r[4] for r in rects(dl)] == ["#ff0000", "#0000ff"]
    assert isinstance(dl.commands[-1], DrawText)  # le texte vient après les fonds


def test_background_covers_padding_and_border_not_margin():
    dl = paint("<div>x</div>", "div { background-color: red; margin: 4px; padding: 3px; border: 1px solid blue }")
    assert rects(dl)[0] == (4, 4, 92, 12 + 2 * 4, "#ff0000")  # (x, y, largeur, hauteur) de la border-box


# ---- bordures ---------------------------------------------------------------------------------------------

def test_border_is_four_rects():
    dl = paint("<div>x</div>", "div { border: 2px solid red; width: 50px }")
    assert rects(dl) == [(0, 0, 54, 2, "#ff0000"),    # haut
                         (0, 14, 54, 2, "#ff0000"),   # bas
                         (0, 2, 2, 12, "#ff0000"),    # gauche
                         (52, 2, 2, 12, "#ff0000")]   # droite
    assert dl.commands[-1] == DrawText(2, 3, 6, 10, "x", FontSpec("serif", 10.0), "#000000")


def test_border_color_defaults_to_the_text_color():
    assert {r[4] for r in rects(paint("<div>x</div>", "div { color: green; border: 1px solid }"))} == {"#008000"}


def test_per_side_border_widths_skip_empty_sides():
    dl = paint("<div>x</div>", "div { border-style: solid; border-width: 0 0 3px 0; border-color: red }")
    assert rects(dl) == [(0, 12, 100, 3, "#ff0000")]


def test_no_border_without_a_style():
    assert rects(paint("<div>x</div>", "div { border-width: 3px }")) == []


# ---- décorations -----------------------------------------------------------------------------------------------

def test_link_is_blue_and_underlined_across_the_space_between_words():
    dl = paint('<div><a href="/x">aa bb</a></div>')
    assert [c.color for c in dl.commands if isinstance(c, DrawText)] == ["#0000ff", "#0000ff"]
    # ligne de base = 9, soulignement 1px dessous ; le 1er trait s'étend jusqu'au mot suivant (12 + espace 6)
    assert rects(dl) == [(0, 10, 18, 1, "#0000ff"), (18, 10, 12, 1, "#0000ff")]


def test_underline_comes_from_inline_ancestors():
    assert len(rects(paint('<div><a href="/x"><b>x</b></a></div>'))) == 1
    assert rects(paint("<div><b>x</b></div>")) == []


def test_line_through():
    assert rects(paint("<div><s>x</s></div>", "s { text-decoration: line-through }")) == [(0, 6, 6, 1, "#000000")]


def test_visibility_hidden_keeps_the_space_but_draws_nothing():
    dl = paint("<div>hi</div>", "div { visibility: hidden; background-color: red; border: 1px solid red }")
    assert dl.commands == [] and dl.links == [] and dl.height == 14


# ---- zones de liens -------------------------------------------------------------------------------------------------

def test_link_region_covers_only_the_link_text():
    dl = paint('<p>go <a href="/x">here now</a> ok</p>')
    assert dl.links == [LinkRegion("/x", 18, 0, 48, 12)]
    assert dl.link_at(20, 5) == dl.links[0]
    assert dl.link_at(5, 5) is None            # sur « go »
    assert dl.link_at(18 + 48, 5) is None       # le bord droit est exclu
    assert dl.link_at(20, 12) is None           # le bord bas est exclu
    assert dl.link_at(18, 0) == dl.links[0]     # le coin haut-gauche est inclus


def test_two_adjacent_links_are_two_regions():
    dl = paint('<div><a href="a">x</a> <a href="b">y</a></div>')
    assert [(l.href, l.x, l.width) for l in dl.links] == [("a", 0, 6), ("b", 12, 6)]
    assert dl.link_at(1, 1).href == "a" and dl.link_at(13, 1).href == "b" and dl.link_at(8, 1) is None


def test_link_split_across_lines_gives_one_region_per_line():
    dl = paint('<div><a href="l">aaaa bbbb</a></div>', width=30)
    assert [(l.href, l.y) for l in dl.links] == [("l", 0), ("l", 12)]
    assert dl.link_at(5, 15).href == "l"


def test_link_regions_through_inline_and_block_descendants():
    assert paint('<div><a href="y"><b>t</b></a></div>').links[0].href == "y"
    assert paint('<a href="z"><div>t</div></a>').links[0].href == "z"


def test_anchor_without_href_is_not_a_link():
    assert paint("<div><a>t</a></div>").links == []


def test_href_is_kept_as_written():
    assert paint('<div><a href="../x?y=1#z">t</a></div>').links[0].href == "../x?y=1#z"  # c'est Tab qui le résout


# ---- fenêtre de visibilité et exécution ----------------------------------------------------------------------------------

def five_lines():
    return paint("<div>aa bb cc dd ee</div>", width=12)  # un mot par ligne : textes à y = 1, 13, 25, 37, 49


def test_visible_returns_only_commands_touching_the_band_in_order():
    dl = five_lines()
    assert [c.text for c in dl.visible(12, 24)] == ["bb"]
    assert [c.text for c in dl.visible(0, 100)] == ["aa", "bb", "cc", "dd", "ee"]
    assert [c.text for c in dl.visible(20, 40)] == ["bb", "cc", "dd"]


def test_visible_band_edges_are_exclusive():
    dl = five_lines()
    assert dl.visible(11, 13) == []   # « aa » finit pile à 11, « bb » commence pile à 13
    assert [c.text for c in dl.visible(10.9, 13.1)] == ["aa", "bb"]


class Recorder:
    def __init__(self):
        self.calls = []

    def draw_rect(self, *args):
        self.calls.append(("rect", *args))

    def draw_text(self, *args):
        self.calls.append(("text", *args))


def test_execute_dispatches_in_order_and_applies_the_scroll_offset():
    dl = paint("<div>hi</div>", "div { background-color: red }")
    surface = Recorder()
    execute(dl.commands, surface, scroll_y=0.5)
    assert surface.calls == [("rect", 0, -0.5, 100, 12, "#ff0000"),
                             ("text", 0, 0.5, "hi", FontSpec("serif", 10.0), "#000000")]


def test_scrolling_only_changes_execution_not_the_display_list():
    dl = five_lines()
    before = list(dl.commands)
    a, b = Recorder(), Recorder()
    execute(dl.visible(0, 24), a, 0)
    execute(dl.visible(24, 48), b, 24)
    assert dl.commands == before                                  # rien n'est recalculé
    assert [c[2] for c in a.calls] == [1, 13] and [c[2] for c in b.calls] == [1, 13]  # mêmes y à l'écran


# ---- liens avec layout / séparation --------------------------------------------------------------------------------------------

def test_painting_does_not_modify_the_layout():
    dom = parse("<div>aaaa bbbb</div>")
    compute_styles(dom, [parse_stylesheet(BASE)])
    root = layout_document(dom, 30)
    snapshot = [(b.x, b.y, b.width, b.height, [(f.text, f.x, f.y) for l in b.lines for f in l.fragments])
                for b in [root, *root.children]]
    build_display_list(root)
    build_display_list(root)
    assert snapshot == [(b.x, b.y, b.width, b.height, [(f.text, f.x, f.y) for l in b.lines for f in l.fragments])
                        for b in [root, *root.children]]


def test_a_new_width_gives_a_new_display_list_from_the_same_boxes():
    dom = parse("<div>aaaa bbbb</div>")
    compute_styles(dom, [parse_stylesheet(BASE)])
    root = build_layout_tree(dom)
    wide = build_display_list(layout(root, 100))
    narrow = build_display_list(layout(root, 30))
    assert [(c.x, c.y) for c in wide.commands] == [(0, 1), (30, 1)]       # une ligne
    assert [(c.x, c.y) for c in narrow.commands] == [(0, 1), (0, 13)]     # deux lignes
    assert (wide.height, narrow.height) == (12, 24)


def test_display_list_height_is_the_document_height():
    assert paint("<div>a</div><div>b</div>").height == 24
    assert paint("").height == 0


def test_empty_document():
    dl = paint("")
    assert isinstance(dl, DisplayList) and dl.commands == [] and dl.links == []


def test_dump_display_list_format():
    out = dump_display_list(paint('<div><a href="/x">hi</a></div>', "div { background-color: #eee }"))
    assert out.splitlines() == [
        "RECT  (0, 0) 100x12  #eeeeee",
        'TEXT  (0, 1) serif 10px  #0000ff  "hi"',
        "RECT  (0, 10) 12x1  #0000ff",
        "LINK  (0, 0) 12x12  -> /x",
        "document height: 12",
    ]
