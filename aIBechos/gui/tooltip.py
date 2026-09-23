"""
Tooltip genérico para cualquier widget de customtkinter/tkinter -- aparece
tras una pequeña pausa al pasar el ratón por encima, se cierra al salir o al
hacer clic. customtkinter no trae ningún tooltip propio, y este es el primer
sitio del proyecto que necesita uno (ver core/trending.py::
explain_trending_score, usado en "Episodios que faltan"/"Liberar espacio").
"""

import tkinter as tk


class Tooltip:
    _DELAY_MS = 500

    def __init__(self, widget, text_fn):
        """text_fn: callable sin argumentos que devuelve el texto a
        mostrar en el momento de pasar el ratón (no una cadena fija) --
        así el tooltip siempre refleja el dato real de esa fila/celda en
        ese instante, aunque la fila se haya reconstruido desde que se
        creó el tooltip. Si text_fn() devuelve una cadena vacía/None, no
        se muestra nada (p.ej. una celda sin dato todavía)."""
        self.widget = widget
        self.text_fn = text_fn
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button-1>", self._hide, add="+")

    def _schedule(self, event=None):
        self._cancel_pending()
        self._after_id = self.widget.after(self._DELAY_MS, self._show)

    def _cancel_pending(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        self._after_id = None
        if self._tip is not None:
            return
        text = self.text_fn()
        if not text:
            return
        try:
            win = self.widget.winfo_toplevel()
        except Exception:
            win = self.widget
        # Ancho máximo para que no se salga: limitado por ventana y por pantalla
        try:
            win_w = win.winfo_width() or win.winfo_screenwidth()
            screen_w = self.widget.winfo_screenwidth()
            max_w = min(420, max(200, win_w - 40), max(200, screen_w - 40))
        except Exception:
            max_w = 420
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(tip, text=text, justify="left", background="#2b2b2b", foreground="#f0f0f0",
                  relief="solid", borderwidth=1, padx=8, pady=5, wraplength=max_w,
                  font=("Segoe UI", 10)).pack()
        # Medir y clampear dentro de ventana y de pantalla
        try:
            tip.update_idletasks()
            tw = tip.winfo_reqwidth()
            th = tip.winfo_reqheight()
            # ventana principal
            try:
                wx = win.winfo_rootx(); wy = win.winfo_rooty()
                ww = win.winfo_width(); wh = win.winfo_height()
            except Exception:
                wx = wy = 0; ww = screen_w; wh = self.widget.winfo_screenheight()
            sw = self.widget.winfo_screenwidth(); sh = self.widget.winfo_screenheight()
            # horizontal: a la derecha del widget; si no cabe, probar a la izquierda del widget antes de ir al borde derecho
            if x + tw > wx + ww - 4:
                alt_x = self.widget.winfo_rootx() - tw - 4
                if alt_x >= wx + 4 and alt_x + tw <= sw - 4:
                    x = alt_x
                else:
                    x = wx + ww - tw - 4
            if x + tw > sw - 4:
                # si aún no cabe en pantalla, probar a la izquierda también respecto a pantalla
                alt_x = self.widget.winfo_rootx() - tw - 4
                if alt_x >= 4:
                    x = alt_x
                else:
                    x = sw - tw - 4
            if x < wx + 4:
                # si izquierda del widget no cabía, al menos que no se salga de la ventana
                # pero sin mandarlo al extremo derecho: centrar sobre el widget si hace falta
                if x + tw > wx + ww - 4:
                    x = max(wx + 4, wx + (ww - tw) // 2)
                else:
                    x = wx + 4
            if x < 4:
                x = 4
            # vertical: si no cabe debajo, poner encima del widget
            if y + th > wy + wh - 4:
                y = self.widget.winfo_rooty() - th - 4
            if y + th > sh - 4:
                y = sh - th - 4
            if y < wy + 4:
                y = wy + 4
            if y < 4:
                y = 4
            tip.wm_geometry(f"+{x}+{y}")
        except Exception:
            tip.wm_geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self, event=None):
        self._cancel_pending()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def attach_tooltip(widget, text_fn) -> Tooltip:
    """Punto de entrada simple -- ver Tooltip. Devuelve la instancia por si
    el llamador necesita guardarla (no hace falta para que funcione, los
    binds ya quedan puestos en *widget*, pero evita que un futuro GC la
    recoja antes de tiempo en algún caso límite)."""
    return Tooltip(widget, text_fn)
