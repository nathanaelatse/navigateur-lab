"""Tests des sous-étapes 3b-3c : cascade, héritage, valeurs par défaut.

Attention : `compute_styles` appelle `specificity()` pour toute règle qui correspond (même celles
du navigateur), donc ces tests échouent tant que tu n'as pas écrit cette fonction.
"""

from browser.css import (
    INITIAL, compute_styles, dump_styled_tree, parse_stylesheet, resolve_font_size, style_document,
)
from browser.html_parser import Element, Text, parse


def styled(html, css="", *more_sheets):
    root = parse(html)
    compute_styles(root, [parse_stylesheet(c) for c in (css, *more_sheets) if c])
    return root


def by_id(root, ident):
    def walk(n):
        if isinstance(n, Element):
            if n.attributes.get("id") == ident:
                return n
            for c in n.children:
                found = walk(c)
                if found:
                    return found
    return walk(root)


# ---- cascade : ordre ---------------------------------------------------------------------

def test_later_rule_wins_at_equal_specificity():
    root = styled('<p id="x">', "p { color: red } p { color: blue }")
    assert by_id(root, "x").style["color"] == "blue"


def test_rule_order_across_sheets():
    root = styled('<p id="x">', "p { color: red }", "p { color: blue }")
    assert by_id(root, "x").style["color"] == "blue"


def test_declarations_from_different_rules_are_merged():
    root = styled('<p id="x" class="a">', "p { color: red } .a { text-align: center }")
    s = by_id(root, "x").style
    assert (s["color"], s["text-align"]) == ("red", "center")


def test_property_overridden_only_where_redeclared():
    root = styled('<p id="x" class="a">', "p { color: red; text-align: right } .a { color: blue }")
    s = by_id(root, "x").style
    assert (s["color"], s["text-align"]) == ("blue", "right")


def test_selector_list_applies_to_each():
    root = styled('<h1 id="a"></h1><p id="b" class="c"></p><div id="d"></div>', "h1, .c { color: red }")
    assert [by_id(root, i).style["color"] for i in "abd"] == ["red", "red", "black"]


# ---- cascade : spécificité (utilise TA fonction) ------------------------------------------------

def test_specificity_beats_order():
    root = styled('<p id="x" class="a">', ".a { color: red } p { color: blue }")
    assert by_id(root, "x").style["color"] == "red"


def test_id_beats_class_beats_tag():
    css = "p { color: blue } .a { color: red } #x { color: green }"
    assert by_id(styled('<p id="x" class="a">', css), "x").style["color"] == "green"
    assert by_id(styled('<p id="x">', "#x { color: green } p { color: blue }"), "x").style["color"] == "green"


def test_compound_selector_is_more_specific_than_its_parts():
    root = styled('<p id="x" class="a">', "p.a { color: red } .a { color: blue }")
    assert by_id(root, "x").style["color"] == "red"


def test_author_always_beats_user_agent_even_with_lower_specificity():
    # UA : `a { color: blue }` (0,0,1). Auteur : `*` (0,0,0) gagne quand même.
    root = styled('<a id="x">', "* { color: red }")
    assert by_id(root, "x").style["color"] == "red"


def test_user_agent_applies_when_author_is_silent():
    root = styled('<a id="x">')
    assert by_id(root, "x").style["color"] == "blue"


# ---- héritage -----------------------------------------------------------------------------------------

def test_inherited_property_flows_down_several_levels():
    root = styled('<body><div><p><span id="x">t</span></p></div></body>', "body { color: red }")
    assert by_id(root, "x").style["color"] == "red"


def test_inheritance_stops_where_the_child_declares_its_own():
    root = styled('<div><p><i id="x">t</i></p><p id="y" class="a">u</p></div>',
                  "div { color: red } .a { color: blue }")
    assert by_id(root, "y").style["color"] == "blue"
    assert by_id(root, "x").style["color"] == "red"


