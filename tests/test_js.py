"""Tests de l'étape 6 : JavaScript (document minimal, isolation, timers, re-rendu après modification du DOM).

Nécessite dukpy (`pip install dukpy`) : sans lui, tout ce fichier est ignoré.
"""

import pytest

dukpy = pytest.importorskip("dukpy")

import browser.js as js_module
from browser.html_parser import Element, Text, parse
from browser.js import REMOVED_GLOBALS, JSRuntime
from browser.network import NetworkError, Response
from browser.paint import DrawText
from browser.tab import START_URL, Tab
from conftest import make_response, request_path

pytestmark = pytest.mark.usefixtures("constant_specificity")  # voir tests/conftest.py

PAGE = ('<div id=a class="x y"><p>one</p><p id=b>two <b>bold</b></p></div><a href="/z">l</a>'
        '<input disabled name=n>')


def runtime(html=PAGE, **kwargs):
    dom = parse(html)
    return JSRuntime(dom, **kwargs), dom


# ---- document : requêtes ------------------------------------------------------------------------------------

def test_query_selector_all_by_tag_class_id_and_compound():
    rt, _ = runtime()
    count = lambda sel: rt.run(f"document.querySelectorAll({sel!r}).length")
    assert (count("p"), count(".x"), count("#b"), count("div.x.y"), count("p#b")) == (2, 1, 1, 1, 1)
    assert count("span") == 0 and count("*") == 6  # div, p, p, b, a, input


def test_query_selector_all_accepts_a_selector_list_in_document_order():
    rt, _ = runtime()
    assert rt.run("document.querySelectorAll('a, p, div').map(function (n) { return n.tagName }).join()") == "DIV,P,P,A"


def test_node_query_searches_descendants_only():
    rt, _ = runtime()
    assert rt.run("document.getElementById('a').querySelectorAll('p').length") == 2
    assert rt.run("document.getElementById('a').querySelectorAll('div').length") == 0  # pas lui-même
    assert rt.run("document.getElementById('b').querySelector('b').textContent") == "bold"


def test_query_selector_returns_first_or_null():
    rt, _ = runtime()
    assert rt.run("document.querySelector('p').textContent") == "one"
    assert rt.run("document.querySelector('nope') === null") is True


def test_get_element_by_id():
    rt, _ = runtime()
    assert rt.run("document.getElementById('b').tagName") == "P"
    assert rt.run("document.getElementById('missing') === null") is True


def test_same_node_gives_the_same_js_object():
    rt, _ = runtime()
    assert rt.run("document.getElementById('b') === document.querySelectorAll('p')[1]") is True
    assert rt.run("document.querySelectorAll('p')[0] === document.querySelectorAll('p')[1]") is False


def test_unsupported_selector_returns_empty_and_warns():
    rt, _ = runtime()
    assert rt.run("document.querySelectorAll('div p').length") == 0
    assert rt.console == [("warn", "sélecteur non supporté : 'div p'")]


def test_body_is_found_when_present():
    rt, _ = runtime("<body><p>x</p></body>")
    assert rt.run("document.body.tagName") == "BODY"
    assert runtime("<p>x</p>")[0].run("document.body === null") is True


def test_window_is_the_global_object():
    rt, _ = runtime()
    assert rt.run("window.document === document && window === this") is True


# ---- nœuds : attributs, innerHTML, textContent -----------------------------------------------------------------

def test_get_attribute():
    rt, _ = runtime()
    assert rt.run("document.getElementById('a').getAttribute('class')") == "x y"
    assert rt.run("document.getElementById('a').getAttribute('CLASS')") == "x y"      # nom insensible à la casse
    assert rt.run("document.getElementById('a').getAttribute('nope') === null") is True  # absent -> null
    assert rt.run("document.querySelector('input').getAttribute('disabled') === ''") is True  # booléen -> ""


def test_set_attribute_changes_the_dom_and_marks_it_dirty():
    rt, dom = runtime()
    assert not rt.dirty
    rt.run("document.getElementById('a').setAttribute('Data-N', 5)")
    assert rt.dirty and dom.children[0].attributes["data-n"] == "5"


def test_reading_never_marks_the_dom_dirty():
    rt, _ = runtime()
    rt.run("var n = document.getElementById('b'); n.innerHTML; n.textContent; n.getAttribute('id'); n.tagName; "
           "document.querySelectorAll('p')")
    assert not rt.dirty


