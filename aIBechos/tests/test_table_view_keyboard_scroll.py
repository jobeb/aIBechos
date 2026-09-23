"""Desplazamiento de los listados con el teclado (gui/table_view.py).

Estos tests SÍ construyen widgets de verdad, así que se saltan solos donde no
haya customtkinter/tkinter -- entre otros sitios, el CI (ver
.github/workflows/tests.yml, que a propósito no instala las dependencias de
interfaz). En el equipo de desarrollo se ejecutan y cubren el hueco que deja
pyflakes: dos fallos reales que solo aparecían al ARRANCAR la app.

  1. `TableView.bind_all(...)` -> AttributeError: CTkBaseClass lo prohíbe.
     Tumbaba el arranque entero antes de pintar la primera pestaña.
  2. `self._canvas()` -> TypeError: 'CTkCanvas' object is not callable.
     CTkFrame ya define un atributo `_canvas`, que tapaba el método. El
     binding se disparaba en cada tecla y moría en la primera línea, así que
     "no funcionan las flechas" sin una sola pista en pantalla.
"""

import pytest

ctk = pytest.importorskip("customtkinter")

from gui.table_view import ColumnSpec, TableView


class _FakeKey:
    """Lo justo que mira _on_scroll_key de un evento de tecla."""
    def __init__(self, keysym):
        self.keysym = keysym
        self.x_root = 0
        self.y_root = 0


@pytest.fixture(scope="module")
def root():
    """UN solo root de Tk para todo el módulo: crear y destruir varios en el
    mismo proceso falla al recargar tk.tcl ("this probably means that tk wasn't
    installed properly"), y con un fixture por test eso hacía saltar uno suelto
    de forma aleatoria."""
    try:
        win = ctk.CTk()
    except Exception as e:                      # sin entorno gráfico
        pytest.skip(f"sin display para tkinter: {e}")
    win.geometry("600x200")
    yield win
    win.destroy()


@pytest.fixture
def tabla(root):
    holder = ctk.CTkFrame(root)
    holder.pack(fill="both", expand=True)
    tv = TableView(holder, columns=[ColumnSpec("a", "Columna", width=200)])
    tv.pack(fill="both", expand=True)
    # Filas de sobra para que haya algo que desplazar de verdad.
    for i in range(60):
        ctk.CTkLabel(tv.body, text=f"fila {i}").pack(fill="x")
    root.update()
    # El ratón no está de verdad encima de la tabla en un test: se simula el
    # "¿qué widget hay bajo el puntero?" que usa _on_scroll_key.
    tv.winfo_containing = lambda x, y: tv.body
    yield tv
    holder.destroy()


def test_construir_la_tabla_no_revienta_por_bind_all(tabla):
    # Si _enable_keyboard_scroll usara self.bind_all, la fixture ni llegaría.
    assert tabla._scroll_canvas() is not None


def test_flecha_abajo_desplaza_hacia_abajo(tabla):
    antes = tabla._scroll_canvas().yview()[0]
    tabla._on_scroll_key(_FakeKey("Down"))
    tabla.update()
    assert tabla._scroll_canvas().yview()[0] > antes


def test_flecha_arriba_vuelve_hacia_arriba(tabla):
    tabla._on_scroll_key(_FakeKey("Next"))     # bajar una página primero
    tabla.update()
    medio = tabla._scroll_canvas().yview()[0]
    assert medio > 0
    tabla._on_scroll_key(_FakeKey("Up"))
    tabla.update()
    assert tabla._scroll_canvas().yview()[0] < medio


def test_fin_e_inicio_van_a_los_extremos(tabla):
    tabla._on_scroll_key(_FakeKey("End"))
    tabla.update()
    assert tabla._scroll_canvas().yview()[1] == pytest.approx(1.0, abs=0.01)
    tabla._on_scroll_key(_FakeKey("Home"))
    tabla.update()
    assert tabla._scroll_canvas().yview()[0] == pytest.approx(0.0, abs=0.01)