def test_ua_declaration_beats_inheritance():
    # <a> déclare color: blue dans la feuille du navigateur : il ne prend pas le rouge de <body>.
    root = styled('<body><a id="x">l</a></body>', "body { color: red }")
    assert by_id(root, "x").style["color"] == "blue"


def test_non_inherited_properties_do_not_flow_down():
    root = styled('<div class="a"><span id="x"></span></div>',
                  ".a { margin-top: 30px; padding-left: 9px; background-color: red; display: flex; width: 50px }")
    s = by_id(root, "x").style
    assert s["margin-top"] == "0" and s["padding-left"] == "0"
    assert s["background-color"] == "transparent" and s["display"] == "inline" and s["width"] == "auto"


def test_all_inherited_properties_listed_are_inherited():
    root = styled('<div class="a"><span id="x"></span></div>',
                  ".a { color: red; font-family: monospace; font-style: italic; font-weight: bold; "
                  "line-height: 2; text-align: center; visibility: hidden; white-space: pre }")
    s = by_id(root, "x").style
    assert (s["color"], s["font-family"], s["font-style"], s["font-weight"]) == ("red", "monospace", "italic", "bold")
    assert (s["line-height"], s["text-align"], s["visibility"], s["white-space"]) == ("2", "center", "hidden", "pre")


def test_inherit_keyword_takes_parents_value_even_for_non_inherited_property():
    root = styled('<div class="a"><p id="x">t</p></div>', ".a { margin-left: 7px } p { margin-left: inherit }")
    assert by_id(root, "x").style["margin-left"] == "7px"


def test_inherit_keyword_on_root_child_falls_back_to_initial():
    root = styled('<p id="x">', "p { margin-left: inherit }")
    assert by_id(root, "x").style["margin-left"] == "0"


# ---- valeurs par défaut -----------------------------------------------------------------------------

def test_unknown_element_gets_initial_values():
    root = styled('<blink id="x">')
    assert by_id(root, "x").style == INITIAL


def test_document_root_has_initial_style():
    assert styled("<p>").style == INITIAL


def test_ua_block_and_hidden_elements():
    root = styled('<div id="a"></div><span id="b"></span><li id="c"></li><script id="d"></script><title id="e"></title>')
    assert [by_id(root, i).style["display"] for i in "abcde"] == ["block", "inline", "list-item", "none", "none"]


def test_ua_headings_are_big_and_bold():
    root = styled('<h1 id="a"></h1><h2 id="b"></h2><h6 id="c"></h6>')
    assert [by_id(root, i).style["font-size"] for i in "abc"] == ["32px", "24px", "10.72px"]
    assert by_id(root, "a").style["font-weight"] == "bold"


def test_heading_children_inherit_its_size_and_weight():
    root = styled('<h1><span id="x">t</span></h1>')
    s = by_id(root, "x").style
    assert (s["font-size"], s["font-weight"]) == ("32px", "bold")


def test_ua_bold_italic_link_underline():
    root = styled('<b id="a"></b><em id="b"></em><a id="c"></a>')
    assert by_id(root, "a").style["font-weight"] == "bold"
    assert by_id(root, "b").style["font-style"] == "italic"
    assert by_id(root, "c").style["text-decoration"] == "underline"


def test_text_decoration_is_not_inherited():
    root = styled('<a><span id="x">t</span></a>')
    assert by_id(root, "x").style["text-decoration"] == "none"


def test_ua_body_and_paragraph_margins():
    root = styled('<body id="b"><p id="p"></p></body>')
    assert by_id(root, "b").style["margin-left"] == "8px"
    assert (by_id(root, "p").style["margin-top"], by_id(root, "p").style["margin-bottom"]) == ("16px", "16px")


def test_author_shorthand_overrides_ua_margins():
    root = styled('<p id="x">', "p { margin: 0 }")
    s = by_id(root, "x").style
    assert [s[f"margin-{side}"] for side in ("top", "right", "bottom", "left")] == ["0"] * 4


