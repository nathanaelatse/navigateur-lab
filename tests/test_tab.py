"""Tests de l'étape 5b : l'onglet (chargement, historique, scroll, redimensionnement, clics).

Le réseau est remplacé par un faux `fetcher` : on teste la logique du navigateur, pas HTTP
(déjà couvert à l'étape 1).

Repères : `* { font-size: 10px; margin: 0 }`, 6px par caractère, lignes de 12px.
"""

import pytest

from browser.network import NetworkError, Response
from browser.paint import DrawText
from browser.tab import START_URL, Tab, error_page, normalize_url
from conftest import make_response, request_path

pytestmark = pytest.mark.usefixtures("constant_specificity")  # voir tests/conftest.py

STYLE = "<style>* { font-size: 10px; margin: 0 }</style>"
PAGE_A = STYLE + "<title>  Page   A </title><div>hello <a href='/b'>go</a></div>"
PAGE_B = STYLE + "<div>page b</div>"
SIX_WORDS = STYLE + "<div>aa bb cc dd ee ff</div>"


def make_fetcher(pages):
    """pages : {url: html | Response}. Un URL absent lève NetworkError, comme un serveur injoignable."""
    calls = []

    def fetcher(url):
        calls.append(url)
        if url not in pages:
            raise NetworkError(f"connexion refusée : {url}")
        page = pages[url]
        if isinstance(page, Response):
            return page
        return Response(url, 200, "OK", {"content-type": "text/html"}, page.encode())

    fetcher.calls = calls
    return fetcher


def make_tab(pages, width=100, height=24, **kwargs):
    fetcher = make_fetcher(pages)
    return Tab(width, height, fetcher=fetcher, **kwargs), fetcher


def shown(tab):
    return [c.text for c in tab.display_list.commands if isinstance(c, DrawText)]


# ---- adresses ---------------------------------------------------------------------------------------

def test_normalize_url():
    assert normalize_url("example.org") == "https://example.org"
    assert normalize_url("  example.org/a?b=1 ") == "https://example.org/a?b=1"
    assert normalize_url("http://example.org") == "http://example.org"
    assert normalize_url("HTTPS://x") == "HTTPS://x"
    assert normalize_url("localhost:8000/x") == "http://localhost:8000/x"  # en local : pas de TLS
    assert normalize_url("127.0.0.1:5000") == "http://127.0.0.1:5000"
    assert normalize_url(START_URL) == START_URL


def test_navigate_normalizes_what_the_user_typed():
    tab, fetcher = make_tab({"https://example.org": PAGE_B})
    tab.navigate("example.org")
    assert fetcher.calls == ["https://example.org"] and shown(tab) == ["page", "b"]


# ---- chargement ----------------------------------------------------------------------------------------

def test_load_runs_the_whole_pipeline():
    tab, fetcher = make_tab({"http://h/a": PAGE_A})
    tab.load("http://h/a")
    assert tab.url == "http://h/a" and shown(tab) == ["hello", "go"]
    assert tab.title == "Page A"  # espaces collapsés
    assert tab.display_list.links[0].href == "/b"
    assert tab.history == ["http://h/a"] and not tab.can_go_back()


def test_title_defaults_to_empty():
    tab, _ = make_tab({"http://h/b": PAGE_B})
    tab.load("http://h/b")
    assert tab.title == ""


def test_final_url_after_redirect_is_the_base_for_relative_links():
    page = Response("http://h/dir/final", 200, "OK", {"content-type": "text/html"},
                    (STYLE + "<div><a href='next.html'>go</a></div>").encode())
    tab, fetcher = make_tab({"http://h/start": page, "http://h/dir/next.html": PAGE_B})
    tab.load("http://h/start")
    assert tab.url == "http://h/dir/final"
    assert tab.click(3, 3) == "http://h/dir/next.html"


