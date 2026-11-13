"""Tests du parseur HTML (étape 2), dans l'ordre des sous-étapes."""

from browser.html_parser import Element, Text, dump_tree, inner_html, main, parse, to_html


def sx(node):
    """Arbre -> notation compacte : p['a', b['c']] ; les attributs ne sont pas montrés."""
    if isinstance(node, Text):
        return repr(node.text)
    inner = ", ".join(sx(c) for c in node.children)
    return f"{node.tag}[{inner}]"


def tree(html):
    """Enfants de #document en notation compacte (les tests lisent comme l'arbre)."""
    return ", ".join(sx(c) for c in parse(html).children)


# ---- 2a : balises + texte -------------------------------------------------

def test_plain_text():
    assert tree("bonjour") == "'bonjour'"


def test_empty_input():
    assert parse("").children == []


def test_nested_tags_and_text():
    assert tree("<div><p>hello</p></div>") == "div[p['hello']]"


def test_text_around_tags_keeps_order():
    assert tree("a<b>b</b>c") == "'a', b['b'], 'c'"


def test_tag_names_are_case_insensitive():
    assert tree("<DIV><P>x</p></Div>") == "div[p['x']]"


def test_parent_links():
    doc = parse("<div><p>x</p></div>")
    div = doc.children[0]
    p = div.children[0]
    assert p.parent is div and div.parent is doc
    assert p.children[0].parent is p


def test_literal_less_than_is_text():
    assert tree("1 < 2") == "'1 < 2'"
    assert tree("a <3 b") == "'a <3 b'"
    assert tree("fin <") == "'fin <'"


def test_entities_are_decoded_in_text():
    assert tree("<p>a &amp; b &lt;c&gt;</p>") == "p['a & b <c>']"


def test_stray_end_tag_is_ignored():
    assert tree("a</p>b") == "'ab'"
    assert tree("</br>x") == "'x'"


def test_end_tag_closes_forgotten_inner_tags():
    # </div> ferme aussi le <b> oublié ; le "y" n'est donc PAS dans le div.
    assert tree("<div><b>x</div>y") == "div[b['x']], 'y'"


def test_unclosed_tags_at_eof_are_kept():
    assert tree("<div><p>x") == "div[p['x']]"


def test_comments_doctype_and_pi_are_dropped():
    assert tree("<!DOCTYPE html><!-- note --><p>x</p><?xml v?>") == "p['x']"


def test_comment_inside_text_merges_text():
    assert tree("a<!-- c -->b") == "'ab'"


def test_comment_may_contain_tags_and_dashes():
    assert tree("<!-- <p>pas une balise</p> - -- ->  --><i>x</i>") == "i['x']"


# ---- 2b : attributs ---------------------------------------------------------

def attrs(html):
    return parse(html).children[0].attributes


def test_double_quoted_attribute():
    assert attrs('<a href="http://x/?a=1&amp;b=2">') == {"href": "http://x/?a=1&b=2"}


def test_single_quoted_attribute_can_contain_double_quotes():
    assert attrs("""<p title='dit "oui"'>""") == {"title": 'dit "oui"'}


def test_unquoted_attribute():
    assert attrs("<a href=/x/y?z=1 id=a>") == {"href": "/x/y?z=1", "id": "a"}


def test_boolean_attribute():
    assert attrs("<input disabled>") == {"disabled": ""}
    assert attrs("<input disabled name=x>") == {"disabled": "", "name": "x"}
    assert attrs("<input name=x disabled>") == {"name": "x", "disabled": ""}


def test_attribute_names_are_lowercased_but_values_are_not():
    assert attrs('<a HREF="/Ab">') == {"href": "/Ab"}


def test_spaces_around_equals_and_newlines():
    assert attrs('<a\n  href = "/x"\n  class\t=\ty\n>') == {"href": "/x", "class": "y"}


def test_duplicate_attribute_first_wins():
    assert attrs('<a x="1" x="2">') == {"x": "1"}


def test_quoted_value_may_contain_gt_and_slash():
    el = parse('<a title="a > b / c">t</a>').children[0]
    assert el.attributes == {"title": "a > b / c"}
    assert sx(el) == "a['t']"