def test_author_shorthand_two_values():
    root = styled('<div id="x">', "div { padding: 1px 2px }")
    s = by_id(root, "x").style
    assert (s["padding-top"], s["padding-right"], s["padding-bottom"], s["padding-left"]) == ("1px", "2px", "1px", "2px")


def test_unknown_properties_are_kept_as_declared():
    root = styled('<p id="x">', "p { outline: 1px solid red; z-index: 3 }")
    assert by_id(root, "x").style["outline"] == "1px solid red"
    assert by_id(root, "x").style["z-index"] == "3"


# ---- font-size : unités relatives --------------------------------------------------------------------

def test_resolve_font_size_units():
    assert resolve_font_size("20px", 16) == 20
    assert resolve_font_size("2em", 10) == 20
    assert resolve_font_size("150%", 20) == 30
    assert resolve_font_size("12pt", 99) == 16
    assert resolve_font_size(" 1.5EM ", 10) == 15
    assert resolve_font_size("18", 99) == 18  # sans unité : on tolère, en pixels


def test_resolve_font_size_rejects_garbage():
    for v in ["", "big", "10 px", "px", "1rem", "-3px", "calc(1px)"]:
        assert resolve_font_size(v, 16) is None, v


def test_em_is_relative_to_parent_and_compounds():
    root = styled('<div id="a"><div id="b"><div id="c"></div></div></div>',
                  "#a { font-size: 20px } #b { font-size: 2em } #c { font-size: 50% }")
    assert [by_id(root, i).style["font-size"] for i in "abc"] == ["20px", "40px", "20px"]


def test_font_size_inherited_as_pixels_not_as_em():
    root = styled('<div id="a"><span id="b"></span></div>', "#a { font-size: 2em }")
    assert by_id(root, "b").style["font-size"] == "32px"


def test_invalid_font_size_is_ignored():
    root = styled('<div id="a"><span id="b"></span></div>', "#a { font-size: 20px } #b { font-size: banana }")
    assert by_id(root, "b").style["font-size"] == "20px"


def test_font_size_inherit_keyword():
    root = styled('<h1><span id="x">t</span></h1>', "span { font-size: inherit }")
    assert by_id(root, "x").style["font-size"] == "32px"


# ---- du document au style ---------------------------------------------------------------------------

def test_style_document_reads_style_elements():
    root = parse('<style>p { color: red }</style><p id="x">t</p>')
    style_document(root)
    assert by_id(root, "x").style["color"] == "red"


def test_style_document_two_style_elements_later_wins():
    root = parse('<style>p { color: red }</style><style>p { color: blue }</style><p id="x">t</p>')
    style_document(root)
    assert by_id(root, "x").style["color"] == "blue"


def test_every_element_gets_a_full_style_and_text_nodes_have_none():
    root = styled("<div><p>a<b>c</b></p></div>")
    seen = []

    def walk(n):
        if isinstance(n, Element):
            seen.append(n)
            assert set(INITIAL) <= set(n.style)
            for c in n.children:
                walk(c)
        else:
            assert isinstance(n, Text) and not hasattr(n, "style")
    walk(root)
    assert [e.tag for e in seen] == ["#document", "div", "p", "b"]


def test_computing_twice_gives_the_same_result():
    root = parse('<p id="x" class="a">t</p>')
    sheets = [parse_stylesheet(".a { color: red }")]
    compute_styles(root, sheets)
    first = dict(by_id(root, "x").style)
    compute_styles(root, sheets)
    assert by_id(root, "x").style == first


# ---- affichage -----------------------------------------------------------------------------------------

def test_dump_styled_tree_shows_only_non_default_properties():
    root = styled('<h1 id="x">T</h1>', "h1 { color: red }")
    lines = dump_styled_tree(root).splitlines()
    assert lines[0] == "#document"
    assert lines[1].startswith('  <h1 id="x">  {')
    for expected in ("display: block", "font-size: 32px", "font-weight: bold", "color: red"):
        assert expected in lines[1]
    assert "text-align" not in lines[1]  # resté à la valeur par défaut
    assert lines[2] == '    "T"'
