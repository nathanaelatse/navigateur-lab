"""Tests de `specificity()` : à toi de la faire passer (browser/css.py, sous-étape 3b).

Ils échouent tant que la fonction lève NotImplementedError. Lance-les avec :

    .venv/bin/pytest tests/test_css_specificity.py -q

La spécificité est un tuple (ids, classes, balises). Les tests du bas vérifient que la
comparaison de deux tuples donne le bon vainqueur : c'est tout ce dont la cascade a besoin.
"""

from browser.css import cascade, parse_selector, parse_stylesheet, specificity
from browser.html_parser import parse


def spec(selector_text):
    return specificity(parse_selector(selector_text))


# ---- chaque ingrédient -----------------------------------------------------------------

def test_universal_counts_for_nothing():
    assert spec("*") == (0, 0, 0)


def test_tag():
    assert spec("p") == (0, 0, 1)


def test_class():
    assert spec(".intro") == (0, 1, 0)


def test_id():
    assert spec("#top") == (1, 0, 0)


# ---- composés -----------------------------------------------------------------------------

def test_tag_and_class():
    assert spec("p.intro") == (0, 1, 1)


def test_tag_and_id():
    assert spec("p#top") == (1, 0, 1)


def test_everything_at_once():
    assert spec("p.a.b#x") == (1, 2, 1)


def test_universal_with_class_counts_only_the_class():
    assert spec("*.a") == (0, 1, 0)


def test_repeated_class_counts_each_time():
    assert spec(".a.a") == (0, 2, 0)


def test_two_ids_count_two():
    assert spec("#a#b") == (2, 0, 0)


def test_order_inside_the_selector_does_not_matter():
    assert spec("#x.a.b") == spec(".b#x.a") == (1, 2, 0)


# ---- forme du résultat ------------------------------------------------------------------------

def test_result_is_a_tuple_of_three_ints():
    result = spec("p.a#x")
    assert isinstance(result, tuple) and len(result) == 3
    assert all(isinstance(n, int) for n in result)


# ---- comparaisons : le vrai rôle de la spécificité ---------------------------------------------

def test_class_beats_tag():
    assert spec(".a") > spec("p")


def test_id_beats_class():
    assert spec("#x") > spec(".a")


def test_id_beats_any_number_of_classes():
    # Le piège classique : si tu additionnes (a*100 + b*10 + c), 10 classes battent un id.
    assert spec("#x") > spec(".a.b.c.d.e.f.g.h.i.j.k.l")


def test_class_beats_any_number_of_tags():
    assert spec(".a") > (0, 0, 99)


def test_more_tags_beat_fewer_tags():
    assert spec("p") > spec("*")
    assert spec("p.a") > spec(".a")


def test_equal_specificity():
    assert spec("p") == spec("div")
    assert spec(".a") == spec(".b")


# ---- branchement avec la cascade (dès que specificity() existe) ----------------------------------

def test_cascade_picks_the_more_specific_rule_even_if_it_comes_first():
    p = parse('<p id="x" class="a">').children[0]
    rules = parse_stylesheet("#x { color: green } .a { color: red } p { color: blue }").rules
    assert cascade(p, rules)["color"] == "green"


def test_cascade_falls_back_to_source_order_on_a_tie():
    p = parse("<p>").children[0]
    rules = parse_stylesheet("p { color: red } p { color: blue }").rules
    assert cascade(p, rules)["color"] == "blue"
