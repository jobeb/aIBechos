"""
TableView: cabecera de columnas + cuerpo con scroll, compartido por las
cuatro pestañas de lista (Archivos, Episodios que faltan, Liberar espacio,
Historial). Existe para arreglar de raíz un bug que se repitió varias
veces al mantener cada tabla por separado: la cabecera y las filas leían
anchos de columna de sitios distintos (o calculados con fórmulas
distintas), así que cualquier cambio en una fila -- un botón nuevo, un
icono nuevo -- descuadraba la cabecera sin que fuera obvio por qué.

Con TableView, cabecera y filas SIEMPRE leen el mismo ancho para la misma
columna (self.col_width(key)), y la cabecera nunca se puede salir de su
columna: si el texto de la cabecera no cabe en el ancho pedido, la
columna crece para acomodarlo (antes, el CTkLabel de la cabecera crecía
él solo más allá del ancho configurado y desalineaba todo lo de después).
"""

import tkinter as tk
import customtkinter as ctk


class ColumnSpec:
    """Una columna de TableView.

    key: identificador (el que se usa luego en table.col_width(key)).
    header: texto de la cabecera ("" para una columna sin etiqueta, p.ej.
        la de un botón de acción).
    width: ancho base en px (ignorado si expand=True).
    min_width: ancho mínimo al arrastrar un separador (ver resizable).
    resizable: si hay un separador arrastrable a la DERECHA de esta
        columna, que redistribuye ancho entre esta columna y la
        siguiente (igual que _sash_press/_sash_motion de Archivos).
    expand: esta columna ocupa el espacio sobrante (fill="x",
        expand=True) -- como máximo UNA columna de la tabla debería
        marcarse así.
    sortable: la cabecera responde a clic (ver TableView.on_header_click)
        para ordenar la tabla por esta columna -- el propio TableView no
        sabe ordenar nada (no conoce el modelo de las filas), solo avisa;
        quien construye la tabla decide la clave de orden real.
    """

    __slots__ = ("key", "header", "width", "min_width", "resizable", "expand", "anchor", "sortable", "hideable")

    def __init__(self, key, header="", width=80, min_width=40,
                 resizable=False, expand=False, anchor="w", sortable=False, hideable=True):
        self.key = key
        self.header = header
        self.width = width
        self.min_width = min_width
        self.resizable = resizable
        self.expand = expand
        self.anchor = anchor
        self.sortable = sortable
        self.hideable = hideable