def test_charset_from_content_type_is_honoured():
    page = Response("http://h/", 200, "OK", {"content-type": "text/html; charset=iso-8859-1"},
                    (STYLE + "<div>café</div>").encode("latin-1"))
    tab, _ = make_tab({"http://h/": page})
    tab.load("http://h/")
    assert shown(tab) == ["café"]


def test_error_status_pages_are_still_rendered():
    page = Response("http://h/x", 404, "Not Found", {}, (STYLE + "<div>nope</div>").encode())
    tab, _ = make_tab({"http://h/x": page})
    tab.load("http://h/x")
    assert shown(tab) == ["nope"]


def test_network_error_becomes_an_error_page_not_an_exception():
    tab, _ = make_tab({})
    tab.load("http://h/missing")
    assert tab.title == "Erreur" and tab.url == "http://h/missing"
    assert "connexion" in " ".join(shown(tab))


def test_error_page_escapes_the_message():
    assert "<script>" not in error_page("http://h/", "<script>alert(1)</script>")
    assert "&lt;script&gt;" in error_page("http://h/", "<script>")


def test_start_page_needs_no_network():
    tab, fetcher = make_tab({})
    tab.load(START_URL)
    assert fetcher.calls == [] and tab.title == "Page de démarrage"
    assert any(l.href == "http://example.org/" for l in tab.display_list.links)


# ---- liens et clics ---------------------------------------------------------------------------------------

def test_click_on_a_link_loads_the_resolved_url():
    tab, fetcher = make_tab({"http://h/a": PAGE_A, "http://h/b": PAGE_B})
    tab.load("http://h/a")
    # « hello » = 30px, une espace = 6px : le lien « go » occupe x = 36..48
    assert tab.link_at(40, 5) == "http://h/b"
    assert tab.click(40, 5) == "http://h/b"
    assert tab.url == "http://h/b" and shown(tab) == ["page", "b"]
    assert tab.history == ["http://h/a", "http://h/b"]


def test_click_elsewhere_does_nothing():
    tab, fetcher = make_tab({"http://h/a": PAGE_A})
    tab.load("http://h/a")
    assert tab.click(5, 5) is None and tab.link_at(5, 5) is None
    assert tab.click(500, 500) is None
    assert fetcher.calls == ["http://h/a"] and tab.url == "http://h/a"


@pytest.mark.parametrize("href", ["#top", "mailto:a@b.c", "javascript:void(0)", "ftp://h/x", "  #x"])
def test_links_without_a_web_destination_are_ignored(href):
    tab, fetcher = make_tab({"http://h/": STYLE + f"<div><a href='{href}'>go</a></div>"})
    tab.load("http://h/")
    assert tab.link_at(3, 3) is None and tab.click(3, 3) is None
    assert fetcher.calls == ["http://h/"]


@pytest.mark.parametrize("href,expected", [("b.html", "http://h/d/b.html"), ("/r", "http://h/r"),
                                           ("../up", "http://h/up"), ("//other/x", "http://other/x"),
                                           ("https://z/", "https://z/"), ("?q=1", "http://h/d/page?q=1")])
def test_relative_links_are_resolved_against_the_page_url(href, expected):
    tab, _ = make_tab({"http://h/d/page": STYLE + f"<div><a href='{href}'>go</a></div>"})
    tab.load("http://h/d/page")
    assert tab.link_at(3, 3) == expected


def test_clicks_are_in_window_coordinates_so_scroll_shifts_the_target():
    # 6 mots d'une ligne chacun (72px) puis une ligne avec un lien (72..84) : le document fait 84px,
    # la fenêtre 24px, donc on peut scroller jusqu'à 60.
    html = STYLE + "<div>aa bb cc dd ee ff</div><div><a href='/b'>go</a></div>"
    tab, fetcher = make_tab({"http://h/": html, "http://h/b": PAGE_B}, width=12, height=24)
    tab.load("http://h/")
    assert tab.max_scroll == 60
    assert tab.click(5, 15) is None                      # scroll 0 : le point (5, 15) du document est « bb »
    tab.scroll_to(60)
    assert tab.link_at(5, 15) == "http://h/b"            # même point de la fenêtre = point 75 du document
    assert tab.click(5, 15) == "http://h/b" and tab.scroll_y == 0  # nouvelle page : retour en haut


