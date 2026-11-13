"""Tests de l'étape 3a : parseur CSS et correspondance des sélecteurs (n'utilise pas specificity)."""

from browser.css import (
    Selector, extract_stylesheets, parse_declarations, parse_selector, parse_selector_list, parse_stylesheet,
)
from browser.html_parser import parse


def rules_of(css):
    """[(sélecteur en texte court, déclarations)] pour comparer facilement."""
    def short(s):
        return (s.tag or "*") + "".join(f"#{i}" for i in s.ids) + "".join(f".{c}" for c in s.classes)
    return [(short(r.selector), r.declarations) for r in parse_stylesheet(css).rules]


# ---- sélecteurs ------------------------------------------------------------------

def test_parse_tag_class_id():
    assert parse_selector("p") == Selector(tag="p")
    assert parse_selector(".intro") == Selector(classes=("intro",))
    assert parse_selector("#top") == Selector(ids=("top",))


def test_parse_compound_selector():
    assert parse_selector("p.intro#top.big") == Selector(tag="p", ids=("top",), classes=("intro", "big"))


def test_parse_universal_selector():
    assert parse_selector("*") == Selector()
    assert parse_selector("*.a") == Selector(classes=("a",))


def test_tag_is_case_insensitive_class_is_not():
    assert parse_selector("DIV.Intro") == Selector(tag="div", classes=("Intro",))


def test_surrounding_whitespace_is_ignored():
    assert parse_selector("  p.a \n") == Selector(tag="p", classes=("a",))


def test_unsupported_selectors_return_none():
    for text in ["", "div p", "div > p", "a + b", "a:hover", "a[href]", "p::first-line", ".", "#", "1p", "a..b"]:
        assert parse_selector(text) is None, text


def el(html):
    return parse(html).children[0]


def test_matches_tag():
    assert Selector(tag="p").matches(el("<p>"))
    assert not Selector(tag="p").matches(el("<div>"))


def test_matches_class_among_several():
    assert Selector(classes=("b",)).matches(el('<p class="a b  c">'))
    assert not Selector(classes=("d",)).matches(el('<p class="a b c">'))
    assert not Selector(classes=("a",)).matches(el("<p>"))


def test_matches_all_required_classes():
    s = Selector(classes=("a", "b"))
    assert s.matches(el('<p class="b a">'))
    assert not s.matches(el('<p class="a">'))


def test_class_match_is_exact_not_substring():
    assert not Selector(classes=("a",)).matches(el('<p class="aa ba">'))


def test_matches_id():
    assert Selector(ids=("x",)).matches(el('<p id="x">'))
    assert not Selector(ids=("x",)).matches(el('<p id="y">'))
    assert not Selector(ids=("x",)).matches(el("<p>"))


def test_matches_compound_needs_every_part():
    s = Selector(tag="p", ids=("x",), classes=("a",))
    assert s.matches(el('<p id="x" class="a">'))
    assert not s.matches(el('<div id="x" class="a">'))
    assert not s.matches(el('<p id="x">'))


def test_universal_matches_anything():
    assert Selector().matches(el("<whatever>"))


# ---- déclarations ----------------------------------------------------------------------

def test_declarations_basic():
    assert parse_declarations("color: red; font-size:12px") == {"color": "red", "font-size": "12px"}


def test_declarations_trailing_semicolon_and_whitespace():
    assert parse_declarations("\n  color : red ;\n  x:y;\n") == {"color": "red", "x": "y"}


def test_declaration_names_are_lowercased():
    assert parse_declarations("COLOR: Red") == {"color": "Red"}


def test_later_declaration_in_same_block_wins():
    assert parse_declarations("color: red; color: blue") == {"color": "blue"}


def test_invalid_declarations_are_skipped_without_breaking_next_ones():
    assert parse_declarations("color red; : x; y: ; ok: 1; 9bad: 2") == {"ok": "1"}


def test_value_may_contain_semicolon_in_string_or_url():
    assert parse_declarations('content: "a;b"; background: url(data:image/png;base64,AA); x: 1') == {
        "content": '"a;b"', "background": "url(data:image/png;base64,AA)", "x": "1"}


def test_value_may_contain_colons():
    assert parse_declarations("background: url(http://x/y.png)") == {"background": "url(http://x/y.png)"}


def test_important_is_stripped():
    assert parse_declarations("color: red !important") == {"color": "red"}
    assert parse_declarations("color: red ! IMPORTANT ;") == {"color": "red"}


def test_margin_shorthand_one_value():
    assert parse_declarations("margin: 5px") == {
        "margin-top": "5px", "margin-right": "5px", "margin-bottom": "5px", "margin-left": "5px"}


def test_margin_shorthand_two_three_four_values():
    assert parse_declarations("margin: 1px 2px") == {
        "margin-top": "1px", "margin-right": "2px", "margin-bottom": "1px", "margin-left": "2px"}
    assert parse_declarations("margin: 1px 2px 3px") == {
        "margin-top": "1px", "margin-right": "2px", "margin-bottom": "3px", "margin-left": "2px"}
    assert parse_declarations("margin: 1px 2px 3px 4px") == {
        "margin-top": "1px", "margin-right": "2px", "margin-bottom": "3px", "margin-left": "4px"}


def test_padding_shorthand_and_invalid_arity():
    assert parse_declarations("padding: 0 auto")["padding-left"] == "auto"
    assert parse_declarations("margin: 1px 2px 3px 4px 5px") == {}


def test_longhand_after_shorthand_overrides_and_vice_versa():
    assert parse_declarations("margin: 0; margin-top: 9px")["margin-top"] == "9px"
    d = parse_declarations("margin-top: 9px; margin: 0")
    assert d["margin-top"] == "0"


