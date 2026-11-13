"""Tests de l'étape 4 : layout (lignes, blocs, margin/padding/border, reflow).

Repères de calcul : avec `* { font-size: 10px }` et la police fictive par défaut, un caractère
mesure 6px, une espace 6px, une ligne `normal` fait 12px (10 x 1,2) : 9px au-dessus de la ligne
de base, 3px en dessous.
"""

import pytest

from browser.css import compute_styles, parse_stylesheet
from browser.html_parser import parse
from browser.layout import (
    BREAK, BlockBox, Edges, build_layout_tree, collapse, default_measure, dump_layout, layout,
    layout_document, line_height_px, parse_length, Rect,
)

BASE = "* { font-size: 10px; margin: 0 }"


pytestmark = pytest.mark.usefixtures("constant_specificity")  # voir tests/conftest.py


def styled(html, css_text=""):
    dom = parse(html)
    compute_styles(dom, [parse_stylesheet(BASE + css_text)])
    return dom


def lay(html, css_text="", width=100, measure=default_measure):
    return layout_document(styled(html, css_text), width, measure)


def lines_of(box):
    return [line.text for line in box.lines]


# ---- 4a : texte en ligne ---------------------------------------------------------------------

def test_single_word_geometry():
    root = lay("<div>hello</div>")
    div = root.children[0]
    (line,) = div.lines
    (frag,) = line.fragments
    assert (frag.x, frag.width, frag.height) == (0, 30, 10)
    assert (line.y, line.height) == (0, 12)
    assert (div.y, div.height) == (0, 12)


def test_text_wraps_when_the_line_is_full():
    # 5 lettres = 30px ; "aaaaa bbbbb" = 66px ; un 3e mot ferait 102px > 100.
    div = lay("<div>aaaaa bbbbb ccccc ddddd</div>").children[0]
    assert lines_of(div) == ["aaaaa bbbbb", "ccccc ddddd"]
    assert [l.y for l in div.lines] == [0, 12]
    assert div.height == 24


def test_word_that_exactly_fits_stays_on_the_line():
    assert lines_of(lay("<div>aaaaa bbbbb</div>", width=66).children[0]) == ["aaaaa bbbbb"]
    assert lines_of(lay("<div>aaaaa bbbbb</div>", width=65.9).children[0]) == ["aaaaa", "bbbbb"]


def test_one_word_per_line_in_a_narrow_window():
    assert lines_of(lay("<div>aa bb cc</div>", width=20).children[0]) == ["aa", "bb", "cc"]


def test_word_wider_than_the_line_overflows_alone():
    div = lay("<div>abcdefghij kl</div>", width=20).children[0]
    assert lines_of(div) == ["abcdefghij", "kl"]
    assert div.lines[0].fragments[0].width == 60  # dépasse les 20px, sans être coupé


def test_whitespace_is_collapsed_and_trimmed():
    div = lay("<div>\n   a   b\n\n  c  \t</div>").children[0]
    assert lines_of(div) == ["a b c"]
    assert [f.x for f in div.lines[0].fragments] == [0, 12, 24]  # un seul espace (6px) entre les mots


def test_inline_elements_flow_with_the_text():
    div = lay("<div>a <b>bb</b> c</div>").children[0]
    assert lines_of(div) == ["a bb c"]
    assert [(f.text, f.x, f.width) for f in div.lines[0].fragments] == [("a", 0, 6), ("bb", 12, 12), ("c", 30, 6)]
    assert div.lines[0].fragments[1].style["font-weight"] == "bold"  # le fragment garde SON style


def test_inline_element_can_be_split_across_lines():
    div = lay("<div><b>aaa bbb ccc</b></div>", width=40).children[0]
    assert lines_of(div) == ["aaa", "bbb", "ccc"]  # "aaa bbb" = 42px > 40 : un inline, coupé en 3 lignes
    assert all(f.style["font-weight"] == "bold" for line in div.lines for f in line.fragments)


