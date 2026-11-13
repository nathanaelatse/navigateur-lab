"""Étape 5c : la fenêtre tkinter (barre d'URL, Canvas, scroll, liens cliquables).

    python -m browser.gui                      # page de démarrage
    python -m browser.gui http://example.org/

Ce fichier ne fait que de la COLLE : il transmet les événements Tk à un `Tab` (browser/tab.py)
puis redessine. Rien ici ne sait ce qu'est une marge ou un lien :
    événement ─► Tab (change d'état) ─► redraw() ─► paint.execute(commandes visibles, TkSurface)

Seules `TkSurface` (traduire une commande en appel Canvas), `tk_family` et `wheel_pixels` ne
touchent pas à un widget : elles se testent avec de faux objets. `tkinter` est importé
sans erreur même s'il manque ; `main()` explique alors comment l'installer.
"""

import sys

try:
    import tkinter as tk
    from tkinter import font as tkfont
except ImportError:  # Python sans Tk (ex. Homebrew : `brew install python-tk@3.14`)
    tk = tkfont = None

from browser.layout import ASCENT
from browser.paint import execute, font_spec
from browser.tab import START_URL, Tab

SCROLL_STEP = 48          # pixels par cran de molette ou par flèche
FAMILIES = {"serif": "Times", "sans-serif": "Helvetica", "monospace": "Courier",
            "cursive": "Apple Chancery" if sys.platform == "darwin" else "Comic Sans MS", "fantasy": "Impact"}
RESIZE_DELAY_MS = 40      # on attend la fin du glisser de la bordure avant de refaire le layout
JS_POLL_MS = 50           # fréquence de vérification des setTimeout de la page


def tk_family(css_family):
    """Famille CSS -> famille Tk (les noms génériques n'existent pas pour Tk)."""
    return FAMILIES.get(css_family.lower(), css_family)


def wheel_pixels(num, delta, platform=sys.platform):
    """Événement molette -> décalage de scroll en pixels (positif = vers le bas).

    Chaque système envoie un signal différent : Linux = boutons 4/5, Windows = multiples de 120,
    macOS = petits entiers (un par « cran » du trackpad).
    """
    if num == 4:
        return -SCROLL_STEP
    if num == 5:
        return SCROLL_STEP
    if platform == "darwin":
        return -delta * 4
    return -delta / 120 * SCROLL_STEP


class TkSurface:
    """Exécute les commandes de la display list sur un Canvas Tk.

    `make_font(FontSpec)` fabrique une police Tk ; le résultat est mis en cache car créer une
    police coûte cher et que le layout en demande une pour CHAQUE mesure.
    """

    def __init__(self, canvas, make_font):
        self.canvas = canvas
        self._make_font = make_font
        self._fonts = {}

    def font(self, spec):
        if spec not in self._fonts:
            self._fonts[spec] = self._make_font(spec)
        return self._fonts[spec]

    def draw_rect(self, x, y, width, height, color):
        self.canvas.create_rectangle(x, y, x + width, y + height, fill=color, outline="", width=0)

    def draw_text(self, x, y, text, spec, color):
        font = self.font(spec)
        # Le layout pose la ligne de base à `y + 0,8 x taille` ; Tk, lui, place la ligne de base à
        # `ascent` pixels sous le haut de sa boîte. On décale pour que les deux s'accordent.
        y -= font.metrics("ascent") - ASCENT * spec.size
        self.canvas.create_text(x, y, text=text, anchor="nw", font=font, fill=color)

    def measure(self, text, style):
        """Fonction `measure` du layout : la largeur RÉELLE de `text` avec la police du style."""
        return self.font(font_spec(style)).measure(text)


def real_font_factory(spec):
    return tkfont.Font(family=tk_family(spec.family), size=-max(1, round(spec.size)),  # taille négative = pixels
                       weight="bold" if spec.bold else "normal", slant="italic" if spec.italic else "roman")