# ---- feuilles de style --------------------------------------------------------------------

def test_one_rule():
    assert rules_of("p { color: red }") == [("p", {"color": "red"})]


def test_several_rules_keep_source_order():
    assert [s for s, _ in rules_of("a{x:1} b{x:2} .c{x:3} #d{x:4}")] == ["a", "b", "*.c", "*#d"]


def test_selector_list_makes_one_rule_per_selector():
    assert rules_of("h1, .t, #x { color: red }") == [
        ("h1", {"color": "red"}), ("*.t", {"color": "red"}), ("*#x", {"color": "red"})]


def test_rules_from_a_list_do_not_share_their_declarations_dict():
    a, b = parse_stylesheet("a, b { x: 1 }").rules
    a.declarations["x"] = "changed"
    assert b.declarations["x"] == "1"


def test_unsupported_selector_in_list_is_dropped_others_kept():
    assert rules_of("a, div p, .c { x: 1 }") == [("a", {"x": "1"}), ("*.c", {"x": "1"})]


def test_comments_are_ignored():
    css = "/* début */ p /* au milieu */ { color: /* ici */ red } /* fin */"
    assert rules_of(css) == [("p", {"color": "red"})]


def test_unterminated_comment_swallows_the_rest():
    assert rules_of("a{x:1} /* jamais fermé b{x:2}") == [("a", {"x": "1"})]


def test_at_rules_are_skipped():
    css = '@charset "utf-8"; @import url(x.css); @media (min-width: 1px) { p { x: 1 } .a { y: 2 } } b { z: 3 }'
    assert rules_of(css) == [("b", {"z": "3"})]


def test_rule_with_empty_body_is_kept_with_no_declarations():
    assert rules_of("p {}") == [("p", {})]


def test_unclosed_block_runs_to_end_of_file():
    assert rules_of("p { color: red; ") == [("p", {"color": "red"})]


def test_garbage_does_not_crash():
    for css in ["", "{", "}", "{}", "p", "p {", "}}} p {x:1}", "@", "@media", "p { x: 'unterminated }", ";;;", "a{b:c}}d{e:f}"]:
        parse_stylesheet(css)


def test_braces_in_strings_do_not_close_the_block():
    assert rules_of('p { content: "}"; color: red }') == [("p", {"content": '"}"', "color": "red"})]


def test_origin_is_recorded_on_sheet_and_rules():
    sheet = parse_stylesheet("p{x:1}", origin="user-agent")
    assert sheet.origin == "user-agent" and sheet.rules[0].origin == "user-agent"


# ---- <style> dans le document ----------------------------------------------------------------

def test_extract_stylesheets_in_document_order():
    root = parse("<style>a{x:1}</style><div><style>b{x:2}</style></div>")
    sheets = extract_stylesheets(root)
    assert [[r.selector.tag for r in s.rules] for s in sheets] == [["a"], ["b"]]


def test_extract_stylesheets_none():
    assert extract_stylesheets(parse("<p>x</p>")) == []


def test_style_content_is_not_parsed_as_html():
    # Grâce au contenu « brut » de l'étape 2 : le `>` du sélecteur ne casse rien.
    root = parse("<style>p > a { x: 1 } b { y: 2 }</style>")
    (sheet,) = extract_stylesheets(root)
    assert [r.selector.tag for r in sheet.rules] == ["b"]  # `p > a` non supporté : ignoré


# ---- bordures (utilisées par le layout, étape 4) ---------------------------------------------

def test_border_shorthand_any_order():
    for value in ["1px solid red", "solid 1px red", "red solid 1px"]:
        d = parse_declarations(f"border: {value}")
        assert d == {"border-top-width": "1px", "border-right-width": "1px", "border-bottom-width": "1px",
                     "border-left-width": "1px", "border-style": "solid", "border-color": "red"}, value


def test_border_shorthand_defaults_reset_what_is_not_mentioned():
    d = parse_declarations("border: solid")
    assert d["border-top-width"] == "medium" and d["border-color"] == "currentcolor"
    assert parse_declarations("border: 1px red")["border-style"] == "none"  # pas de style : pas de bordure


def test_border_shorthand_color_with_spaces_inside_parentheses():
    d = parse_declarations("border: 2px solid rgb(1, 2, 3)")
    assert d["border-color"] == "rgb(1, 2, 3)" and d["border-left-width"] == "2px"


def test_border_none_and_zero():
    assert parse_declarations("border: none")["border-style"] == "none"
    assert parse_declarations("border: 0")["border-top-width"] == "0"


def test_border_width_shorthand_like_margin():
    d = parse_declarations("border-width: 1px 2px")
    assert (d["border-top-width"], d["border-right-width"], d["border-bottom-width"], d["border-left-width"]) == \
        ("1px", "2px", "1px", "2px")
    assert "border-style" not in d  # border-width ne touche pas au style


def test_border_longhand_width_is_kept_as_is():
    assert parse_declarations("border-left-width: 4px") == {"border-left-width": "4px"}


# ---- liste de sélecteurs « tout ou rien » (querySelectorAll, étape 6) ----------------------------------------------

def test_parse_selector_list():
    assert parse_selector_list("p, .a ,#x") == [Selector(tag="p"), Selector(classes=("a",)), Selector(ids=("x",))]
    assert parse_selector_list("p") == [Selector(tag="p")]


def test_parse_selector_list_is_all_or_nothing():
    assert parse_selector_list("p, div p") is None  # contrairement à une feuille de style, aucun sélecteur « partiel »
    assert parse_selector_list("") is None and parse_selector_list("p,,a") is None