def test_no_space_between_pieces_keeps_them_glued_in_one_word():
    div = lay("<div>foo<b>bar</b>baz</div>").children[0]
    assert [(f.text, f.x) for f in div.lines[0].fragments] == [("foo", 0), ("bar", 18), ("baz", 36)]
    assert lines_of(div) == ["foobarbaz"]


def test_glued_word_wraps_as_a_unit():
    div = lay("<div>aa foo<b>bar</b></div>", width=40).children[0]
    assert lines_of(div) == ["aa", "foobar"]  # "foo" et "bar" ne se séparent pas


def test_br_forces_a_new_line():
    div = lay("<div>a<br>b</div>").children[0]
    assert lines_of(div) == ["a", "b"]


def test_double_br_leaves_an_empty_line_but_trailing_br_does_not():
    div = lay("<div>a<br><br>b</div>").children[0]
    assert lines_of(div) == ["a", "", "b"] and div.height == 36
    assert lines_of(lay("<div>a<br></div>").children[0]) == ["a"]


def test_text_align_center_and_right():
    assert lay("<div>aaaa</div>", "div { text-align: center }").children[0].lines[0].fragments[0].x == 38  # (100-24)/2
    assert lay("<div>aaaa</div>", "div { text-align: right }").children[0].lines[0].fragments[0].x == 76


def test_text_align_is_inherited_by_anonymous_boxes():
    body = lay("<div>aaaa<p>b</p></div>", "div { text-align: right }").children[0]
    assert body.children[0].anonymous and body.children[0].lines[0].fragments[0].x == 76


def test_hidden_inline_elements_disappear():
    div = lay("<div>a<span class=h>zz</span>b</div>", ".h { display: none }").children[0]
    assert lines_of(div) == ["ab"]


def test_empty_and_whitespace_only_blocks_have_no_height():
    assert lay("<div></div>").children[0].height == 0
    assert lay("<div>  \n  </div>").children[0].height == 0


def test_line_height_units():
    assert line_height_px({"line-height": "normal"}, 10) == 12
    assert line_height_px({"line-height": "2"}, 10) == 20     # sans unité : multiplicateur
    assert line_height_px({"line-height": "30px"}, 10) == 30
    assert line_height_px({"line-height": "150%"}, 10) == 15
    assert line_height_px({"line-height": "garbage"}, 10) == 12
    assert lay("<div>a</div>", "div { line-height: 2 }").children[0].height == 20


def test_big_text_makes_the_line_taller_and_all_fragments_share_the_baseline():
    div = lay("<div>a <span class=big>b</span></div>", ".big { font-size: 20px }").children[0]
    (line,) = div.lines
    small, big = line.fragments
    assert line.height == 24                       # 20px x 1,2
    assert small.y + 0.8 * 10 == big.y + 0.8 * 20  # même ligne de base
    assert (small.height, big.height) == (10, 20)


def test_custom_measure_function_is_used():
    div = lay("<div>aa bb</div>", measure=lambda text, style: 10 * len(text)).children[0]
    assert [(f.x, f.width) for f in div.lines[0].fragments] == [(0, 20), (30, 20)]  # l'espace mesure 10


def test_nbsp_does_not_break():
    assert lines_of(lay("<div>a&nbsp;b ccccccc</div>", width=30).children[0])[0].startswith("a\xa0b")


# ---- 4b : blocs empilés ------------------------------------------------------------------------

def test_blocks_stack_vertically():
    root = lay("<div>a</div><div>b</div><div>c</div>")
    assert [(c.y, c.height) for c in root.children] == [(0, 12), (12, 12), (24, 12)]
    assert root.height == 36


def test_block_fills_the_width_inline_does_not():
    root = lay("<div>a</div>", width=250)
    assert root.children[0].width == 250
    assert root.children[0].lines[0].width == 6  # la ligne n'occupe que son texte


def test_nested_blocks_share_the_same_left_edge_and_width():
    outer = lay("<div><div><div>x</div></div></div>").children[0]
    inner = outer.children[0].children[0]
    assert (inner.x, inner.width) == (0, 100)