def test_empty_values():
    assert attrs('<a href="" x=\'\'>') == {"href": "", "x": ""}
    assert attrs("<a href=>") == {"href": ""}


def test_attribute_right_after_quote_without_space():
    assert attrs('<a x="1"y="2">') == {"x": "1", "y": "2"}


def test_attributes_on_end_tag_are_ignored():
    assert tree('<p>x</p class="z">y') == "p['x'], 'y'"


# ---- 2c : auto-fermantes et void -----------------------------------------------

def test_void_tags_have_no_children_without_slash():
    assert tree("a<br>b<img src=x>c") == "'a', br[], 'b', img[], 'c'"


def test_void_tag_with_slash():
    assert tree("a<br/>b<br />c") == "'a', br[], 'b', br[], 'c'"


def test_self_closing_with_attributes():
    el = parse('<img src="a.png" alt=x />t').children[0]
    assert el.attributes == {"src": "a.png", "alt": "x"}
    assert tree('<img src="a.png" alt=x />t') == "img[], 't'"


def test_self_closing_unquoted_value_keeps_slash_in_value():
    # Comme dans les navigateurs : `href=/a/` mange le '/', donc `<a href=/a/>` N'EST PAS auto-fermant.
    el = parse("<a href=/a/>x</a>").children[0]
    assert el.attributes == {"href": "/a/"}
    assert sx(el) == "a['x']"


def test_self_closing_boolean_attribute():
    assert tree("<input disabled/>x") == "input[], 'x'"


def test_self_closing_non_void_tag():
    assert tree("<div/>x") == "div[], 'x'"


def test_end_tag_of_void_is_ignored():
    assert tree("<br></br>x") == "br[], 'x'"


# ---- 2d : balises oubliées --------------------------------------------------------

def test_p_closed_by_next_p():
    assert tree("<p>a<p>b") == "p['a'], p['b']"


def test_p_closed_by_block_element():
    assert tree("<p>a<div>b</div>") == "p['a'], div['b']"
    assert tree("<p>a<ul><li>b</ul>") == "p['a'], ul[li['b']]"


def test_p_not_closed_by_inline_element():
    assert tree("<p>a<b>b</b>c") == "p['a', b['b'], 'c']"


def test_p_closed_even_through_forgotten_inline():
    assert tree("<p><b>x<div>y") == "p[b['x']], div['y']"


def test_p_in_table_cell_is_not_closed_by_outer_p():
    assert tree("<p>a<table><tr><td><p>b</td></tr></table>") == "p['a'], table[tr[td[p['b']]]]"


def test_li_closed_by_next_li():
    assert tree("<ul><li>a<li>b<li>c</ul>") == "ul[li['a'], li['b'], li['c']]"


def test_nested_list_li_stays_inside():
    assert tree("<ul><li>a<ul><li>b<li>c</ul><li>d</ul>") == \
        "ul[li['a', ul[li['b'], li['c']]], li['d']]"


def test_li_with_forgotten_p_inside():
    assert tree("<ul><li><p>a<li><p>b</ul>") == "ul[li[p['a']], li[p['b']]]"


def test_definition_list():
    assert tree("<dl><dt>a<dd>b<dt>c<dd>d</dl>") == "dl[dt['a'], dd['b'], dt['c'], dd['d']]"


def test_table_cells_and_rows():
    assert tree("<table><tr><td>a<td>b<tr><td>c</table>") == \
        "table[tr[td['a'], td['b']], tr[td['c']]]"


def test_th_and_td_close_each_other():
    assert tree("<table><tr><th>h<td>d</table>") == "table[tr[th['h'], td['d']]]"


def test_table_sections():
    assert tree("<table><thead><tr><td>a<tbody><tr><td>b</table>") == \
        "table[thead[tr[td['a']]], tbody[tr[td['b']]]]"


def test_option_closed_by_next_option():
    assert tree("<select><option>a<option>b</select>") == "select[option['a'], option['b']]"


# ---- contenu brut : script / style ---------------------------------------------------

def test_script_content_is_raw_text():
    html = '<script>if (a<b && c>d) { s = "<p>x</p>"; }</script><p>y</p>'
    assert tree(html) == "script['if (a<b && c>d) { s = \"<p>x</p>\"; }'], p['y']"


