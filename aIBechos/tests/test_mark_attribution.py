"""Guarda de regresión: los tooltips de ★/🔒/⚡ muestran quién/cuándo.

App no se puede instanciar sin tkinter, así que la comprobación es
estática sobre gui/app.py (mismo patrón que test_gui_status_colors.py y
test_upload_skip_all.py): los toggles persisten la atribución y los
tooltips la leen.
"""

import ast
from pathlib import Path

APP_SOURCE = Path(__file__).resolve().parent.parent / "gui" / "app.py"


def _segment(tree: ast.Module, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            seg = ast.get_source_segment(APP_SOURCE.read_text(encoding="utf-8"), node)
            assert seg is not None, f"No se pudo extraer el código de {name}"
            return seg
    raise AssertionError(f"No existe {name} en gui/app.py")


def test_toggle_favorite_guarda_quien_y_cuando():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")), "_toggle_favorite")
    assert "app_user_name" in seg, "_toggle_favorite no guarda quién (added_by)"
    assert "_time.time()" in seg, "_toggle_favorite no guarda cuándo (added_at)"


def test_toggle_reservation_guarda_cuando():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")), "_toggle_reservation")
    assert "_time.time()" in seg, "_toggle_reservation no guarda cuándo (reserved_at)"


def test_rayo_guarda_fecha_de_activacion():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")),
                   "_toggle_missing_ep_auto_complete")
    assert "missing_ep_auto_since" in seg, \
        "El rayo no persiste cuándo se activó (missing_ep_auto_since)"


def test_tooltips_de_marcas_muestran_atribucion():
    src = APP_SOURCE.read_text(encoding="utf-8")
    # def + Archivos + Episodios + Recomendados + Liberar espacio
    assert src.count("_favorite_attribution(") >= 5, \
        "Algún tooltip de ★ no muestra quién/cuándo"
    assert src.count("_reservation_attribution(") >= 5, \
        "Algún tooltip de 🔒 no muestra quién/cuándo"
    seg = _segment(ast.parse(src), "_auto_btn_tooltip")
    assert "Activo desde" in seg, "El tooltip del rayo no muestra desde cuándo"
    assert "Reservado el:" in src, "La ficha de Protegidos no muestra la fecha"


def test_solo_el_dueno_puede_soltar_reserva():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")), "_toggle_reservation")
    assert "solo esa persona puede liberarlo" in seg, \
        "_toggle_reservation ya no restringe liberar al dueño"


def test_rayo_compartido_tiene_sync_y_push():
    tree = ast.parse(APP_SOURCE.read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    for name in ("_sync_auto_series_from_ftp", "_push_auto_series_to_ftp",
                 "_auto_series_remote_path", "_transfer_auto_owners",
                 "_remove_all_auto_owners"):
        assert name in names, f"Falta {name} en gui/app.py"


def test_rayo_avisa_si_otro_equipo_lo_tiene():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")),
                   "_toggle_missing_ep_auto_complete")
    assert "other_owners" in seg, "El toggle del rayo no mira dueños de otros equipos"
    assert "_ConfirmDialog" in seg, "El toggle del rayo no pide confirmación"
    assert "_push_auto_series_to_ftp" in seg, "El toggle del rayo no publica el cambio"


def test_tooltip_del_rayo_muestra_otros_duenos():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")), "_auto_btn_tooltip")
    assert "En auto también en" in seg, \
        "El tooltip del rayo no muestra otros equipos"