def test_parent_height_is_the_sum_of_children():
    div = lay("<div><p>aaaaa bbbbb ccccc ddddd</p><p>x</p></div>").children[0]
    assert [c.height for c in div.children] == [24, 12] and div.height == 36


def test_mixed_content_gets_anonymous_blocks():
    div = lay("<div>one<p>two</p>three</div>").children[0]
    assert [(c.anonymous, lines_of(c)) for c in div.children] == [(True, ["one"]), (False, ["two"]), (True, ["three"])]
    assert [c.y for c in div.children] == [0, 12, 24]


def test_whitespace_between_blocks_makes_no_anonymous_box():
    div = lay("<div>\n  <p>a</p>\n  <p>b</p>\n</div>").children[0]
    assert [c.anonymous for c in div.children] == [False, False]


def test_display_none_elements_make_no_box():
    root = lay("<head><title>t</title></head><div>a</div><script>x</script>")
    assert [c.node.tag for c in root.children] == ["div"]


def test_list_items_stack_like_blocks():
    ul = lay("<ul><li>a<li>b</ul>").children[0]
    assert [(c.node.tag, c.y) for c in ul.children] == [("li", 0), ("li", 12)]


def test_inline_element_containing_a_block_is_laid_out_as_a_block():
    div = lay("<div>x<a><div>y</div></a>z</div>").children[0]
    assert [c.anonymous for c in div.children] == [True, False, True]
    assert div.children[1].children[0].lines[0].text == "y"


def test_user_agent_defaults_h1_and_collapsing_paragraph_margins():
    dom = parse("<p>a</p><p>b</p><h1>T</h1>")
    compute_styles(dom, [parse_stylesheet("* { font-size: 10px }")])  # on garde les marges du navigateur
    p1, p2, h1 = layout_document(dom, 100).children
    assert (p1.y, p2.y) == (16, 16 + 12 + 16)  # 16px entre les deux, pas 32 : les marges se superposent
    assert h1.border_box.y == p2.border_box.bottom + 21  # max(16, 21)


# ---- 4c : margin / padding / border ---------------------------------------------------------------

def test_padding_shrinks_the_content_and_grows_the_box():
    div = lay("<div>aaaaa bbbbb</div>", "div { padding: 10px }", width=100).children[0]
    assert (div.x, div.y, div.width) == (10, 10, 80)
    assert div.border_box.width == 100 and div.border_box.height == div.height + 20
    assert div.padding_box == div.border_box


def test_padding_changes_where_the_text_wraps():
    # Contenu de 80px : "aaaaa bbbbb" (66) tient. Avec padding 20 -> 60px : il ne tient plus.
    assert len(lay("<div>aaaaa bbbbb</div>", "div { padding: 10px }").children[0].lines) == 1
    assert len(lay("<div>aaaaa bbbbb</div>", "div { padding: 20px }").children[0].lines) == 2


def test_border_takes_space_between_padding_and_margin():
    div = lay("<div>a</div>", "div { margin: 5px; padding: 10px; border: 2px solid red }").children[0]
    assert (div.x, div.y) == (5 + 2 + 10, 5 + 2 + 10)
    assert div.width == 100 - 2 * (5 + 2 + 10)
    assert div.margin_box == Rect(0, 0, 100, div.height + 2 * 17)
    assert div.border_box.x == 5 and div.padding_box.x == 7
    assert (div.border.top, div.padding.top, div.margin.top) == (2, 10, 5)


def test_border_without_a_style_takes_no_space():
    div = lay("<div>a</div>", "div { border-width: 3px }").children[0]
    assert (div.x, div.y, div.border.top) == (0, 0, 0)
    div = lay("<div>a</div>", "div { border: 3px red }").children[0]  # raccourci sans style : style none
    assert div.border.left == 0