def test_inner_html_getter_serializes_children():
    rt, _ = runtime("<div id=d>a &lt; b <br><i class='k'>x</i></div>")
    assert rt.run("document.getElementById('d').innerHTML") == 'a &lt; b <br><i class="k">x</i>'


def test_inner_html_setter_parses_markup_and_replaces_children():
    rt, dom = runtime("<div id=d><p>old</p></div>")
    div = dom.children[0]
    old = div.children[0]
    rt.run("document.getElementById('d').innerHTML = 'new <b id=n>bold</b> &amp; <i>it</i>'")
    assert rt.dirty
    assert [type(c).__name__ for c in div.children] == ["Text", "Element", "Text", "Element"]
    assert div.children[1].tag == "b" and div.children[1].parent is div and old.parent is None
    assert rt.run("document.getElementById('n').textContent") == "bold"  # les nouveaux nœuds sont atteignables
    assert rt.run("document.getElementById('d').innerHTML") == "new <b id=\"n\">bold</b> &amp; <i>it</i>"


def test_inner_html_empty_string_clears():
    rt, dom = runtime("<div id=d><p>old</p></div>")
    rt.run("document.getElementById('d').innerHTML = ''")
    assert dom.children[0].children == []


def test_inner_html_of_script_is_text_not_markup():
    rt, dom = runtime("<script id=s></script>")
    rt.run("document.getElementById('s').innerHTML = 'if (a < b) { x = \"<p>\" }'")
    (child,) = dom.children[0].children
    assert isinstance(child, Text) and child.text == 'if (a < b) { x = "<p>" }'


def test_text_content_reads_all_descendant_text():
    rt, _ = runtime()
    assert rt.run("document.getElementById('a').textContent") == "onetwo bold"


def test_text_content_setter_never_parses_markup():
    rt, dom = runtime("<div id=d><p>old</p></div>")
    rt.run("document.getElementById('d').textContent = '<b>not bold</b>'")
    div = dom.children[0]
    assert [c.text for c in div.children] == ["<b>not bold</b>"]  # un seul Text, aucun <b>
    assert rt.run("document.getElementById('d').innerHTML") == "&lt;b&gt;not bold&lt;/b&gt;"
    assert rt.dirty


def test_tag_name_is_uppercase():
    rt, _ = runtime()
    assert rt.run("document.querySelector('input').tagName") == "INPUT"


def test_bridges_ignore_unknown_or_forged_handles():
    rt, _ = runtime()
    assert rt._js_get_attribute("abc", "x") is None and rt._js_get_attribute(None, "x") is None
    assert rt._js_query_all(9999, "p") == [] and rt._js_get_inner_html(-1) == ""
    rt._js_set_attribute(9999, "x", "y")
    rt._js_set_inner_html("zzz", "<p>")
    assert not rt.dirty


# ---- console et erreurs -----------------------------------------------------------------------------------------------

def test_console_levels_and_argument_joining():
    rt, _ = runtime()
    seen = []
    rt._console_sink = lambda level, message: seen.append((level, message))
    rt.run("console.log('a', 1, true, null); console.warn('w'); console.error('e'); console.info('i')")
    assert rt.console == seen == [("log", "a 1 true null"), ("warn", "w"), ("error", "e"), ("info", "i")]


def test_run_returns_the_last_expression():
    rt, _ = runtime()
    assert rt.run("1 + 2") == 3 and rt.run("'a' + 'b'") == "ab" and rt.run("[1, 2].map(function (x) { return x * 2 })") == [2, 4]
    assert rt.run("undefined") is None


def test_errors_are_logged_not_raised_and_state_survives():
    rt, _ = runtime()
    rt.run("var kept = 41")
    assert rt.run("this is not javascript") is None
    assert rt.run("undefinedFunction()") is None
    assert rt.run("throw new Error('boom')") is None
    assert [level for level, _ in rt.console] == ["error"] * 3
    assert "SyntaxError" in rt.console[0][1] and "ReferenceError" in rt.console[1][1] and "boom" in rt.console[2][1]
    assert rt.run("kept + 1") == 42  # le contexte reste utilisable


def test_globals_persist_between_scripts():
    rt, _ = runtime()
    rt.run("var counter = 1")
    rt.run("counter = counter + 1")
    assert rt.run("counter") == 2


