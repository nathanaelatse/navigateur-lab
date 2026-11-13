"""Tests de ce qui, dans gui.py, ne dépend pas de Tk : traduction des commandes, polices, molette.

La fenêtre elle-même (BrowserWindow) a besoin d'un écran et de `_tkinter` : elle se vérifie à la main
avec `python -m browser.gui`. Tout ce qu'elle appelle (Tab, execute) est testé ailleurs.
"""

import pytest

from browser import gui
from browser.gui import SCROLL_STEP, TkSurface, tk_family, wheel_pixels
from browser.paint import DrawRect, DrawText, FontSpec, execute

pytestmark = pytest.mark.usefixtures("constant_specificity")  # voir tests/conftest.py


class FakeFont:
    def __init__(self, spec):
        self.spec = spec

    def metrics(self, name):
        assert name == "ascent"
        return 9

    def measure(self, text):
        return 7 * len(text)


class FakeCanvas:
    def __init__(self):
        self.calls = []

    def create_rectangle(self, *args, **kwargs):
        self.calls.append(("rect", args, kwargs))

    def create_text(self, *args, **kwargs):
        self.calls.append(("text", args, kwargs))


def make_surface():
    made = []

    def make_font(spec):
        made.append(spec)
        return FakeFont(spec)

    canvas = FakeCanvas()
    return TkSurface(canvas, make_font), canvas, made


# ---- polices ------------------------------------------------------------------------------------------

def test_tk_family_maps_generic_names_and_keeps_others():
    assert tk_family("serif") == "Times" and tk_family("Sans-Serif") == "Helvetica"
    assert tk_family("monospace") == "Courier"
    assert tk_family("Arial") == "Arial"


def test_fonts_are_created_once_per_spec():
    surface, _, made = make_surface()
    a = surface.font(FontSpec("serif", 10.0))
    assert surface.font(FontSpec("serif", 10.0)) is a
    surface.font(FontSpec("serif", 10.0, bold=True))
    assert len(made) == 2


def test_measure_uses_the_same_font_as_painting():
    surface, _, made = make_surface()
    style = {"font-family": "monospace", "font-size": "10px", "font-weight": "bold"}
    assert surface.measure("abc", style) == 21
    assert made == [FontSpec("monospace", 10.0, True, False)]
    surface.draw_text(0, 0, "abc", FontSpec("monospace", 10.0, True, False), "#000000")
    assert len(made) == 1  # la police de la mesure est réutilisée pour le dessin


# ---- commandes -> Canvas ------------------------------------------------------------------------------------

def test_draw_rect_calls_create_rectangle_with_corners():
    surface, canvas, _ = make_surface()
    surface.draw_rect(10, 20, 5, 8, "#ff0000")
    assert canvas.calls == [("rect", (10, 20, 15, 28), {"fill": "#ff0000", "outline": "", "width": 0})]


def test_draw_text_aligns_tk_baseline_with_the_layout_baseline():
    surface, canvas, _ = make_surface()
    surface.draw_text(10, 20, "hi", FontSpec("serif", 10.0), "#123456")
    # Tk : ascent 9 sous le haut de sa boîte ; layout : 0,8 x 10 = 8. On remonte de 9 - 8 = 1px.
    (kind, args, kwargs), = canvas.calls
    assert (kind, args) == ("text", (10, 19.0))
    assert kwargs["text"] == "hi" and kwargs["anchor"] == "nw" and kwargs["fill"] == "#123456"
    assert kwargs["font"].spec == FontSpec("serif", 10.0)


def test_execute_on_the_tk_surface_applies_scroll():
    surface, canvas, _ = make_surface()
    commands = [DrawRect(0, 100, 50, 10, "#eeeeee"), DrawText(0, 101, 12, 10, "hi", FontSpec("serif", 10.0), "#000000")]
    execute(commands, surface, scroll_y=100)
    assert canvas.calls[0][1] == (0, 0, 50, 10)
    assert canvas.calls[1][1] == (0, 1 - 1.0)  # y = 101 - 100, puis le décalage de ligne de base


# ---- molette ----------------------------------------------------------------------------------------------------------

def test_wheel_linux_buttons():
    assert wheel_pixels(4, 0) == -SCROLL_STEP and wheel_pixels(5, 0) == SCROLL_STEP


def test_wheel_windows_uses_multiples_of_120():
    assert wheel_pixels("??", 120, "win32") == -SCROLL_STEP
    assert wheel_pixels("??", -240, "win32") == 2 * SCROLL_STEP


def test_wheel_macos_uses_small_deltas():
    assert wheel_pixels("??", 2, "darwin") == -8 and wheel_pixels("??", -3, "darwin") == 12


# ---- sans Tk -------------------------------------------------------------------------------------------------------------

def test_main_explains_how_to_get_tk_when_it_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(gui, "tk", None)
    assert gui.main([]) == 1
    err = capsys.readouterr().err
    assert "tkinter" in err and "python-tk" in err and "browser.paint" in err


def test_module_imports_without_tk():
    # Si ce fichier a pu s'importer ici, c'est que `import tkinter` est protégé.
    assert hasattr(gui, "BrowserWindow") and hasattr(gui, "main")