class TableView(ctk.CTkFrame):
    """Cabecera fija (no scrollea) + CTkScrollableFrame para las filas.

    Uso:
        table = TableView(parent, columns=[
            ColumnSpec("nombre", "Nombre", width=200, resizable=True),
            ColumnSpec("detalle", "Detalle", expand=True),
        ])
        table.grid(...)
        # construir una fila:
        row = ctk.CTkFrame(table.body)
        row.pack(fill="x", pady=3, padx=2)
        ctk.CTkLabel(row, width=table.col_width("nombre"), ...).pack(side="left")
        ...

    Las columnas con resizable=True/expand=True se recalculan solas al
    arrastrar su separador; las demás mantienen su `width` tal cual
    (súbelo a mano con set_width() si hace falta adaptarlo, p.ej. al
    ancho de la ventana -- ver _on_table_resize en Archivos)."""

    _PADX_BETWEEN = 4   # separación horizontal entre columnas consecutivas
    _HEADER_TEXT_PADDING = 16   # margen que se añade al medir el texto de cabecera

    # Tamaño de página dinámico (ver enable_dynamic_page_size/note_rows_rendered
    # más abajo) -- en vez de un número fijo de filas por pestaña, se calcula
    # cuántas caben de verdad en el alto real del canvas, para no depender del
    # scroll interno en uso normal.
    MIN_PAGE_ROWS = 10       # suelo pedido por el usuario, aunque la ventana sea muy pequeña
    DEFAULT_PAGE_ROWS = 25   # valor de arranque, antes de medir ninguna fila real
    _RESIZE_DEBOUNCE_MS = 150   # mismo valor que _DETAIL_RESIZE_DEBOUNCE_MS en gui/app.py

    def __init__(self, master, columns, scrollable: bool = True, header_right_pad: int = 0,
                 on_visibility_changed=None, **kwargs):
        """scrollable=False: self.body es un CTkFrame normal (crece con
        cada fila añadida, sin barra de scroll propia) en vez de un
        CTkScrollableFrame -- para listas pequeñas/ya acotadas (p.ej. ya
        paginadas) que deben apoyarse en el scroll de la pantalla que las
        contiene, no tener una segunda barra de scroll anidada e inútil
        (ver "Sincronizar visionado"). Por defecto True: mismo
        comportamiento de siempre, el que necesitan Archivos/Episodios/
        Liberar espacio/Historial con listas sin acotar.

        header_right_pad: hueco extra (px) reservado a la derecha de la
        cabecera -- 0 por defecto, no cambia nada. Cada fila vive dentro
        de self.body (CTkScrollableFrame, que reserva su propia barra de
        scroll + borde interno en el lado derecho, la tenga visible ahora
        mismo o no), mientras que self.header_frame es un CTkFrame normal
        sin ese hueco -- así que la columna expand=True de la cabecera
        puede acabar creciendo más que la misma columna en cada fila,
        desplazando hacia la derecha, en la cabecera, las columnas de
        ancho fijo que van DESPUÉS del expand. El hueco real que hace
        falta compensar NO es un valor fijo universal -- depende de
        cuántas columnas/qué anidación tenga cada tabla en concreto (visto
        de verdad: Episodios que faltan necesitaba ~30px, Archivos ~16px)
        -- por eso esto es opt-in por tabla (medido a mano contra esa
        tabla, ver dónde se pasa en gui/app.py), no un valor aplicado

        OJO -- esto es un efecto DISTINTO e independiente de col_padx()
        (el hueco que cada columna resizable=True deja tras de sí en la
        CABECERA, ver _init_widths): header_right_pad compensa la
        diferencia de ancho real entre header_frame y body/su scrollbar;
        col_padx() compensa los separadores arrastrables. Los dos se
        miden por separado -- no asumas que ajustar uno arregla el otro
        (confusión real de esta sesión: al medir header_right_pad antes
        de que existiera col_padx(), el valor encontrado absorbía sin
        querer parte del déficit de sashes, así que tocó re-medirlo tras
        añadir col_padx()).
        solo por defecto a las cuatro."""
        # border_width en el propio TableView (aunque fg_color siga
        # "transparent"): sin esto, el "contenedor" de la tabla no tiene
        # ningún límite visible propio -- depende por completo de que el
        # color de fondo heredado del padre contraste con el de las filas,
        # lo cual varía según dónde se monte la tabla (CTkTabview resuelve
        # su propio fg_color de forma distinta según el color del SUYO,
        # ver ctk_tabview.py) y llevó a que la tabla de "Usuarios
        # emparejados" (Sincronizar visionado) se fundiera con el fondo
        # pese a que las filas ya tenían su color de la convención
        # establecida. Un borde explícito garantiza el límite sin
        # depender de esa cadena de colores heredados.
        super().__init__(master, fg_color="transparent",
                          border_width=1, border_color=("gray70", "gray30"), **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.columns = columns
        self.scrollable = scrollable
        self._header_right_pad = header_right_pad
        # Debe quedar fijado ANTES de _build_header() (llamado más abajo) --
        # el menú contextual de ocultar columnas solo se enlaza si este
        # callback está puesto, así que pasarlo por constructor es lo único
        # que garantiza que las etiquetas de la cabecera ya nacen con el
        # clic derecho. Si se pusiera después (p.ej. como atributo desde
        # quien construye la tabla), la primera _build_header() no añadiría
        # el binding, y como set_hidden() solo reconstruye la cabecera si
        # el conjunto oculto CAMBIA (no ocurre en el primer arranque sin
        # columnas ocultas guardadas), el menú no aparecería nunca.
        self.on_visibility_changed = on_visibility_changed
        self._widths = {}
        self._desired = {}   # ancho pedido/elegido, antes de encogerlo para que quepa -- ver fit_to_width
        self._padx = {}   # padx que debe usar CADA FILA para esta columna -- ver _init_widths/col_padx
        self._header_min = {}   # ancho mínimo para que quepa el texto de cabecera -- ver _init_widths
        self._hidden = set()   # claves de columnas ocultas -- ver set_hidden/visible_columns
        self._sash_state = None
        self._header_labels = {}
        # Celdas de fila ya construidas, por columna ({key: [frame, ...]}) --
        # ver cell()/_apply_cell_widths: al arrastrar un separador hay que
        # reajustar el ancho de las celdas YA pintadas, no solo el de la
        # cabecera, o cabecera y filas dejan de coincidir hasta el siguiente
        # redibujado completo. Se lleva aquí (y no en cada pestaña) para que
        # ninguna tabla tenga que reimplementarlo.
        self._cells = {}
        self._bold_font = ctk.CTkFont(weight="bold")

        self.page_size = self.DEFAULT_PAGE_ROWS
        self._row_stride_px = None   # alto medio medido por fila, ver note_rows_rendered
        self._active_row = None      # fila resaltada con el teclado, ver _move_active_row
        self._prev_row_bg = None     # su color de fondo original, para devolvérselo
        self._active_index = None    # su posición, por si la lista se repinta entera
        self._rows_clickable = None  # ¿las filas traen su propia selección? (ver _row_is_clickable)
        self.on_row_activated = None # hook opcional: (indice, frame) al moverse
        self._resize_after_id = None
        self.on_page_size_changed = None   # callback opcional, ver enable_dynamic_page_size
        self.on_header_click = None   # callback opcional: ordenar por columna, ver set_sort_indicator

        # fg_color explícito: sin esto la cabecera usa el gris por defecto
        # de CTkFrame (gray17 en modo oscuro), que es EXACTAMENTE el mismo
        # gris que las filas (ver fg_color=("gray95","gray17") en las
        # filas de cada pestaña) -- cabecera y filas se fundían en un solo
        # bloque sin ninguna línea de separación entre ambas.
        self.header_frame = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray80", "gray23"))
        self.header_frame.grid(row=0, column=0, sticky="ew")

        if scrollable:
            # fg_color explícito, mismo motivo que en la rama scrollable=False
            # de abajo: CTkScrollableFrame, con fg_color="transparent", delega
            # su color real de fondo a un CTkFrame interno (self._parent_frame)
            # que a su vez recorre la cadena de padres "transparent" hacia
            # arriba (customtkinter._detect_color_of_master) para adivinarlo.
            # Cuantos más niveles de frames "transparent" haya de por medio
            # (Archivos/Episodios/etc. anidan varios: pestaña → body → este
            # TableView → CTkScrollableFrame), más falla esa adivinanza --
            # se notó como un recuadro mal coloreado justo en las esquinas
            # redondeadas de la primera fila, pegado al triángulo de
            # expandir/colapsar de Episodios que faltan (que vive ahí
            # pegado al borde izquierdo). Un color fijo aquí corta esa
            # cadena de raíz, igual que ya se hizo abajo para scrollable=False.
            self.body = ctk.CTkScrollableFrame(self, fg_color=("gray92", "gray14"))
        else:
            # fg_color explícito (NO "transparent"): con scrollable=False,
            # self.body es un CTkFrame normal colgado de una cadena de
            # frames "transparent" (mapping_fr → fr → outer CTkScrollableFrame
            # → tab de CTkTabview) -- customtkinter recorre esa cadena para
            # calcular su color real de fondo, y con esa cadena tan larga
            # (y un CTkTabview de por medio, que decide su propio fg_color
            # de forma dinámica según el de SU padre) el cálculo salía
            # inconsistente entre pasadas de dibujado: las filas (encima
            # de este frame) se fundían con el fondo, y además el redondeado
            # de esquinas de la primera fila usaba un color de esquina
            # distinto arriba que abajo (el propio bg_color detectado
            # había cambiado entre un redibujado y otro). Un color propio y
            # fijo aquí corta esa cadena: cada fila detecta SIEMPRE el
            # mismo padre real, así que su redondeado es consistente y
            # contrasta de forma predecible con fg_color=("gray95","gray17")
            # de las filas.
            self.body = ctk.CTkFrame(self, fg_color=("gray92", "gray14"))
        self.body.grid(row=1, column=0, sticky="nsew", pady=(2, 0))

        self._init_widths()
        self._build_header()
        self._enable_keyboard_scroll()

    # -------------------------------------------------- scroll por teclado --

    # Tecla -> (cuánto, unidad) para canvas.yview_scroll. "units" son líneas
    # sueltas (flechas) y "pages" pantallas enteras (AvPág/RePág); Inicio/Fin
    # se tratan aparte porque son yview_moveto, no un desplazamiento relativo.
    #: alto de fila supuesto mientras no haya una medida real
    DEFAULT_ROW_PX = 28

    #: fondo de la fila activa -- el azul de acento del tema, que se ve
    #: igual de bien en modo claro y en modo oscuro
    _ACTIVE_ROW_COLOR = ("#3B8ED0", "#1F6AA5")

    _SCROLL_KEYS = {
        "Up":    (-3, "units"),
        "Down":  (3, "units"),
        "Prior": (-1, "pages"),
        "Next":  (1, "pages"),
    }

    def _enable_keyboard_scroll(self):
        """Flechas / AvPág / RePág / Inicio / Fin desplazan la tabla que
        tenga el ratón encima, igual que ya hace la rueda.

        Se decide por POSICIÓN DEL RATÓN y no por foco a propósito: las filas
        son frames y etiquetas, que en Tk no reciben foco de teclado, así que
        atarlo al foco significaría no funcionar nunca sin pedirle antes al
        usuario que haga clic en un sitio concreto. customtkinter resuelve la
        rueda del ratón exactamente igual (bind_all + comprobar qué widget hay
        debajo, ver CTkScrollableFrame._mouse_wheel_all), así que esto no
        introduce un patrón nuevo en el proyecto."""
        if not self.scrollable:
            return
        # OJO: bind_all va sobre self.body, NO sobre self. CTkBaseClass lo
        # prohíbe a propósito (lanza AttributeError), y TableView hereda de
        # CTkFrame; self.body es un CTkScrollableFrame, que hereda de
        # tkinter.Frame y por tanto conserva el bind_all normal -- es
        # exactamente el objeto sobre el que el propio customtkinter engancha
        # la rueda del ratón.
        for seq in list(self._SCROLL_KEYS) + ["Home", "End"]:
            self.body.bind_all(f"<{seq}>", self._on_scroll_key, add="+")

    def destroy(self):
        try:
            for seq in list(self._SCROLL_KEYS) + ["Home", "End"]:
                try:
                    self.body.unbind_all(f"<{seq}>")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            super().destroy()
        except Exception:
            pass

    def _scroll_canvas(self):
        """El canvas que de verdad hace scroll (el de self.body).

        OJO con el nombre: NO puede llamarse _canvas. CTkFrame ya define un
        atributo de instancia `_canvas` (el CTkCanvas con el que se dibuja su
        propio fondo y borde), y como TableView hereda de CTkFrame, un método
        con ese nombre queda tapado por él: `self._canvas()` reventaba con
        "TypeError: 'CTkCanvas' object is not callable" en cada pulsación."""
        return getattr(self.body, "_parent_canvas", None)

    @staticmethod
    def _is_inside(widget, container) -> bool:
        """¿*widget* cuelga de *container*? (sube por la cadena de masters)"""
        while widget is not None:
            if widget == container:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_scroll_key(self, event):
        canvas = self._scroll_canvas()
        if canvas is None:
            return
        # La tabla que debe reaccionar a flechas se elige por posición del
        # ratón (como la rueda), pero si el puntero no está sobre ninguna
        # tabla y YA hay una fila activa en esta, también se acepta: permite
        # seguir moviéndose con flechas tras hacer clic en una fila y mover el
        # ratón fuera, y cubre el caso donde el foco lo tiene un Entry de la
        # ficha de detalle. Sin este fallback las flechas solo funcionaban con
        # el ratón físicamente encima de la lista.
        try:
            under_mouse = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            under_mouse = None
        mouse_inside = under_mouse is not None and self._is_inside(under_mouse, canvas)
        has_active = self._active_row is not None and self._active_row.winfo_exists()
        if not mouse_inside and not has_active:
            # Primera pulsación sin fila activa y con ratón fuera: permitir
            # si la tabla está realmente visible. Así
            # Recomendado/Episodios/Descargar/Liberar/Protegidos responden a
            # flechas nada más entrar a la pestaña, sin exigir ratón encima.
            if not self._row_frames():
                return
            try:
                if not self._is_viewable():
                    return
                try:
                    if not canvas.winfo_viewable() and not canvas.winfo_ismapped():
                        return
                except Exception:
                    pass
            except Exception:
                return
        # Quien manda es la posición del ratón, NO el foco. Comprobar el foco
        # rompía la función entera: en la aplicación real casi siempre lo tiene
        # algún cuadro de texto (un buscador, la ficha de detalle...), así que
        # las flechas no hacían nunca nada. La rueda del ratón se comporta
        # igual, y aquí ya sabemos que el puntero está DENTRO de la tabla.
        # Única excepción: un campo de texto dentro de la propia tabla, donde
        # las flechas deben seguir moviendo el cursor de escritura.
        try:
            focused = self.focus_get()
        except Exception:
            focused = None
        if (isinstance(focused, (tk.Entry, tk.Text, ctk.CTkEntry, ctk.CTkTextbox))
                and self._is_inside(focused, canvas)):
            return

        rows = self._row_frames()
        if rows:
            self._move_active_row(canvas, rows, event.keysym)
            return "break"
        # Sin filas reconocibles (una lista que no se construya con un frame
        # por fila): al menos desplazar la vista, que es mejor que nada.
        if canvas.yview() == (0.0, 1.0):
            return
        if event.keysym == "Home":
            canvas.yview_moveto(0.0)
        elif event.keysym == "End":
            canvas.yview_moveto(1.0)
        elif event.keysym in ("Prior", "Next"):
            amount, what = self._SCROLL_KEYS[event.keysym]
            canvas.yview_scroll(amount, what)
        else:
            # OJO: NO usar yview_scroll(n, "units"). La unidad de un canvas de
            # Tk es 1 píxel salvo que se configure yscrollincrement, así que
            # una flecha movía la lista 1-2 px: se veía temblar la barra de
            # desplazamiento y nada más.
            total = max(1, self.body.winfo_reqheight())
            fila = self._row_stride_px or self.DEFAULT_ROW_PX
            paso = (1 if event.keysym == "Down" else -1) * fila / total
            canvas.yview_moveto(max(0.0, canvas.yview()[0] + paso))
        return "break"

    # --------------------------------------------------- fila activa ------
    # Recorrer la lista con las flechas resaltando la fila, como en el
    # Explorador de Windows. Se resuelve entero aquí para que funcione IGUAL
    # en las nueve listas de la aplicación sin tocar ninguna: las filas se
    # descubren solas mirando los hijos del cuerpo de la tabla, así que quien
    # las construye (gui/app.py) no tiene que registrar nada.

    def _is_viewable(self) -> bool:
        """¿Esta TableView está realmente visible? winfo_viewable falla en
        CTkTabview porque la pestaña oculta se hace con grid_remove y el
        TableView queda no-mapped pero winfo_viewable puede seguir dando True
        en algunos contenedores anidados. Se comprueba la cadena de padres y,
        si está dentro de un CTkTabview, que su pestaña sea la seleccionada."""
        try:
            cur = self
            tabview = None
            # Buscar CTkTabview ancestro
            while cur is not None:
                try:
                    if cur.__class__.__name__ == "CTkTabview":
                        tabview = cur
                        break
                except Exception:
                    pass
                cur = getattr(cur, "master", None)
                if cur is self:
                    break
            if tabview is not None:
                try:
                    selected = tabview.get() if hasattr(tabview, "get") else None
                    # El TableView está dentro de tabview.tab("Nombre")
                    # Si no podemos determinar, caer al check de ismapped
                    if selected:
                        # Buscar si este TableView cuelga de la pestaña seleccionada
                        # Recorre padres y ve si alguno es el frame de la pestaña seleccionada
                        try:
                            tab_frame = tabview.tab(selected) if hasattr(tabview, "tab") else None
                        except Exception:
                            tab_frame = None
                        if tab_frame is not None:
                            cur2 = self
                            while cur2 is not None and cur2 is not tabview:
                                if cur2 is tab_frame:
                                    # Está dentro de la pestaña seleccionada -> viewable si mapped
                                    return bool(self.winfo_ismapped())
                                cur2 = getattr(cur2, "master", None)
                            # Está dentro de tabview pero NO en la pestaña seleccionada -> oculto
                            return False
                except Exception:
                    pass
            cur = self
            while cur is not None:
                try:
                    if not cur.winfo_exists():
                        return False
                    if not cur.winfo_ismapped():
                        return False
                except Exception:
                    return False
                cur = getattr(cur, "master", None)
                if cur is self:
                    break
            return True
        except Exception:
            return False

    def _row_frames(self) -> list:
        """Los frames de fila, en el orden en que se ven.

        Solo CTkFrame: en el cuerpo de una tabla puede haber además etiquetas
        sueltas (el "no hay nada que mostrar", separadores...), y colarlas
        como filas dejaba un hueco por el que el resaltado desaparecía."""
        try:
            rows = [w for w in self.body.pack_slaves()
                    if isinstance(w, ctk.CTkFrame)]
            if rows:
                return rows
            # Fallback para CTkScrollableFrame cuyo pack_slaves no lista el
            # inner frame en algunas versiones / cuando hay viewable raro:
            # buscar recursivamente cualquier CTkFrame descendiente con filas
            found = []
            try:
                stack = list(self.body.winfo_children())
                while stack:
                    w = stack.pop(0)
                    if isinstance(w, ctk.CTkFrame):
                        # Heurística: un row tiene fg_color distinto de transparente
                        # y contiene celdas; evitar header_frame que está fuera de body
                        try:
                            if w.master is self.body or w.master is getattr(self.body, "_parent_frame", None):
                                found.append(w)
                        except Exception:
                            found.append(w)
                    try:
                        stack.extend(w.winfo_children())
                    except Exception:
                        pass
            except Exception:
                pass
            # Si encontramos algo, devolverlo; si no, lo original
            return found if found else rows
        except Exception:
            return []

    def _highlight_row(self, frame, on: bool):
        """Pinta (o despinta) la fila activa.

        Se cambia el COLOR DE FONDO, no el borde: un borde no se ve: las filas
        empaquetan sus celdas con fill="both" y las celdas lo tapan por
        completo (comprobado con una captura de pantalla, en el código parecía
        correcto). El color anterior se guarda para devolvérselo al salir."""
        try:
            if on:
                self._prev_row_bg = frame.cget("fg_color")
                frame.configure(fg_color=self._ACTIVE_ROW_COLOR)
            elif self._prev_row_bg is not None:
                frame.configure(fg_color=self._prev_row_bg)
        except Exception:
            pass        # una fila que ya no existe

    def _content_height(self, canvas) -> int:
        """Alto TOTAL de lo que hay dentro del canvas, desplazado o no.

        Se pregunta al propio canvas (bbox de su contenido) en vez de usar
        body.winfo_reqheight(): no siempre coinciden, y usar el del body
        dejaba la vista descuadrada -- con Inicio se quedaba a media lista en
        vez de subir del todo."""
        caja = canvas.bbox("all")
        if caja:
            return max(1, caja[3] - caja[1])
        return max(1, self.body.winfo_reqheight())

    def _rows_per_page(self, canvas) -> int:
        """Cuántas filas caben a la vista (lo que salta AvPág/RePág)."""
        fila = self._row_stride_px or self.DEFAULT_ROW_PX
        return max(1, canvas.winfo_height() // max(1, fila))

    def _ensure_row_visible(self, canvas, frame):
        """Arrastra la vista lo justo para que la fila activa se vea entera.

        Se mide con coordenadas de PANTALLA (winfo_rooty) porque son las
        únicas que valen igual esté el contenido desplazado o no."""
        total = self._content_height(canvas)
        alto_visible = canvas.winfo_height()
        desde_arriba = frame.winfo_rooty() - canvas.winfo_rooty()
        desde_abajo = desde_arriba + frame.winfo_height() - alto_visible
        actual = canvas.yview()[0]
        if desde_arriba < 0:
            canvas.yview_moveto(max(0.0, actual + desde_arriba / total))
        elif desde_abajo > 0:
            canvas.yview_moveto(max(0.0, actual + desde_abajo / total))

    def _row_is_clickable(self, frame) -> bool:
        """¿La fila responde al clic con su propia selección?

        Antes se cacheaba el resultado del primer frame inspeccionado para toda
        la tabla (self._rows_clickable). Eso dejaba flechas en solo-azul si el
        primer frame consultado era un placeholder sin binding o una fila aún
        sin construir: todas las filas siguientes quedaban marcadas como no
        clicables. Ahora se evalúa por frame en cada activación, buscando
        también en descendientes (Recomendado/Episodios solo enganchan el
        clic en el nombre, no en el frame)."""
        return self._click_target(frame) is not None

    @staticmethod
    def _click_target(frame):
        """El widget donde de verdad está enganchado el clic de la fila.

        OJO: no es el frame. customtkinter redirige `CTkFrame.bind()` al
        CTkCanvas con el que el frame se dibuja el fondo (`_canvas`), así que
        preguntarle al frame por sus bindings devuelve None y generar el
        evento sobre él no dispara nada -- parecía que las listas no tenían
        selección cuando todas la tienen.

        Para Recomendado/Episodios/Descargas el clic está en el `name_lbl`
        hijo, no en el frame de la fila. Se busca recursivamente el primer
        descendiente con <Button-1> para que las flechas funcionen igual en
        todas las pestañas sin tener que tocar cada builder."""
        def _has_click(w):
            try:
                c = getattr(w, "_canvas", None)
                if c is not None and c.bind("<Button-1>"):
                    return c
                if hasattr(w, "bind") and w.bind("<Button-1>"):
                    return w
            except Exception:
                pass
            return None

        hit = _has_click(frame)
        if hit is not None:
            return hit
        # BFS por descendientes: prioriza el frame de la fila si lo tiene,
        # si no el primer label clicable (name). Evita coger ★/🔒/⚡ antes
        # que el nombre buscando en anchura.
        try:
            queue = list(getattr(frame, "winfo_children", lambda: [])())
            # Incluir nietos de header_row/cells
            visited = set()
            while queue:
                w = queue.pop(0)
                if id(w) in visited:
                    continue
                visited.add(id(w))
                hit = _has_click(w)
                if hit is not None:
                    return hit
                try:
                    queue.extend(w.winfo_children())
                except Exception:
                    pass
        except Exception:
            pass
        return None

    def _activate_row(self, frame, anterior=None):
        """Deja *frame* como fila seleccionada; *anterior* es la que lo era.

        La anterior se recibe como parámetro y no se lee de self._active_row:
        cuando se leía de ahí ya estaba reasignada, así que nunca se despintaba
        ninguna y la lista se iba llenando de filas azules."""
        if self._row_is_clickable(frame):
            # Se simula el clic en vez de reimplementar la selección: así la
            # flecha hace EXACTAMENTE lo mismo que pulsar la fila con el ratón
            # (incluido cargar el panel de la derecha), y sigue haciéndolo si
            # esa pestaña cambia lo que ocurre al seleccionar.
            destino = self._click_target(frame)
            if destino is not None:
                destino.event_generate("<Button-1>", x=5, y=5)
                return
        # Listas sin selección propia (solo se consultan): al menos marcar por
        # dónde va uno.
        if anterior is not None and anterior is not frame:
            self._highlight_row(anterior, False)
        self._highlight_row(frame, True)

    def set_active_row(self, frame):
        """Sincroniza la navegación por teclado con la selección hecha a
        ratón. Llamar desde gui/app.py tras seleccionar una fila con clic
        para que la siguiente flecha parta de ahí (antes partía de 0)."""
        try:
            rows = self._row_frames()
            if frame in rows:
                self._active_row = frame
                self._active_index = rows.index(frame)
            elif frame is not None and frame.winfo_exists():
                # Frame ya destruido por repaginado: guardar índice
                self._active_row = frame
        except Exception:
            self._active_row = frame

    def _move_active_row(self, canvas, rows: list, keysym: str):
        actual = rows.index(self._active_row) if self._active_row in rows else None
        if actual is None and self._active_index is not None:
            # La lista se ha repintado entera (ordenar, filtrar, seleccionar...)
            # y los frames de antes ya no existen: se sigue por donde se iba,
            # que si no la flecha volvía a saltar a la primera fila.
            actual = min(self._active_index, len(rows) - 1)

        por_pagina = self._rows_per_page(canvas)
        if actual is None:
            nuevo = 0 if keysym in ("Down", "Next", "Home") else len(rows) - 1
        elif keysym == "Home":
            nuevo = 0
        elif keysym == "End":
            nuevo = len(rows) - 1
        else:
            saltos = {"Down": 1, "Up": -1, "Next": por_pagina, "Prior": -por_pagina}
            nuevo = actual + saltos[keysym]
        nuevo = max(0, min(len(rows) - 1, nuevo))
        if nuevo == actual:
            return                      # ya se estaba en el extremo

        anterior = self._active_row
        self._active_index = nuevo
        self._active_row = rows[nuevo]
        self._activate_row(self._active_row, anterior)

        # Seleccionar puede reconstruir la lista entera, así que la fila se
        # vuelve a buscar por posición antes de arrastrar la vista hasta ella.
        self.update_idletasks()
        rows = self._row_frames()
        if nuevo < len(rows):
            self._active_row = rows[nuevo]
            self._ensure_row_visible(canvas, self._active_row)
        if self.on_row_activated:
            self.on_row_activated(nuevo, self._active_row)

    # ------------------------------------------------------------ anchos --

    def _min_width_for(self, key: str) -> int:
        """Ancho mínimo real de una columna: el mayor entre min_width (el
        que se pidió para arrastrar) y lo que necesita su propio texto de
        cabecera -- si no, arrastrar un separador puede volver a dejar la
        cabecera más ancha que su columna (el bug que motivó TableView)."""
        col = next(c for c in self.columns if c.key == key)
        return max(col.min_width, self._header_min.get(key, 0))

    def _init_widths(self):
        for col in self.columns:
            width = col.width
            if col.header and not col.expand:
                # La cabecera nunca se sale de su columna: si el texto no
                # cabe en el ancho pedido, la columna crece para acomodarlo
                # (esto es justo lo que fallaba antes -- el CTkLabel de la
                # cabecera crecía él solo y desalineaba todo lo de después).
                # Si es ordenable, se mide con la flecha de orden puesta
                # (" ^", ver set_sort_indicator) aunque no se esté mostrando
                # ahora mismo -- si no, la columna se queda justa y el texto
                # se sale en cuanto el usuario la pulsa para ordenar.
                text_to_measure = f"{col.header} ^" if col.sortable else col.header
                needed = self._bold_font.measure(text_to_measure) + self._HEADER_TEXT_PADDING
                self._header_min[col.key] = needed
                width = max(width, needed)
            self._widths[col.key] = width
            # Ancho DESEADO (el que pidió quien construyó la tabla, o el que
            # el usuario dejó al arrastrar) frente al ancho EFECTIVO
            # (self._widths, que puede ser menor si ahora mismo no cabe) --
            # ver fit_to_width. Separarlos es lo que permite que encoger la
            # ventana estreche columnas sin perder el ancho elegido: al
            # volver a ensanchar, se recuperan solas.
            self._desired[col.key] = width
        self._recompute_padx()

    def _recompute_padx(self):
        """padx que debe usar la CELDA de cada fila para esta columna --
        ver col_padx. Debe reproducir EXACTAMENTE lo que _build_header
        dibuja de verdad: la primera columna VISIBLE no lleva margen
        izquierdo; las demás llevan 4px, +4px más si la columna ANTERIOR
        VISIBLE es resizable=True (porque _build_header le dibuja un
        separador arrastrable de 4px detrás, ver el bucle de más abajo) --
        un separador que solo existe en la cabecera, nunca en las filas.
        Las columnas ocultas no cuentan ni como primera ni como vecina:
        si se oculta "Nombre original", "Detectado" pasa a ser la primera
        columna visible y debe quedar sin margen izquierdo. Antes cada
        tabla copiaba esta cuenta a mano fila por fila ("mirror sash X|Y"
        en gui/app.py) y se desalineaba en cuanto cambiaba una columna sin
        que quien tocara ESA columna se acordara de las demás (bug real,
        repetido dos veces: primero con Estreno/Episodios que faltan,
        luego con Peso en Archivos al añadir el logo de Jellyfin/Plex)."""
        visible = [c for c in self.columns if c.key not in self._hidden]
        self._padx = {}
        for i, col in enumerate(visible):
            if i == 0:
                self._padx[col.key] = (0, 0)
            else:
                prev_resizable = visible[i - 1].resizable
                self._padx[col.key] = (8, 0) if prev_resizable else (4, 0)

    def col_width(self, key: str) -> int:
        return self._widths[key]

    # Alto por defecto de una celda construida con cell() -- ver ahí por qué
    # hace falta un alto EXPLÍCITO. Cada tabla ajusta el suyo (table.row_height
    # = N) justo después de construirla, según lo alto que sea su fila real.
    row_height = 26

    def cell(self, parent, key: str, height=None, pady=0, padx=None):
        """Contenedor de UNA celda de fila, con el ancho de su columna y
        recorte real -- devuelve un CTkFrame ya empaquetado dentro de
        *parent*; el llamador mete dentro su etiqueta/botón con
        pack(fill="both", expand=True).

        Esto es lo que hace que una columna NO PUEDA desbordar, y sustituye
        a la estrategia anterior de "medir el texto y recortarlo antes de
        pintarlo" (_fit_text en gui/app.py). En Tk, `width` en una etiqueta
        es un MÍNIMO, no un máximo: si el texto no cabe, el widget crece y
        empuja a todas las columnas siguientes -- medido de verdad, una
        CTkLabel(width=200) con un nombre de release largo ocupaba 616px y
        movía la columna de al lado de x=200 a x=616. Por eso la alineación
        dependía de que CADA sitio que escribe en una celda midiera bien y
        usara el MISMO presupuesto de píxeles, y no lo hacían (en Archivos
        había tres presupuestos distintos entre _refresh_table/_update_row/
        _apply_col_widths, así que la misma celda acababa con un ancho u
        otro según quién la hubiera tocado la última: unas filas cuadraban
        y otras se salían por la derecha, escondiendo los botones del final).

        Con pack_propagate(False) el contenedor NUNCA crece para acomodar a
        su contenido: lo que no cabe se recorta, mida quien mida. _fit_text
        se sigue usando, pero solo para que el corte quede bonito ("…") --
        si se equivoca, ya no descuadra nada.

        OJO -- pack_propagate(False) fija el alto además del ancho, así que
        la celda necesita un `height` correcto o recortará en vertical: por
        defecto self.row_height (ajústalo por tabla), o pásalo aquí para
        una celda concreta más alta (p.ej. el bloque de dos líneas de
        "Liberar espacio").

        La columna expand=True no lleva ancho propio (es "lo que sobre"),
        así que su celda se empaqueta con fill/expand -- pero con la misma
        propagación desactivada, para que su contenido tampoco pueda
        estirarla más allá del hueco real disponible."""
        col = next(c for c in self.columns if c.key == key)
        frame = ctk.CTkFrame(
            parent, fg_color="transparent",
            width=1 if col.expand else self._widths[key],
            height=height if height is not None else self.row_height)
        frame.pack_propagate(False)
        pack_kwargs = {"side": "left",
                       "padx": self.col_padx(key) if padx is None else padx,
                       "pady": pady}
        if col.expand:
            pack_kwargs.update(fill="both", expand=True)
        frame.pack(**pack_kwargs)
        self._cells.setdefault(key, []).append(frame)
        return frame

    def _apply_cell_widths(self, keys=None):
        """Reajusta el ancho de las celdas YA construidas (ver cell()) --
        llamado al cambiar el ancho de una columna, para que las filas ya
        pintadas sigan cuadrando con la cabecera sin tener que redibujar la
        tabla entera. De paso descarta las celdas de filas ya destruidas
        (las pestañas destruyen sus filas de formas distintas, no todas vía
        clear_rows)."""
        for key in (keys if keys is not None else list(self._cells)):
            frames = self._cells.get(key)
            if not frames:
                continue
            alive = []
            for frame in frames:
                if not frame.winfo_exists():
                    continue
                alive.append(frame)
                col = next(c for c in self.columns if c.key == key)
                if not col.expand:
                    frame.configure(width=self._widths[key])
            self._cells[key] = alive

    def col_padx(self, key: str) -> tuple:
        """padx que debe usar el widget de una FILA para esta columna --
        única fuente de verdad compartida con la cabecera (ver _init_widths).
        Úsalo en vez de escribir `padx=(4, 0)`/`(8, 0)` a mano en cada
        `.pack()` de una fila: así, si la lista de columnas cambia (se
        añade una, se marca resizable=True, se oculta una...), las filas
        ya construidas con esto se mantienen alineadas sin tocar nada
        más."""
        return self._padx[key]

    def visible_columns(self) -> list:
        """Columnas NO ocultas, en orden -- quien construye filas debe
        iterar por esto (o consultar is_hidden) y saltarse las ocultas."""
        return [c for c in self.columns if c.key not in self._hidden]

    def is_hidden(self, key: str) -> bool:
        return key in self._hidden

    def hidden_columns(self) -> set:
        return set(self._hidden)

    def set_hidden(self, hidden_keys, notify: bool = True):
        """Oculta/muestra columnas de golpe (persistencia: quien use esto
        debe guardar hidden_columns() y volver a pasárselo al construir la
        tabla). Nunca permite ocultar la ÚLTIMA columna visible -- una
        tabla sin ninguna columna no tiene cabecera ni filas que pintar.
        Reconstruye la cabecera y recalcula col_padx para que cabecera y
        filas sigan alineadas entre las columnas que quedan. notify=False
        se usa al restaurar desde disco en la construcción, para no
        disparar el callback antes de que el resto de la tabla exista."""
        new_hidden = set(hidden_keys)
        if not [c for c in self.columns if c.key not in new_hidden]:
            return
        if new_hidden != self._hidden:
            self._hidden = new_hidden
            self._recompute_padx()
            self._build_header()
            if notify and self.on_visibility_changed:
                self.on_visibility_changed()

    def _toggle_column_visible(self, key: str):
        """Muestra u oculta una columna (usado por el menú contextual de
        la cabecera). El caso "ocultar la última visible" lo bloquea
        set_hidden."""
        if key in self._hidden:
            self.set_hidden(self._hidden - {key})
        else:
            self.set_hidden(self._hidden | {key})

    def set_width(self, key: str, width: int, refresh: bool = True):
        """Cambia el ancho base de una columna (p.ej. al redimensionar la
        ventana en Archivos, ver _on_table_resize) -- no toca columnas
        expand=True, que siempre ocupan el espacio sobrante. Nunca deja la
        columna más estrecha de lo que necesita su propio texto de
        cabecera (ver _min_width_for)."""
        self._widths[key] = max(width, self._min_width_for(key))
        self._desired[key] = self._widths[key]
        self._apply_cell_widths(keys=[key])
        if refresh:
            self.refresh_header(keys=[key])

    def fit_to_width(self, avail_width: int, min_expand: int = 60):
        """Hace que la suma de las columnas QUEPA de verdad en *avail_width*
        -- llámalo cuando cambie el ancho disponible de la tabla (ver
        _on_table_resize en Archivos).

        El recorte de cell() impide que una celda empuje a las siguientes,
        pero NO cubre el otro caso que también deja los botones del final
        fuera de la vista: que la SUMA de todos los anchos supere el ancho
        real de la tabla (p.ej. tras ensanchar varias columnas a mano y
        luego encoger la ventana, o ensanchar el panel lateral). Tk no
        encoge por su cuenta un widget con ancho fijo -- simplemente lo
        dibuja fuera del contenedor, donde queda recortado e invisible.

        Aquí las columnas arrastrables se encogen proporcionalmente hasta
        que todo cabe, nunca por debajo de su mínimo real (_min_width_for,
        que ya respeta el texto de su cabecera). Se encoge el ancho
        EFECTIVO, no el DESEADO (ver _desired): al volver a haber sitio,
        cada columna recupera sola el ancho que el usuario había elegido,
        así que esto nunca le "roba" un ajuste hecho a mano.

        min_expand: hueco que se le reserva a la columna expand=True (si la
        hay) antes de repartir el resto. Devuelve la lista de columnas cuyo
        ancho efectivo ha cambiado."""
        visible = self.visible_columns()
        if not visible or avail_width <= 1:
            return []

        # + header_right_pad: hueco que SOLO existe en la cabecera (ver
        # __init__) -- si no se descuenta aquí, las columnas pueden crecer
        # hasta llenar el ancho de las filas y dejar la cabecera desbordada.
        padx_total = sum(sum(self.col_padx(c.key)) for c in visible) + self._header_right_pad
        expand_col = next((c for c in visible if c.expand), None)
        shrinkable = [c for c in visible if c.resizable and not c.expand]
        frozen = sum(self._widths[c.key] for c in visible
                     if not c.expand and c not in shrinkable)
        budget = avail_width - padx_total - frozen - (min_expand if expand_col else 0)
        desired_total = sum(self._desired.get(c.key, self._widths[c.key]) for c in shrinkable)

        changed = []
        for col in shrinkable:
            desired = self._desired.get(col.key, self._widths[col.key])
            if desired_total <= budget or desired_total <= 0:
                new_w = desired                      # cabe tal cual: recuperar el ancho elegido
            else:
                new_w = max(self._min_width_for(col.key),
                            int(desired * max(0.0, budget) / desired_total))
            if new_w != self._widths[col.key]:
                self._widths[col.key] = new_w
                changed.append(col.key)

        # La columna expand no tiene ancho propio (es "lo que sobra"), pero
        # su valor en _widths sí se consulta para recortar texto (_fit_text),
        # así que se deja al día con el hueco real que le queda.
        if expand_col is not None:
            used = sum(self._widths[c.key] for c in visible if not c.expand)
            self._widths[expand_col.key] = max(0, avail_width - padx_total - used)

        if changed:
            self._apply_cell_widths(keys=changed)
            self.refresh_header(keys=changed)
        return changed

    # ------------------------------------------------------------ cabecera --

    def _next_resizable_pair(self, key: str):
        keys = [c.key for c in self.visible_columns()]
        i = keys.index(key)
        return keys[i + 1] if i + 1 < len(keys) else None

    def _show_column_menu(self, event):
        """Menú contextual (clic derecho en la cabecera) para ocultar/
        mostrar columnas -- las columnas hideable=False no aparecen. El
        estado se persiste fuera (ver on_visibility_changed)."""
        menu = tk.Menu(self, tearoff=0)
        for col in self.columns:
            if not col.hideable:
                continue
            menu.add_checkbutton(
                label=col.header or col.key,
                variable=tk.BooleanVar(self, value=col.key not in self._hidden),
                onvalue=True, offvalue=False,
                command=lambda k=col.key: self._toggle_column_visible(k))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _build_header(self):
        for w in self.header_frame.winfo_children():
            w.destroy()
        self._header_labels = {}

        for i, col in enumerate(self.visible_columns()):
            lbl = ctk.CTkLabel(
                self.header_frame, text=col.header, font=self._bold_font, anchor=col.anchor,
                width=0 if col.expand else self._widths[col.key],
                cursor="hand2" if col.sortable else "")
            padx = (self._PADX_BETWEEN, 0) if i > 0 else (0, 0)
            if col.expand:
                lbl.pack(side="left", padx=padx, pady=4, fill="x", expand=True)
            else:
                lbl.pack(side="left", padx=padx, pady=4)
            self._header_labels[col.key] = lbl
            if col.sortable:
                lbl.bind("<Button-1>", lambda e, k=col.key: self.on_header_click(k) if self.on_header_click else None)
            # Menú contextual solo si quien construyó la tabla lo ha
            # activado (ver on_visibility_changed) -- las otras tablas
            # (Episodios/Historial/etc.) no saben saltarse columnas
            # ocultas en sus filas, así que no deben poder ocultarlas.
            if col.hideable and self.on_visibility_changed is not None:
                lbl.bind("<Button-3>", lambda e: self._show_column_menu(e))

            if col.resizable:
                right_key = self._next_resizable_pair(col.key)
                if right_key is not None:
                    sash = tk.Frame(self.header_frame, width=4, bg="#505060",
                                     cursor="sb_h_double_arrow")
                    sash.pack(side="left", fill="y", pady=6)
                    sash.bind("<ButtonPress-1>", lambda e, l=col.key, r=right_key: self._sash_press(e, l, r))
                    sash.bind("<B1-Motion>", self._sash_motion)
                    sash.bind("<ButtonRelease-1>", self._sash_release)

        if self._header_right_pad:
            # Ver header_right_pad en __init__ -- 0 por defecto, no toca
            # nada; solo se reserva este hueco en las tablas que lo pidan
            # explícitamente.
            ctk.CTkFrame(self.header_frame, width=self._header_right_pad, height=1,
                         fg_color="transparent").pack(side="right")

    def refresh_header(self, keys=None):
        """Actualiza el ancho de las etiquetas ya construidas -- NUNCA
        destruye/recrea los widgets de la cabecera (a diferencia de
        _build_header, que solo se llama una vez, al construir la
        tabla). Si esto reconstruyera en cada arrastre de separador,
        el propio separador que está recibiendo los eventos de ratón
        (<B1-Motion>) se destruiría a mitad de gesto, cortando el
        arrastre en cuanto el cursor se moviera -- justo el bug que
        reportó el usuario ("se suelta al salir del separador").

        keys: si se da, solo reconfigura esas columnas (p.ej. las dos
        que cambian en un arrastre de separador) -- reconfigurar TODAS
        las etiquetas en cada píxel de arrastre, aunque no cambien de
        ancho, generaba más trabajo de redibujado del necesario."""
        for col in self.visible_columns():
            if col.expand or (keys is not None and col.key not in keys):
                continue
            lbl = self._header_labels.get(col.key)
            if lbl is not None:
                lbl.configure(width=self._widths[col.key])

    def header_label(self, key: str):
        return self._header_labels.get(key)

    def set_sort_indicator(self, key: "str | None", ascending: bool):
        """Marca *key* como la columna de orden activa -- su cabecera
        pasa a "{header} ^" (ascendente) o "{header} v" (descendente).
        ASCII, no ▲/▼: esos caracteres Unicode dejan un recuadro visible
        en el sistema del usuario (mismo motivo que el toggle de
        expandir/colapsar de Episodios que faltan). Cualquier OTRA
        columna sortable que tuviera flecha vuelve a su texto plano --
        solo una columna muestra indicador a la vez. Solo toca texto,
        nunca ancho -- refresh_header() no lo pisa (esa solo toca
        width=)."""
        arrow = "^" if ascending else "v"
        for col in self.visible_columns():
            if not col.sortable:
                continue
            lbl = self._header_labels.get(col.key)
            if lbl is None:
                continue
            lbl.configure(text=f"{col.header} {arrow}" if col.key == key else col.header)

    # --------------------------------------------------- arrastre de sash --

    def _sash_press(self, event, left_key, right_key):
        self._sash_state = {
            "left": left_key, "right": right_key, "x0": event.x_root,
            "left_w": self._widths[left_key], "right_w": self._widths[right_key],
        }

    def _sash_motion(self, event):
        if not self._sash_state:
            return
        delta = event.x_root - self._sash_state["x0"]
        new_l = max(self._min_width_for(self._sash_state["left"]), self._sash_state["left_w"] + delta)
        new_r = max(self._min_width_for(self._sash_state["right"]), self._sash_state["right_w"] - delta)
        self._widths[self._sash_state["left"]] = new_l
        self._widths[self._sash_state["right"]] = new_r
        # Arrastrar = el usuario ELIGE ese ancho -- se guarda también como
        # deseado, para que fit_to_width lo respete/recupere (ver _desired).
        self._desired[self._sash_state["left"]] = new_l
        self._desired[self._sash_state["right"]] = new_r
        self._apply_cell_widths(keys=[self._sash_state["left"], self._sash_state["right"]])
        self.refresh_header(keys=[self._sash_state["left"], self._sash_state["right"]])
        if self.on_column_resize:
            self.on_column_resize()

    def _sash_release(self, event):
        self._sash_state = None
        if self.on_widths_changed:
            self.on_widths_changed(self.all_widths())

    def all_widths(self) -> dict:
        """Anchos actuales de todas las columnas no-expand -- para guardar
        en disco tras un arrastre (ver on_widths_changed). No se incluye
        la columna expand porque su ancho no es un valor propio, es "lo
        que sobra" tras restar las demás."""
        return {c.key: self._widths[c.key] for c in self.columns if not c.expand}

    # ------------------------------------------------------------- cuerpo --

    on_column_resize = None   # callback opcional: redibujar filas tras arrastrar un sash
    on_widths_changed = None  # callback opcional: persistir anchos tras soltar un separador

    def clear_rows(self):
        """Vacía el cuerpo de la tabla (todas las filas) -- la cabecera
        vive en un frame aparte, así que esto nunca se la lleva por
        delante."""
        for w in self.body.winfo_children():
            w.destroy()
        self._cells = {}   # las celdas de esas filas ya no existen -- ver cell()/_apply_cell_widths

    def scroll_to_top(self):
        """No-op si scrollable=False -- self.body es un CTkFrame normal,
        sin canvas de scroll que mover (el scroll, si hace falta, lo da
        la pantalla que contiene esta tabla)."""
        if self.scrollable:
            self.body._parent_canvas.yview_moveto(0)

    # ------------------------------------------------- tamaño de página --

    def enable_dynamic_page_size(self, on_change):
        """A partir de ahora, page_size se recalcula solo cuando el
        contenedor cambia de alto (redimensionar la ventana), en vez de
        quedarse fijo en DEFAULT_PAGE_ROWS -- on_change(nuevo_tamaño) se
        llama solo cuando el número de filas que caben cambia de verdad,
        nunca en cada píxel de un arrastre (ver _on_body_configure).
        No-op si scrollable=False: sin CTkScrollableFrame no hay canvas
        cuyo alto real represente "lo que cabe sin scroll" (ver
        Sincronizar visionado, cuyas tablas viven dentro del scroll de
        toda la pestaña, no de un hueco acotado propio)."""
        if not self.scrollable:
            return
        self.on_page_size_changed = on_change
        # add="+": este mismo canvas puede tener ya otro binding de
        # <Configure> propio de la pestaña (p.ej. _on_table_resize en
        # Archivos, que solo reajusta anchos de columna) -- sin add="+"
        # este binding lo sustituiría en vez de convivir con él.
        self.body._parent_canvas.bind("<Configure>", self._on_body_configure, add="+")

    def _on_body_configure(self, event):
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(
            self._RESIZE_DEBOUNCE_MS, lambda h=event.height: self._recompute_page_size(h))

    def _recompute_page_size(self, avail_height):
        self._resize_after_id = None
        if self._row_stride_px is None or avail_height <= 1:
            # Todavía no se ha medido ninguna fila real, o el canvas aún
            # no tiene geometría de verdad (ventana sin mapear) -- se
            # autocorrige en el siguiente note_rows_rendered().
            return
        fitting = max(self.MIN_PAGE_ROWS, avail_height // self._row_stride_px)
        if fitting != self.page_size:
            self.page_size = fitting
            if self.on_page_size_changed:
                self.on_page_size_changed(fitting)

    def note_rows_rendered(self, n_rows: int):
        """Las funciones _render_*_page deben llamar a esto justo al
        terminar de construir su página -- mide el alto medio real por
        fila (self._row_stride_px) y lo usa para saber cuántas filas
        caben la próxima vez que cambie el alto disponible.

        Se mide el promedio de TODA la página (winfo_reqheight() / n_rows),
        no el alto de una sola fila, para que absorba sin más el pady/padx
        propio de cada tabla (Archivos usa pady=1; las tablas "de tarjeta"
        como Episodios/Protegidos usan más espaciado) sin necesitar una
        constante de alto distinta por pestaña. winfo_reqheight() (no
        winfo_height()) porque ya está disponible nada más construir los
        widgets, incluso antes de que la ventana esté mapeada en
        pantalla -- así se puede medir en el primerísimo dibujado, no
        solo tras un redimensionado real.

        Nota: si la página incluye una fila "expandida" (ver
        self._missing_ep_expanded en gui/app.py, el estado de expandir/
        colapsar persiste entre redibujados), el alto medido sale un poco
        mayor de lo estrictamente necesario y el tamaño de página calculado
        es ligeramente conservador -- seguro (el canvas absorbe cualquier
        desajuste) y se autocorrige en cuanto el usuario colapsa filas.

        IMPORTANTE -- por qué la autocorrección solo dispara UNA vez: la
        primera versión de esto volvía a llamar a on_page_size_changed
        cada vez que la medida cambiaba (con el cuidado de solo remedir en
        páginas "completas"), pero eso no bastaba -- con una lista ya
        corta (menos elementos que page_size), TODA página es "completa"
        por definición (page_items nunca puede pasar del total), así que
        cada redibujado (p.ej. al ir terminando subidas una a una, o justo
        al arrancar con pocos resultados) volvía a remedir, y cualquier
        variación de un solo píxel entre medidas (redondeo de división
        entera, una fila con distinto contenido) disparaba otra
        autocorrección más -- un bucle de redibujado sin fin, reproducido
        dos veces en sesiones reales. Ahora la autocorrección SOLO ocurre
        una vez, la primerísima vez que hay una medida real (para
        corregir el valor de arranque, DEFAULT_PAGE_ROWS, por el real).
        A partir de ahí, page_size únicamente vuelve a cambiar por una
        redimensión de ventana genuina (ver _on_body_configure, que solo
        reacciona a que el propio CANVAS cambie de tamaño, no a que
        cambien las filas dentro) -- nunca porque el contenido cambie y
        se redibuje, así que un redibujado ya no puede encadenar otro."""
        if n_rows <= 0 or not self.scrollable:
            return
        first_measurement = self._row_stride_px is None
        if not first_measurement and n_rows < self.page_size:
            return   # página corta -- no fiable para refinar la medida (ver más arriba)
        self.update_idletasks()
        self._row_stride_px = max(1, self.body.winfo_reqheight() // n_rows)
        if first_measurement and self.on_page_size_changed:
            self.after(0, lambda: self._recompute_page_size(self.body._parent_canvas.winfo_height()))