# ---- timers (horloge simulée) ---------------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_timeout_fires_only_when_due():
    clock = Clock()
    rt, _ = runtime(clock=clock)
    rt.run("var fired = 0; setTimeout(function () { fired += 1 }, 500)")
    assert rt.pending_timers == 1
    clock.now = 0.4
    assert rt.tick() == 0 and rt.run("fired") == 0
    clock.now = 0.5
    assert rt.tick() == 1 and rt.run("fired") == 1 and rt.pending_timers == 0
    assert rt.tick() == 0  # une seule fois


def test_timeouts_fire_by_due_time_then_creation_order_with_arguments():
    clock = Clock()
    rt, _ = runtime(clock=clock)
    rt.run("var order = []; function add(x) { order.push(x) }"
           "setTimeout(add, 300, 'c'); setTimeout(add, 100, 'a'); setTimeout(add, 100, 'b')")
    clock.now = 1
    assert rt.tick() == 3 and rt.run("order.join('')") == "abc"


def test_clear_timeout():
    clock = Clock()
    rt, _ = runtime(clock=clock)
    rt.run("var hit = false; var id = setTimeout(function () { hit = true }, 10); clearTimeout(id)")
    clock.now = 1
    assert rt.pending_timers == 0 and rt.tick() == 0 and rt.run("hit") is False


def test_a_callback_can_cancel_another_due_timer():
    clock = Clock()
    rt, _ = runtime(clock=clock)
    rt.run("var log = []; var second = setTimeout(function () { log.push('second') }, 20);"
           "setTimeout(function () { log.push('first'); clearTimeout(second) }, 10)")
    clock.now = 1
    assert rt.tick() == 1 and rt.run("log.join()") == "first"


def test_timer_set_by_a_callback_waits_for_the_next_tick():
    clock = Clock()
    rt, _ = runtime(clock=clock)
    rt.run("var n = 0; function again() { n += 1; setTimeout(again, 0) } setTimeout(again, 0)")
    clock.now = 1
    assert rt.tick() == 1 and rt.run("n") == 1  # pas de boucle infinie dans un seul tick
    assert rt.tick() == 1 and rt.run("n") == 2


def test_timeout_callback_error_is_logged_and_others_still_run():
    clock = Clock()
    rt, _ = runtime(clock=clock)
    rt.run("var ok = false; setTimeout(function () { nope() }, 1); setTimeout(function () { ok = true }, 2)")
    clock.now = 1
    rt.tick()
    assert rt.run("ok") is True and "nope is not defined" in rt.console[0][1]


def test_timeout_with_a_non_function_is_ignored():
    clock = Clock()
    rt, _ = runtime(clock=clock)
    rt.run("setTimeout('alert(1)', 0)")
    clock.now = 1
    assert rt.tick() == 1 and rt.console == []


def test_timeout_modifying_the_dom_marks_it_dirty():
    clock = Clock()
    rt, _ = runtime(clock=clock)
    rt.run("setTimeout(function () { document.getElementById('a').setAttribute('x', 'y') }, 5)")
    assert not rt.dirty
    clock.now = 1
    rt.tick()
    assert rt.dirty


# ---- isolation ------------------------------------------------------------------------------------------------------------------

def test_dangerous_engine_globals_are_gone():
    rt, _ = runtime()
    for name in REMOVED_GLOBALS:
        assert rt.run(f"typeof {name}") == "undefined", name
    assert rt.run("typeof require + typeof process") == "undefinedundefined"


def test_page_cannot_see_the_python_bridge():
    rt, _ = runtime()
    assert rt.run("typeof call_python") == "undefined"
    assert rt.run("typeof dukpy") == "undefined"
    assert rt.run("typeof Function('return this')().call_python") == "undefined"  # via le constructeur de Function
    assert rt.run("typeof this.call_python + typeof window.call_python") == "undefinedundefined"
    assert rt.run("typeof call_python") == "undefined"  # y compris à la 2e exécution (dukpy le réinjecte à chaque fois)


def test_environment_variables_do_not_leak(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "s3cret-value-123")
    rt, _ = runtime()
    assert rt.run("typeof process") == "undefined"
    rt.run("try { console.log(process.env.SECRET_TOKEN) } catch (e) { console.log('blocked: ' + e) }")
    assert "s3cret-value-123" not in str(rt.console)
    assert rt.console == [("log", "blocked: ReferenceError: process is not defined")]