# ---- historique -------------------------------------------------------------------------------------------------

def test_back_returns_to_the_previous_page():
    tab, fetcher = make_tab({"http://h/a": PAGE_A, "http://h/b": PAGE_B})
    tab.load("http://h/a")
    tab.click(40, 5)
    assert tab.can_go_back()
    tab.back()
    assert tab.url == "http://h/a" and shown(tab) == ["hello", "go"] and not tab.can_go_back()
    assert tab.history == ["http://h/a", "http://h/b"]  # « précédent » ne supprime rien


def test_back_with_no_history_does_nothing():
    tab, fetcher = make_tab({"http://h/a": PAGE_A})
    tab.back()
    tab.load("http://h/a")
    tab.back()
    assert tab.url == "http://h/a" and fetcher.calls == ["http://h/a"]


def test_navigating_after_back_drops_the_forward_pages():
    tab, _ = make_tab({"http://h/a": PAGE_A, "http://h/b": PAGE_B, "http://h/c": PAGE_B})
    tab.load("http://h/a")
    tab.click(40, 5)
    tab.back()
    tab.load("http://h/c")
    assert tab.history == ["http://h/a", "http://h/c"] and tab.history_index == 1


def test_back_does_not_grow_the_history():
    tab, _ = make_tab({"http://h/a": PAGE_A, "http://h/b": PAGE_B})
    tab.load("http://h/a")
    tab.click(40, 5)
    tab.back()
    tab.back()
    assert len(tab.history) == 2


# ---- scroll ---------------------------------------------------------------------------------------------------------

def test_scroll_is_clamped_between_top_and_bottom():
    tab, _ = make_tab({"http://h/": SIX_WORDS}, width=12, height=24)  # document de 72px
    tab.load("http://h/")
    assert (tab.document_height, tab.max_scroll) == (72, 48)
    tab.scroll_by(30)
    assert tab.scroll_y == 30
    tab.scroll_by(100)
    assert tab.scroll_y == 48
    tab.scroll_by(-100)
    assert tab.scroll_y == 0
    tab.scroll_to(1000)
    assert tab.scroll_y == 48
    tab.scroll_to(-5)
    assert tab.scroll_y == 0


def test_short_page_cannot_scroll():
    tab, _ = make_tab({"http://h/": PAGE_B}, width=100, height=300)
    tab.load("http://h/")
    tab.scroll_by(500)
    assert tab.max_scroll == 0 and tab.scroll_y == 0 and tab.scrollbar_range() == (0.0, 1.0)


def test_scrollbar_range():
    tab, _ = make_tab({"http://h/": SIX_WORDS}, width=12, height=24)
    tab.load("http://h/")
    assert tab.scrollbar_range() == (0, 24 / 72)
    tab.scroll_to(48)
    assert tab.scrollbar_range() == (48 / 72, 1.0)


def test_visible_commands_follow_the_scroll_position():
    tab, _ = make_tab({"http://h/": SIX_WORDS}, width=12, height=24)
    tab.load("http://h/")
    names = lambda: [c.text for c in tab.visible_commands() if isinstance(c, DrawText)]
    assert names() == ["aa", "bb"]
    tab.scroll_to(24)
    assert names() == ["cc", "dd"]
    tab.scroll_to(48)
    assert names() == ["ee", "ff"]


def test_loading_a_page_resets_the_scroll():
    tab, _ = make_tab({"http://h/": SIX_WORDS, "http://h/b": PAGE_B}, width=12, height=24)
    tab.load("http://h/")
    tab.scroll_to(40)
    tab.load("http://h/b")
    assert tab.scroll_y == 0


