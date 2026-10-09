"""Guarda de regresión: los tooltips de ★/🔒/⚡ muestran quién/cuándo.

La comprobación es
estática sobre el código de la app (mismo patrón que test_gui_status_colors.py y
test_upload_skip_all.py): los toggles persisten la atribución y los
tooltips la leen.
"""

import ast

from app_source import APP_SOURCE  # mixins de core/ + gui_qt/


def _segment(tree: ast.Module, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            seg = ast.get_source_segment(APP_SOURCE.read_text(encoding="utf-8"), node)
            assert seg is not None, f"No se pudo extraer el código de {name}"
            return seg
    raise AssertionError(f"No existe {name} en la app")


def test_toggle_favorite_guarda_quien_y_cuando():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")), "toggle_favorite")
    assert "app_user_name" in seg, "toggle_favorite no guarda quién (added_by)"
    assert "time.time()" in seg, "toggle_favorite no guarda cuándo (added_at)"


def test_toggle_reservation_guarda_cuando():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")), "toggle_reservation")
    assert "time.time()" in seg, "toggle_reservation no guarda cuándo (reserved_at)"


def test_rayo_guarda_fecha_de_activacion():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")),
                   "_toggle_missing_ep_auto_complete")
    assert "missing_ep_auto_since" in seg, \
        "El rayo no persiste cuándo se activó (missing_ep_auto_since)"


def test_tooltips_de_marcas_muestran_atribucion():
    src = APP_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    assert "added_by" in _segment(tree, "favorite_attribution"), "El tooltip de ★ no muestra quién/cuándo"
    assert "reserved_at" in _segment(tree, "reservation_attribution"), "El tooltip de 🔒 no muestra cuándo"
    # def + tooltip general (Archivos, Episodios, Recomendado) + Liberar espacio.
    assert src.count("favorite_attribution(") >= 3, "Algún ★ no muestra quién/cuándo"
    assert src.count("reservation_attribution(") >= 3, "Algún 🔒 no muestra quién/cuándo"
    assert src.count("favorite_tooltip(") >= 4, "Algún ★ de Archivos/Episodios/Recomendado sin quién/cuándo"
    assert src.count("reservation_tooltip(") >= 4, "Algún 🔒 de Archivos/Episodios/Recomendado sin quién/cuándo"
    seg = _segment(tree, "_auto_btn_tooltip")
    assert "Activo desde" in seg, "El tooltip del rayo no muestra desde cuándo"
    assert "Reservado el:" in src, "La ficha de Protegidos no muestra la fecha"


def test_solo_el_dueno_puede_soltar_reserva():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")), "toggle_reservation")
    assert "solo esa persona puede liberarlo" in seg,         "toggle_reservation ya no restringe liberar al dueño"


def test_rayo_compartido_tiene_sync_y_push():
    tree = ast.parse(APP_SOURCE.read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    for name in ("_sync_auto_series_from_ftp", "_push_auto_series_to_ftp",
                 "_auto_series_remote_path", "_transfer_auto_owners",
                 "_remove_all_auto_owners"):
        assert name in names, f"Falta {name} en la app"


def test_rayo_avisa_si_otro_equipo_lo_tiene():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")),
                   "_toggle_missing_ep_auto_complete")
    assert "other_owners" in seg, "El toggle del rayo no mira dueños de otros equipos"
    # La lógica vive en core/app_auto_complete.py y pide el diálogo a la
    # interfaz con la fábrica _make_confirm_dialog (Tk: _ConfirmDialog).
    assert "_make_confirm_dialog" in seg or "_ConfirmDialog" in seg,         "El toggle del rayo no pide confirmación"
    assert "_push_auto_series_to_ftp" in seg, "El toggle del rayo no publica el cambio"


def test_tooltip_del_rayo_muestra_otros_duenos():
    seg = _segment(ast.parse(APP_SOURCE.read_text(encoding="utf-8")), "_auto_btn_tooltip")
    assert "En auto también en" in seg, \
        "El tooltip del rayo no muestra otros equipos"