def test_page_cannot_read_or_run_local_files_through_require(tmp_path):
    secret = tmp_path / "secret.js"
    secret.write_text("var leaked = 'file-was-run'; 'top-secret-text'")
    rt, _ = runtime()
    assert rt.run(f"require({str(secret)!r})") is None
    assert rt.run("typeof leaked") == "undefined"  # le fichier n'a pas été exécuté
    assert "require is not defined" in rt.console[0][1] and "top-secret-text" not in str(rt.console)


def test_only_known_globals_were_added_or_removed():
    """Inventaire : alerte si une nouvelle version de dukpy ajoute un global que la page pourrait atteindre."""
    pristine = set(dukpy.JSInterpreter().evaljs("Object.getOwnPropertyNames(this)"))
    rt, _ = runtime()
    ours = set(rt.run("Object.getOwnPropertyNames(this)"))
    assert ours - pristine == {"document", "window", "setTimeout", "clearTimeout", "__fireTimer"}
    assert pristine - ours == set(REMOVED_GLOBALS) | {"call_python", "dukpy"}


def test_python_objects_are_unreachable_from_js():
    rt, _ = runtime()
    shape = rt.run("(function () { var n = document.querySelector('p');"
                   "return [typeof n.children, typeof n.attributes, typeof n.tag, typeof n.parent, typeof n.style,"
                   "        Object.keys(n).length, typeof n.__handle].join() })()")
    assert shape == "undefined,undefined,undefined,undefined,undefined,0,number"  # une poignée, rien d'autre


def test_js_receives_copies_not_references():
    rt, dom = runtime("<p id=p class=c>x</p>")
    rt.run("var a = document.getElementById('p').getAttribute('class'); a = 'changed'")
    assert dom.children[0].attributes["class"] == "c"  # modifier la valeur JS ne touche pas le DOM Python


def test_each_runtime_has_its_own_globals_dom_and_timers():
    clock = Clock()
    a, dom_a = runtime("<p id=p>a</p>", clock=clock)
    b, dom_b = runtime("<p id=p>b</p>", clock=clock)
    a.run("var leak = 1; document.getElementById('p').textContent = 'changed'; setTimeout(function () {}, 1)")
    assert b.run("typeof leak") == "undefined"
    assert b.run("document.getElementById('p').textContent") == "b" and not b.dirty
    assert (a.pending_timers, b.pending_timers) == (1, 0)


def test_node_handles_cannot_cross_runtimes():
    a, _ = runtime("<p id=p>a</p><p id=q>q</p>")
    b, _ = runtime("<p id=p>b</p>")
    handle = a.run("document.getElementById('q').__handle")
    assert b._element(handle) is None or b._element(handle).attributes.get("id") != "q"


def test_runtime_requires_dukpy(monkeypatch):
    monkeypatch.setattr(js_module, "AVAILABLE", False)
    with pytest.raises(RuntimeError, match="dukpy"):
        JSRuntime(parse("<p>"))


# ---- intégration dans l'onglet : le DOM change, la page est refaite --------------------------------------------------------------

STYLE = "<style>* { font-size: 10px; margin: 0 }</style>"


def make_fetcher(pages):
    calls = []

    def fetcher(url):
        calls.append(url)
        if url not in pages:
            raise NetworkError(f"connexion refusée : {url}")
        page = pages[url]
        return page if isinstance(page, Response) else Response(url, 200, "OK", {"content-type": "text/html"}, page.encode())

    fetcher.calls = calls
    return fetcher


def make_tab(pages, width=100, height=50, **kwargs):
    clock = Clock()
    fetcher = make_fetcher(pages)
    return Tab(width, height, fetcher=fetcher, clock=clock, **kwargs), clock, fetcher


def shown(tab):
    return [c.text for c in tab.display_list.commands if isinstance(c, DrawText)]


def load(html, **kwargs):
    tab, clock, fetcher = make_tab({"http://h/": html}, **kwargs)
    tab.load("http://h/")
    return tab, clock, fetcher


def test_script_modifies_the_dom_before_the_first_render():
    tab, _, _ = load(STYLE + "<div id=x>old</div><script>document.getElementById('x').innerHTML = 'new <b>bold</b>'</script>")
    assert shown(tab) == ["new", "bold"]
    assert [c.font.bold for c in tab.display_list.commands if isinstance(c, DrawText)] == [False, True]


def test_script_text_is_never_displayed():
    tab, _, _ = load(STYLE + "<div>visible</div><script>var secret = 'not shown'</script>")
    assert shown(tab) == ["visible"]


