"""Nombre puesto a mano (core/file_entry.py::apply_manual_name): para lo
que no tiene ficha propia en TMDB (real: "Vengadores Endgame Encore",
identificado como Endgame 2019 y omitido como duplicado)."""

from core.api_client import MediaInfo
from core.file_entry import FileEntry, apply_manual_name

ENCORE = r"C:\dl\Vengadores.Endgame.Encore.(2026).(Spanish.English.Subs).WEBRip.1080p.x265.mkv"


def _endgame():
    return MediaInfo(tmdb_id=299534, media_type="movie", title="Vengadores: Endgame",
                     original_title="Avengers: Endgame", year="2019")


def test_con_ficha_cambia_solo_el_nombre_y_sale_de_omitido():
    e = FileEntry(ENCORE)
    e.media_info = _endgame()
    e.new_name = "Vengadores Endgame (2019).mkv"
    e.status, e.error_msg = "omitido", "Ya existe otro archivo para este mismo contenido"
    e._duplicate_checked = True
    assert apply_manual_name(e, "Vengadores Endgame Encore (2026)") == ""
    assert e.new_name == "Vengadores Endgame Encore (2026).mkv"
    assert e.has_manual_name
    assert e.media_info.tmdb_id == 299534
    assert (e.status, e.error_msg) == ("listo", "")
    assert e._duplicate_checked is False


def test_la_extension_escrita_no_se_duplica():
    e = FileEntry(ENCORE)
    e.media_info = _endgame()
    assert apply_manual_name(e, "Encore (2026).mkv") == ""
    assert e.new_name == "Encore (2026).mkv"


def test_reidentificar_deja_de_valer_el_nombre_manual():
    e = FileEntry(ENCORE)
    e.media_info = _endgame()
    apply_manual_name(e, "Encore (2026)")
    e.new_name = "Vengadores Endgame (2019).mkv"   # p. ej. "Buscar de nuevo"
    assert not e.has_manual_name


def test_sin_ficha_crea_una_de_pelicula_con_titulo_y_anio():
    e = FileEntry(ENCORE)
    assert apply_manual_name(e, "Vengadores Endgame Encore (2026)") == ""
    mi = e.media_info
    assert (mi.tmdb_id, mi.media_type, mi.title, mi.year) == (
        0, "movie", "Vengadores Endgame Encore", "2026")


def test_sin_ficha_no_vale_para_series():
    e = FileEntry(r"C:\dl\Serie Rara 1x03.mkv")
    assert apply_manual_name(e, "Serie Rara 1x03") != ""
    assert e.media_info is None and not e.has_manual_name


def test_rechaza_vacio_y_caracteres_prohibidos():
    e = FileEntry(ENCORE)
    e.media_info = _endgame()
    assert apply_manual_name(e, "   ") != ""
    assert apply_manual_name(e, "Endgame: Encore") != ""
    assert not e.has_manual_name


def test_sobrevive_a_la_sesion():
    e = FileEntry(ENCORE)
    e.media_info = _endgame()
    apply_manual_name(e, "Vengadores Endgame Encore (2026)")
    back = FileEntry.from_dict(e.to_dict())
    assert back.has_manual_name
    assert back.new_name == "Vengadores Endgame Encore (2026).mkv"


# ── Diálogo "Corregir identificación" (búsqueda o nombre exacto) ──

def test_dialogo_modo_busqueda_corrige_detectado_y_quita_nombre_manual(qtbot):
    from gui_qt.files.dialogs import FixIdentificationDialog
    e = FileEntry(ENCORE)
    e.media_info = _endgame()
    apply_manual_name(e, "Encore (2026)")
    d = FixIdentificationDialog(None, e, manual=False)
    qtbot.addWidget(d)
    assert not d.use_manual.isChecked() and not d.name.isEnabled()
    d.title.setText("Avengers Endgame")
    d._save()
    assert d.mode == "search"
    assert e.detected["title"] == "Avengers Endgame"
    assert not e.has_manual_name


def test_dialogo_abre_en_modo_manual_si_ya_tenia_nombre_manual(qtbot):
    from gui_qt.files.dialogs import FixIdentificationDialog
    e = FileEntry(ENCORE)
    e.media_info = _endgame()
    apply_manual_name(e, "Encore (2026)")
    d = FixIdentificationDialog(None, e)
    qtbot.addWidget(d)
    assert d.use_manual.isChecked() and not d.title.isEnabled()
    assert d.name.text() == "Encore (2026)"


def test_dialogo_modo_manual_aplica_nombre_y_enseña_errores(qtbot):
    from gui_qt.files.dialogs import FixIdentificationDialog
    e = FileEntry(ENCORE)
    e.status = "omitido"
    d = FixIdentificationDialog(None, e, manual=True)
    qtbot.addWidget(d)
    d.name.setText("Endgame: x")
    d._save()
    assert d.error.text() and d.result() == 0
    d.name.setText("Vengadores Endgame Encore (2026)")
    d._save()
    assert d.mode == "manual" and e.new_name == "Vengadores Endgame Encore (2026).mkv"
    assert e.status == "listo"


def test_dialogo_no_deja_nombre_manual_si_ya_esta_subido(qtbot):
    from gui_qt.files.dialogs import FixIdentificationDialog
    e = FileEntry(ENCORE)
    e.status = "subido"
    d = FixIdentificationDialog(None, e, manual=True)
    qtbot.addWidget(d)
    assert not d.use_manual.isEnabled() and not d.use_manual.isChecked()