def test_border_keywords_and_per_side_widths():
    div = lay("<div>a</div>", "div { border-style: solid; border-width: thin 5px 0 thick }").children[0]
    assert tuple(div.border) == (1, 5, 0, 5)
    div = lay("<div>a</div>", "div { border-style: dashed; border-left-width: 4px }").children[0]
    assert div.border.left == 4 and div.border.top == 3  # medium par défaut


def test_margin_offsets_the_box_and_narrows_it():
    div = lay("<div>a</div>", "div { margin: 5px 10px }").children[0]
    assert (div.x, div.y, div.width) == (10, 5, 80)
    assert lay("<div>a</div><div>b</div>", "div { margin: 5px }").children[1].y > 12


def test_margin_units_em():
    assert lay("<div>a</div>", "div { margin-left: 2em }").children[0].x == 20


def test_nested_offsets_accumulate():
    inner = lay("<div><div>a</div></div>", "div { padding: 10px }").children[0].children[0]
    assert (inner.x, inner.y, inner.width) == (20, 20, 60)


def test_vertical_margins_of_siblings_collapse():
    a, b = lay("<div>a</div><div>b</div>", "div { margin: 10px 0 }").children
    assert b.border_box.y - a.border_box.bottom == 10   # et non 20
    a, b = lay("<div class=a>a</div><div>b</div>", "div { margin-top: 10px } .a { margin-bottom: 20px }").children
    assert b.border_box.y - a.border_box.bottom == 20   # la plus grande des deux


def test_collapse_function():
    assert collapse(10, 20) == 20 and collapse(20, 10) == 20 and collapse(0, 0) == 0
    assert collapse(-5, 10) == 5 and collapse(-5, -10) == -10 and collapse(10, 10) == 10


def test_parent_and_child_margins_add_up_in_this_implementation():
    # Simplification documentée : un navigateur les superposerait (le parent n'a ni padding ni bordure).
    inner = lay("<div><div>a</div></div>", "div { margin-top: 10px }").children[0].children[0]
    assert inner.border_box.y == 20


def test_explicit_width_height_px_em_percent():
    div = lay("<div>a</div>", "div { width: 50px; height: 30px }").children[0]
    assert (div.width, div.height, div.x) == (50, 30, 0)
    assert lay("<div>a</div>", "div { width: 5em }").children[0].width == 50
    assert lay("<div>a</div>", "div { width: 25% }", width=200).children[0].width == 50


def test_width_is_the_content_width_padding_comes_on_top():
    div = lay("<div>a</div>", "div { width: 50px; padding: 5px; border: 1px solid red }").children[0]
    assert div.width == 50 and div.border_box.width == 62


def test_auto_margins_center_the_box():
    assert lay("<div>a</div>", "div { width: 50px; margin: 0 auto }").children[0].x == 25
    assert lay("<div>a</div>", "div { width: 50px; margin-left: auto }").children[0].x == 50
    assert lay("<div>a</div>", "div { width: 50px; margin-right: auto }").children[0].x == 0


def test_auto_margins_without_a_width_are_zero():
    div = lay("<div>a</div>", "div { margin: 0 auto }").children[0]
    assert (div.x, div.width) == (0, 100)


def test_box_wider_than_its_parent_does_not_go_negative():
    div = lay("<div>a</div>", "div { width: 500px; margin: 0 auto }").children[0]
    assert div.x == 0 and div.width == 500


def test_explicit_height_overrides_content_height_and_pushes_the_next_block():
    a, b = lay("<div>a</div><div>b</div>", "div { height: 40px }").children
    assert (a.height, b.y) == (40, 40)


def test_parse_length():
    assert parse_length("10px", 16) == 10 and parse_length("2em", 5) == 10 and parse_length("12pt", 16) == 16
    assert parse_length("0", 16) == 0 and parse_length("-3px", 16) == -3
    assert parse_length("50%", 16, percent_base=200) == 100
    assert parse_length("50%", 16) is None and parse_length("auto", 16) is None and parse_length("x", 16) is None


# ---- reflow --------------------------------------------------------------------------------------------