class BrowserWindow:
    def __init__(self, root, start_url):
        self.root = root
        root.geometry("900x700")
        self.tab = Tab()

        bar = tk.Frame(root)
        bar.pack(side="top", fill="x")
        self.back_button = tk.Button(bar, text="←", width=3, command=self.go_back)
        self.back_button.pack(side="left", padx=(4, 2), pady=4)
        self.url_var = tk.StringVar()
        self.entry = tk.Entry(bar, textvariable=self.url_var)
        self.entry.pack(side="left", fill="x", expand=True, padx=2, pady=4)
        self.entry.bind("<Return>", self.on_url_submitted)
        tk.Button(bar, text="Aller", command=self.on_url_submitted).pack(side="left", padx=(2, 4), pady=4)

        self.status = tk.StringVar()
        tk.Label(root, textvariable=self.status, anchor="w", relief="sunken").pack(side="bottom", fill="x")
        self.scrollbar = tk.Scrollbar(root, orient="vertical", command=self.on_scrollbar)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas = tk.Canvas(root, background="white", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        self.surface = TkSurface(self.canvas, real_font_factory)
        self.tab.measure = self.surface.measure  # le layout mesure avec les polices qu'on dessinera
        self._resize_job = None

        self.canvas.bind("<Configure>", self.on_configure)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Motion>", self.on_motion)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(sequence, self.on_wheel)
        for sequence, action in (("<Down>", lambda: self.scroll_by(SCROLL_STEP)),
                                 ("<Up>", lambda: self.scroll_by(-SCROLL_STEP)),
                                 ("<Next>", lambda: self.scroll_by(self.tab.viewport_height * 0.9)),
                                 ("<space>", lambda: self.scroll_by(self.tab.viewport_height * 0.9)),
                                 ("<Prior>", lambda: self.scroll_by(-self.tab.viewport_height * 0.9)),
                                 ("<Home>", lambda: self.scroll_to(0)),
                                 ("<End>", lambda: self.scroll_to(self.tab.max_scroll))):
            self.canvas.bind(sequence, lambda event, action=action: action())
        for modifier in ("Control", "Command"):
            try:
                root.bind(f"<{modifier}-l>", self.focus_url_bar)
            except tk.TclError:
                pass  # « Command » n'existe pas hors macOS
        root.bind("<Alt-Left>", lambda event: self.go_back())

        self.canvas.focus_set()
        root.update_idletasks()
        self.tab.resize(self.canvas.winfo_width(), self.canvas.winfo_height())
        self.open(start_url)
        root.after(JS_POLL_MS, self.poll_js)

    # -- actions ---------------------------------------------------------------------------------

    def open(self, text):
        self.tab.navigate(text)
        self.after_navigation()

    def go_back(self):
        self.tab.back()
        self.after_navigation()

    def after_navigation(self):
        self.url_var.set("" if self.tab.url == START_URL else self.tab.url)
        self.back_button.config(state="normal" if self.tab.can_go_back() else "disabled")
        self.canvas.focus_set()
        self.redraw()

    def poll_js(self):
        """Les setTimeout de la page n'ont pas de « réveil » : on vient voir régulièrement s'ils sont échus."""
        if self.tab.tick():  # le JS a modifié le DOM : styles + layout + display list refaits par Tab
            self.redraw()
        for level, message in self.tab.drain_console():
            print(f"[JS {level}] {message}", file=sys.stderr)  # la « console développeur » : le terminal
        self.root.after(JS_POLL_MS, self.poll_js)

    def scroll_by(self, dy):
        self.tab.scroll_by(dy)
        self.redraw()

    def scroll_to(self, y):
        self.tab.scroll_to(y)
        self.redraw()

    def redraw(self):
        self.canvas.delete("all")
        execute(self.tab.visible_commands(), self.surface, self.tab.scroll_y)  # seul le visible part au Canvas
        self.scrollbar.set(*self.tab.scrollbar_range())
        self.root.title(f"{self.tab.title or self.tab.url} — MiniBrowser")

    # -- événements Tk ---------------------------------------------------------------------------

    def on_url_submitted(self, event=None):
        self.open(self.url_var.get())

    def focus_url_bar(self, event=None):
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        return "break"

    def on_configure(self, event):
        # Pendant qu'on tire la bordure, Tk envoie des dizaines d'événements : on ne refait le
        # layout (cher) qu'une fois le mouvement terminé.
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(RESIZE_DELAY_MS, self.apply_resize)

    def apply_resize(self):
        self._resize_job = None
        self.tab.resize(self.canvas.winfo_width(), self.canvas.winfo_height())
        self.redraw()

    def on_wheel(self, event):
        self.scroll_by(wheel_pixels(event.num, getattr(event, "delta", 0)))

    def on_scrollbar(self, action, *args):
        if action == "moveto":
            self.scroll_to(float(args[0]) * self.tab.document_height)
        else:  # "scroll", n, "units" | "pages"
            step = SCROLL_STEP if args[1] == "units" else self.tab.viewport_height * 0.9
            self.scroll_by(int(args[0]) * step)

    def on_click(self, event):
        self.canvas.focus_set()
        if self.tab.click(event.x, event.y) is not None:
            self.after_navigation()

    def on_motion(self, event):
        target = self.tab.link_at(event.x, event.y)
        self.canvas.config(cursor="hand2" if target else "")
        self.status.set(target or "")


NO_TK_MESSAGE = """tkinter n'est pas disponible dans ce Python.
  macOS (Homebrew) : brew install python-tk@3.14   (adapter au numéro de version de python3)
  Debian/Ubuntu    : sudo apt install python3-tk
  puis recréer le venv si besoin.
Sans fenêtre, on peut quand même voir ce qui serait dessiné :
  python -m browser.paint page.html --width 800"""


def main(argv=None):
    if tk is None:
        print(NO_TK_MESSAGE, file=sys.stderr)
        return 1
    argv = sys.argv[1:] if argv is None else argv
    root = tk.Tk()
    BrowserWindow(root, argv[0] if argv else START_URL)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