def test_script_content_is_not_entity_decoded():
    assert tree("<script>a &amp; b</script>") == "script['a &amp; b']"


def test_script_end_tag_is_case_insensitive_and_prefix_safe():
    assert tree("<script>x</scripty>y</SCRIPT >z") == "script['x</scripty>y'], 'z'"


def test_style_raw_text_and_text_entities_resume_after():
    assert tree("<style>a>b{}</style>&lt;") == "style['a>b{}'], '<'"


def test_unterminated_script_runs_to_eof():
    assert tree("<script>var a = 1;") == "script['var a = 1;']"


# ---- fichiers cassés / fin de fichier brutale -------------------------------------------

def test_incomplete_tag_at_eof_is_dropped_text_before_is_kept():
    assert tree("abc<div cla") == "'abc'"
    assert tree('x<a href="ab') == "'x'"


def test_garbage_does_not_crash():
    for html in ["<", "<<<", "</", "<!", "<!-", "<!--", "<a =", "<a b='", "<>", "<a/b/c>", "&", "<p/<"]:
        parse(html)  # pas d'exception, c'est tout ce qu'on demande ici


# ---- affichage de débogage --------------------------------------------------------------

def test_dump_tree_exact():
    assert dump_tree(parse('<div id="a"><p>x &amp; y</p><br></div>')) == "\n".join([
        "#document",
        '  <div id="a">',
        "    <p>",
        '      "x & y"',
        "    <br>",
    ])


def test_dump_tree_hides_whitespace_only_text_by_default():
    doc = parse("<ul>\n  <li>a</li>\n</ul>")
    assert '"\\n' not in dump_tree(doc).replace('"a"', "")
    assert '"\\n  "' in dump_tree(doc, show_whitespace=True)


def test_dump_tree_escapes_newlines_so_one_node_is_one_line():
    assert dump_tree(parse("a\nb")).splitlines() == ["#document", '  "a\\nb"']


def test_main_reads_a_file(tmp_path, capsys):
    f = tmp_path / "p.html"
    f.write_text("<p>é</p>", encoding="utf-8")
    main([str(f)])
    assert capsys.readouterr().out == '#document\n  <p>\n    "é"\n'


def test_element_and_text_are_the_only_node_types():
    def kinds(n):
        yield type(n)
        for c in getattr(n, "children", []):
            yield from kinds(c)
    assert set(kinds(parse("<!doctype html><p>a<!--c-->b<br><script>s</script>"))) == {Element, Text}


# ---- sérialisation : DOM -> HTML (étape 6, innerHTML) ------------------------------------------------------------

def html_of(markup):
    return "".join(to_html(c) for c in parse(markup).children)


def test_to_html_basic_and_attributes():
    assert html_of('<div id=a class="x y"><p>hi</p></div>') == '<div id="a" class="x y"><p>hi</p></div>'


def test_to_html_escapes_text_and_attribute_values():
    assert html_of("<p>a &lt; b &amp; c</p>") == "<p>a &lt; b &amp; c</p>"
    assert html_of("<a title='say \"hi\" &amp; go'>x</a>") == '<a title="say &quot;hi&quot; &amp; go">x</a>'


def test_to_html_void_elements_have_no_closing_tag_and_boolean_attributes_get_empty_value():
    assert html_of("a<br>b<img src=x><input disabled>") == 'a<br>b<img src="x"><input disabled="">'


def test_to_html_script_and_style_content_is_raw():
    assert html_of("<script>if (a < b && c) {}</script><style>a>b{}</style>") == \
        "<script>if (a < b && c) {}</script><style>a>b{}</style>"


def test_inner_html_excludes_the_element_itself():
    div = parse("<div id=a>x<b>y</b></div>").children[0]
    assert inner_html(div) == "x<b>y</b>"


def test_to_html_round_trips_through_parse():
    for markup in ["<p>a<p>b", "<ul><li>a<li>b <b>c</b></ul>", "<div><br><img src=a alt='x y'>t</div>",
                   "<table><tr><td>a<td>b</table>", "<p>&lt;tag&gt; &amp; more</p>", "<script>a<b</script>x"]:
        once = html_of(markup)
        assert html_of(once) == once  # une fois sérialisé, c'est un point fixe
        assert tree(once) == tree(markup)  # et l'arbre est le même