def test_changing_the_width_reflows_the_same_tree():
    root = build_layout_tree(styled("<div>aaaaa bbbbb ccccc ddddd</div><div>tail</div>"))
    div, tail = root.children
    layout(root, 100)
    assert (len(div.lines), tail.y, root.height) == (2, 24, 36)
    layout(root, 40)   # même arbre, autre largeur : les lignes sont recalculées...
    assert (len(div.lines), tail.y, root.height) == (4, 48, 60)  # ...donc la hauteur, donc la position du bloc suivant
    layout(root, 100)  # et c'est réversible : rien n'est resté en cache
    assert (len(div.lines), tail.y, root.height) == (2, 24, 36)
    assert root.children == [div, tail] and root.children[0] is div  # l'arbre n'a pas été reconstruit


def test_wider_window_never_adds_lines():
    root = build_layout_tree(styled("<div>aaaaa bbbbb ccccc ddddd eeeee fffff</div>"))
    counts = []
    for width in (30, 60, 100, 200, 500):
        layout(root, width)
        counts.append(len(root.children[0].lines))
    assert counts == sorted(counts, reverse=True) and counts[-1] == 1


def test_layout_is_deterministic_for_a_given_width():
    root = build_layout_tree(styled("<div>aaaaa bbbbb ccccc</div>"))
    layout(root, 100)
    snapshot = dump_layout(root)
    layout(root, 100)
    assert dump_layout(root) == snapshot


def test_auto_margins_are_recomputed_on_reflow_not_cached():
    root = build_layout_tree(styled("<div>a</div>", "div { width: 50px; margin: 0 auto }"))
    layout(root, 100)
    assert root.children[0].x == 25
    layout(root, 200)
    assert root.children[0].x == 75
    layout(root, 40)  # fenêtre plus étroite que la boîte : plus de marge à répartir
    assert root.children[0].x == 0


def test_reflow_percentage_widths_follow_the_window():
    root = build_layout_tree(styled("<div>a</div>", "div { width: 50% }"))
    assert layout(root, 100).children[0].width == 50
    assert layout(root, 300).children[0].width == 150


# ---- structure de l'arbre -------------------------------------------------------------------------------

def test_a_box_holds_blocks_or_lines_never_both():
    def check(box):
        assert not (box.children and box.inline_items)
        for c in box.children:
            check(c)
    check(build_layout_tree(styled("<div>a<p>b<b>c</b></p>d<ul><li>e<li>f</ul></div>")))


def test_build_tree_has_no_geometry_yet():
    root = build_layout_tree(styled("<div>hello</div>"))
    div = root.children[0]
    assert isinstance(div, BlockBox) and div.lines == [] and (div.x, div.y, div.width, div.height) == (0, 0, 0, 0)
    assert [type(i).__name__ for i in div.inline_items] == ["Word"] and BREAK not in div.inline_items


def test_edges_helpers():
    e = Edges(1, 2, 3, 4)
    assert (e.horizontal, e.vertical, tuple(e)) == (6, 4, (1, 2, 3, 4))


# ---- affichage ------------------------------------------------------------------------------------------

def test_dump_layout_exact():
    out = dump_layout(lay("<div>hi</div>", "div { padding: 5px }", width=80))
    assert out.splitlines() == [
        "Block #document x=0 y=0 w=80 h=22",
        "  Block <div> x=0 y=0 w=80 h=22 padding=5/5/5/5",
        '    Line y=5 h=12 "hi"',
    ]


# ---- avec la vraie cascade ----------------------------------------------------------------------------------

def test_layout_with_the_real_specificity(monkeypatch):
    """Seul test de ce fichier qui utilise TA fonction : il échoue tant qu'elle n'est pas écrite."""
    monkeypatch.undo()
    dom = parse('<div id="x" class="a">hello</div>')
    compute_styles(dom, [parse_stylesheet(BASE + "#x { padding: 10px } .a { padding: 0 } div { padding: 1px }")])
    assert layout_document(dom, 100).children[0].padding.top == 10  # l'id gagne