def test_desplaza_aunque_el_foco_este_en_un_cuadro_de_texto_de_fuera(tabla, root):
    """El caso que rompía la función entera.

    Se miraba el foco y se descartaba la tecla si lo tenía cualquier campo de
    texto. En la aplicación real casi siempre lo tiene alguno (un buscador, la
    ficha de detalle de la derecha...), así que las flechas no desplazaban NADA
    en ninguna lista. Manda la posición del ratón, igual que con la rueda.
    """
    entry = ctk.CTkEntry(root)
    entry.pack()
    tabla.update()
    tabla.focus_get = lambda: entry._entry   # el widget real que recibe el foco
    antes = tabla._scroll_canvas().yview()[0]
    tabla._on_scroll_key(_FakeKey("Down"))
    tabla.update()
    assert tabla._scroll_canvas().yview()[0] > antes
    entry.destroy()


def test_no_secuestra_las_flechas_de_un_cuadro_de_texto_de_la_propia_tabla(tabla):
    """Única excepción: si se escribe DENTRO de la tabla, las flechas mueven
    el cursor de escritura y no desplazan la lista."""
    entry = ctk.CTkEntry(tabla.body)
    entry.pack()
    tabla.update()
    tabla.focus_get = lambda: entry._entry
    antes = tabla._scroll_canvas().yview()[0]
    assert tabla._on_scroll_key(_FakeKey("Down")) is None
    tabla.update()
    assert tabla._scroll_canvas().yview()[0] == antes


def test_no_hace_nada_si_el_raton_esta_fuera_de_la_tabla(tabla):
    tabla.winfo_containing = lambda x, y: None
    antes = tabla._scroll_canvas().yview()[0]
    assert tabla._on_scroll_key(_FakeKey("Down")) is None
    assert tabla._scroll_canvas().yview()[0] == antes


# --------------------------------------------------------------------------
# Recorrer la lista fila a fila (lo que de verdad se pedía: moverse por las
# filas resaltándolas, no solo desplazar la vista). Las pruebas de arriba usan
# etiquetas sueltas como contenido, así que ejercitan el camino de respaldo
# (sin filas reconocibles -> desplazar y ya); estas construyen filas de verdad.
# --------------------------------------------------------------------------

@pytest.fixture
def tabla_con_filas(root):
    holder = ctk.CTkFrame(root)
    holder.pack(fill="both", expand=True)
    tv = TableView(holder, columns=[ColumnSpec("a", "Columna", width=200)])
    tv.pack(fill="both", expand=True)
    for i in range(40):
        fila = ctk.CTkFrame(tv.body)
        fila.pack(fill="x", pady=1)
        ctk.CTkLabel(fila, text=f"fila {i}").pack(fill="x")
    root.update()
    tv.winfo_containing = lambda x, y: tv.body
    yield tv
    holder.destroy()


def _activa(tv):
    filas = tv._row_frames()
    return filas.index(tv._active_row) if tv._active_row in filas else None


def test_encuentra_las_filas_sin_que_nadie_las_registre(tabla_con_filas):
    assert len(tabla_con_filas._row_frames()) == 40


def test_la_flecha_abajo_avanza_de_fila_en_fila(tabla_con_filas):
    tabla_con_filas._on_scroll_key(_FakeKey("Down"))
    assert _activa(tabla_con_filas) == 0        # la primera pulsación entra
    for esperado in (1, 2, 3):
        tabla_con_filas._on_scroll_key(_FakeKey("Down"))
        assert _activa(tabla_con_filas) == esperado


def test_solo_hay_una_fila_resaltada_a_la_vez(tabla_con_filas):
    for _ in range(5):
        tabla_con_filas._on_scroll_key(_FakeKey("Down"))
    resaltadas = [f for f in tabla_con_filas._row_frames()
                  if f.cget("fg_color") == TableView._ACTIVE_ROW_COLOR]
    assert resaltadas == [tabla_con_filas._active_row]