def test_scripts_run_in_document_order_and_share_globals():
    tab, _, _ = load(STYLE + "<div id=x></div><script>var n = 1</script>"
                     "<script>document.getElementById('x').textContent = 'n=' + (n + 1)</script>")
    assert shown(tab) == ["n=2"]


def test_external_script_is_fetched_relative_to_the_page():
    pages = {"http://h/dir/page": STYLE + "<div id=x>old</div><script src='app.js'></script>",
             "http://h/dir/app.js": "document.getElementById('x').textContent = 'from app.js'"}
    tab, _, fetcher = make_tab(pages)
    tab.load("http://h/dir/page")
    assert shown(tab) == ["from", "app.js"] and fetcher.calls == ["http://h/dir/page", "http://h/dir/app.js"]


def test_missing_external_script_is_logged_and_the_page_still_renders():
    tab, _, _ = load(STYLE + "<script src='/missing.js'></script><div>still here</div>"
                     "<script>console.log('next script ran')</script>")
    assert shown(tab) == ["still", "here"]
    assert [m for _, m in tab.console_messages if "missing.js" in m] and ("log", "next script ran") in tab.console_messages


def test_only_javascript_script_types_run():
    tab, _, _ = load(STYLE + "<script type='text/template'>console.log('template')</script>"
                     "<script type='application/javascript'>console.log('app')</script>"
                     "<script type='text/javascript'>console.log('text')</script>"
                     "<script type='module'>console.log('module')</script>"
                     "<script>console.log('plain')</script>")
    assert [m for _, m in tab.console_messages] == ["app", "text", "plain"]


def test_a_broken_script_does_not_stop_the_page():
    tab, _, _ = load(STYLE + "<script>this is broken(</script><div id=x>a</div>"
                     "<script>document.getElementById('x').textContent = 'b'</script>")
    assert shown(tab) == ["b"] and tab.console_messages[0][0] == "error"


def test_script_inserted_through_inner_html_does_not_run():
    tab, _, _ = load(STYLE + "<div id=x></div><script>document.getElementById('x').innerHTML = "
                     "'<script>console.log(\"injected\")<\\/script><p>after</p>'</script>")
    assert shown(tab) == ["after"] and tab.console_messages == []


def test_pages_without_scripts_never_create_an_interpreter():
    tab, _, _ = load(STYLE + "<div>plain</div>")
    assert tab._runtime is None and tab.tick() is False


def test_javascript_disabled_ignores_scripts():
    tab, _, _ = load(STYLE + "<div id=x>old</div><script>document.getElementById('x').textContent = 'new'</script>",
                     javascript=False)
    assert shown(tab) == ["old"] and tab.console_messages == [] and tab.evaluate("1 + 1") is None


def test_tab_without_dukpy_ignores_scripts(monkeypatch):
    monkeypatch.setattr(js_module, "AVAILABLE", False)
    tab, _, _ = load(STYLE + "<div id=x>old</div><script>document.getElementById('x').textContent = 'new'</script>")
    assert tab.javascript is False and shown(tab) == ["old"]


# -- après le premier rendu : timers et console --

def test_timer_changes_the_dom_and_the_page_is_restyled():
    tab, clock, _ = load("<style>* { font-size: 10px; margin: 0 } .red { color: red }</style><div id=d>hi</div>"
                         "<script>setTimeout(function () { document.getElementById('d').setAttribute('class', 'red') }, 100)</script>")
    color = lambda: [c.color for c in tab.display_list.commands if isinstance(c, DrawText)]
    assert color() == ["#000000"] and tab.tick() is False
    clock.now = 0.2
    assert tab.tick() is True and color() == ["#ff0000"]  # nouveau style calculé sans recharger la page
    assert tab.tick() is False                            # rien de plus à refaire


def test_tick_without_dom_change_does_not_rerender():
    tab, clock, _ = load(STYLE + "<div>a</div><script>setTimeout(function () { console.log('tick') }, 10)</script>")
    display_list = tab.display_list
    clock.now = 1
    assert tab.tick() is False and tab.display_list is display_list
    assert ("log", "tick") in tab.console_messages