def test_no_page_loaded_yet_is_harmless():
    tab = Tab(100, 50)
    assert tab.visible_commands() == [] and tab.link_at(1, 1) is None and tab.max_scroll == 0
    tab.scroll_by(10)
    tab.resize(200, 100)
    assert tab.scroll_y == 0


# ---- redimensionnement (reflow) -------------------------------------------------------------------------------------------

def test_new_width_reflows_without_refetching_or_reparsing():
    tab, fetcher = make_tab({"http://h/": SIX_WORDS}, width=100, height=24)
    tab.load("http://h/")
    dom, boxes, old_list = tab.dom, tab.layout_root, tab.display_list
    assert tab.document_height == 24  # 102px de texte dans 100px : 2 lignes
    tab.resize(200, 24)
    assert tab.document_height == 12  # tout tient sur une ligne
    assert tab.dom is dom and tab.layout_root is boxes     # même DOM, mêmes boîtes...
    assert tab.display_list is not old_list                # ...mais une nouvelle display list
    assert fetcher.calls == ["http://h/"]                  # et aucun nouvel appel réseau
    tab.resize(12, 24)
    assert tab.document_height == 72


def test_height_only_change_does_not_touch_layout():
    tab, _ = make_tab({"http://h/": SIX_WORDS}, width=12, height=24)
    tab.load("http://h/")
    display_list = tab.display_list
    tab.resize(12, 48)
    assert tab.display_list is display_list and tab.max_scroll == 24


def test_scroll_is_clamped_when_the_window_grows():
    tab, _ = make_tab({"http://h/": SIX_WORDS}, width=12, height=24)
    tab.load("http://h/")
    tab.scroll_to(48)
    tab.resize(12, 60)   # fenêtre plus haute : on ne peut plus scroller aussi bas
    assert tab.scroll_y == 12
    tab.resize(200, 60)  # fenêtre plus large : le document passe à 12px, tout est visible
    assert tab.scroll_y == 0


def test_link_regions_follow_the_reflow():
    html = STYLE + "<div>aaaa bbbb <a href='/b'>go</a></div>"
    tab, _ = make_tab({"http://h/": html}, width=100, height=50)
    tab.load("http://h/")
    assert (tab.display_list.links[0].x, tab.display_list.links[0].y) == (60, 0)
    tab.resize(30, 50)
    assert (tab.display_list.links[0].x, tab.display_list.links[0].y) == (0, 24)
    assert tab.link_at(1, 25) == "http://h/b" and tab.link_at(61, 1) is None


def test_measure_function_is_pluggable():
    tab, _ = make_tab({"http://h/": STYLE + "<div>ab cd</div>"}, measure=lambda text, style: 10 * len(text))
    tab.load("http://h/")
    assert [(c.x, c.width) for c in tab.display_list.commands] == [(0, 20), (30, 20)]


# ---- de bout en bout avec la vraie couche réseau (étape 1) --------------------------------------------------------

def test_end_to_end_with_the_real_network_layer_and_a_redirect(serve):
    def handler(request):
        path = request_path(request)
        if path == "/old":
            return make_response(301, "Moved Permanently", {"Location": "/dir/page"})
        pages = {"/dir/page": STYLE + "<title>Réel</title><div><a href='next'>go</a></div>",
                 "/dir/next": PAGE_B}
        if path not in pages:
            return make_response(404, "Not Found", body="<div>absent</div>")
        return make_response(headers={"Content-Type": "text/html; charset=utf-8"}, body=pages[path])

    server = serve(handler)
    tab = Tab(100, 50)  # fetcher par défaut : le vrai `fetch`
    tab.load(server.url("/old"))
    assert tab.url == server.url("/dir/page") and tab.title == "Réel"   # redirection suivie
    assert tab.click(3, 3) == server.url("/dir/next")                   # lien relatif résolu sur l'URL finale
    assert shown(tab) == ["page", "b"]
    tab.back()
    assert tab.title == "Réel"