def test_la_fila_recupera_su_color_al_dejar_de_estar_activa(tabla_con_filas):
    """Volver a resaltar la MISMA fila (al toparse con el final de la lista)
    llegó a guardar el azul como si fuera su color original: la fila se
    quedaba resaltada para siempre al salir de ella."""
    primera = tabla_con_filas._row_frames()[0]
    original = primera.cget("fg_color")
    tabla_con_filas._on_scroll_key(_FakeKey("Down"))       # activa la primera
    for _ in range(3):
        tabla_con_filas._on_scroll_key(_FakeKey("Up"))     # topa con el borde
    tabla_con_filas._on_scroll_key(_FakeKey("Down"))       # y se va a la segunda
    assert primera.cget("fg_color") == original


def test_fin_e_inicio_van_a_la_ultima_y_a_la_primera_fila(tabla_con_filas):
    tabla_con_filas._on_scroll_key(_FakeKey("End"))
    assert _activa(tabla_con_filas) == 39
    tabla_con_filas._on_scroll_key(_FakeKey("Home"))
    assert _activa(tabla_con_filas) == 0


def test_no_se_sale_por_los_extremos(tabla_con_filas):
    for _ in range(60):                      # más pulsaciones que filas
        tabla_con_filas._on_scroll_key(_FakeKey("Up"))
    assert _activa(tabla_con_filas) == 0
    for _ in range(60):
        tabla_con_filas._on_scroll_key(_FakeKey("Down"))
    assert _activa(tabla_con_filas) == 39


def test_avpag_salta_mas_de_una_fila_pero_no_hasta_el_final(tabla_con_filas):
    tabla_con_filas._on_scroll_key(_FakeKey("Down"))
    tabla_con_filas._on_scroll_key(_FakeKey("Next"))
    assert 1 < _activa(tabla_con_filas) < 39


def test_avisa_a_quien_quiera_saber_que_fila_esta_activa(tabla_con_filas):
    vistas = []
    tabla_con_filas.on_row_activated = lambda i, f: vistas.append(i)
    tabla_con_filas._on_scroll_key(_FakeKey("Down"))
    tabla_con_filas._on_scroll_key(_FakeKey("Down"))
    assert vistas == [0, 1]


def test_la_flecha_dispara_la_seleccion_propia_de_la_lista(root):
    """El caso que se pedía: mover la selección DE VERDAD (y con ella el panel
    de detalles de la derecha), no solo pintar la fila de azul.

    Guarda además contra una trampa de customtkinter: `CTkFrame.bind()` no
    engancha en el frame sino en el CTkCanvas con el que se dibuja el fondo
    (`_canvas`), así que buscar el clic en el frame -- o generarlo sobre él --
    no encuentra ni dispara nada, y parecía que ninguna lista tenía selección.
    """
    holder = ctk.CTkFrame(root)
    holder.pack(fill="both", expand=True)
    tv = TableView(holder, columns=[ColumnSpec("a", "Columna", width=200)])
    tv.pack(fill="both", expand=True)
    pulsadas = []
    for i in range(20):
        fila = ctk.CTkFrame(tv.body)
        fila.pack(fill="x", pady=1)
        ctk.CTkLabel(fila, text=f"fila {i}").pack(fill="x")
        fila.bind("<Button-1>", lambda ev, n=i: pulsadas.append(n))
    root.update()
    tv.winfo_containing = lambda x, y: tv.body

    tv._on_scroll_key(_FakeKey("Down"))
    tv._on_scroll_key(_FakeKey("Down"))
    root.update()
    assert pulsadas == [0, 1]
    # Y no se pinta por su cuenta: de eso ya se encarga la propia lista.
    assert all(f.cget("fg_color") != TableView._ACTIVE_ROW_COLOR
               for f in tv._row_frames())
    holder.destroy()