def test_dom_change_triggers_a_new_layout_and_a_taller_document():
    tab, _, _ = load(STYLE + "<div id=d>one</div>", width=12, height=24)
    boxes, old_list = tab.layout_root, tab.display_list
    assert tab.document_height == 12
    tab.evaluate("document.getElementById('d').innerHTML = 'aa bb cc dd ee ff'")
    assert tab.document_height == 72 and shown(tab) == ["aa", "bb", "cc", "dd", "ee", "ff"]
    assert tab.layout_root is not boxes and tab.display_list is not old_list  # DOM changé : nouvelles boîtes (≠ simple reflow)


def test_scroll_position_is_kept_and_clamped_after_a_dom_change():
    tab, _, _ = load(STYLE + "<div id=d>aa bb cc dd ee ff</div>", width=12, height=24)
    tab.scroll_to(48)
    tab.evaluate("document.getElementById('d').innerHTML = 'aa bb cc dd ee ff gg hh'")  # plus haut : scroll gardé
    assert tab.scroll_y == 48 and tab.max_scroll == 72
    tab.evaluate("document.getElementById('d').textContent = 'x'")                      # plus court : recadré
    assert tab.scroll_y == 0


def test_evaluate_returns_the_value_and_rerenders():
    tab, _, _ = load(STYLE + "<div id=d>old</div><div>other</div>")
    assert tab.evaluate("document.querySelectorAll('div').length") == 2
    assert tab.evaluate("document.getElementById('d').textContent = 'new'") == "new"
    assert shown(tab) == ["new", "other"]


def test_links_created_by_javascript_are_clickable():
    pages = {"http://h/": STYLE + "<div id=d></div><script>document.getElementById('d').innerHTML = "
                                  "\"<a href='/b'>go</a>\"</script>",
             "http://h/b": STYLE + "<div>page b</div>"}
    tab, _, _ = make_tab(pages)
    tab.load("http://h/")
    assert tab.click(3, 3) == "http://h/b" and shown(tab) == ["page", "b"]


def test_style_element_inserted_by_javascript_applies():
    tab, _, _ = load(STYLE + "<div id=d></div><script>document.getElementById('d').innerHTML = "
                     "'<style>p { color: red }</style><p>x</p>'</script>")
    assert [c.color for c in tab.display_list.commands if isinstance(c, DrawText)] == ["#ff0000"]


def test_new_page_new_context_nothing_survives_navigation():
    pages = {"http://h/1": STYLE + "<script>var leak = 42; setTimeout(function () { console.log('late') }, 100)</script>",
             "http://h/2": STYLE + "<script>console.log(typeof leak)</script>"}
    tab, clock, _ = make_tab(pages)
    tab.load("http://h/1")
    first = tab._runtime
    tab.load("http://h/2")
    assert tab._runtime is not first and tab.console_messages == [("log", "undefined")]  # `leak` n'existe plus
    clock.now = 1
    assert tab.tick() is False and ("log", "late") not in tab.console_messages  # le timer de la page 1 est mort


def test_back_runs_the_page_scripts_again_from_scratch():
    pages = {"http://h/1": STYLE + "<div id=x>a</div><script>document.getElementById('x').textContent += '!'</script>",
             "http://h/2": STYLE + "<div>two</div>"}
    tab, _, _ = make_tab(pages)
    tab.load("http://h/1")
    tab.load("http://h/2")
    tab.back()
    assert shown(tab) == ["a!"]  # et pas « a!! » : page rechargée, DOM neuf


def test_drain_console_returns_and_clears():
    tab, _, _ = load(STYLE + "<script>console.log('one'); console.error('two')</script>")
    assert tab.drain_console() == [("log", "one"), ("error", "two")] and tab.drain_console() == []


def test_start_page_javascript_demo_runs_and_updates_with_a_timer():
    tab, clock, _ = make_tab({}, width=800, height=600)
    tab.load(START_URL)
    text = lambda: " ".join(shown(tab))
    assert "ce texte a été écrit par un script" in text() and "désactivé" not in text()
    clock.now = 1.5
    assert tab.tick() is True and "a changé 1 seconde plus tard" in text()


def test_end_to_end_over_real_http_with_an_external_script(serve):
    def handler(request):
        path = request_path(request)
        if path == "/app.js":
            return make_response(headers={"Content-Type": "application/javascript"},
                                 body="document.getElementById('x').textContent = 'loaded over http'")
        return make_response(headers={"Content-Type": "text/html"}, body=STYLE + "<div id=x>old</div><script src='/app.js'></script>")

    server = serve(handler)
    tab = Tab(200, 50)  # vrai fetch
    tab.load(server.url("/"))
    assert shown(tab) == ["loaded", "over", "http"]
