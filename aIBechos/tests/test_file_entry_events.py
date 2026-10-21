"""Registro de lo que le pasa a cada archivo (FileEntry.events) y el
tooltip de Archivos que lo enseña: cuándo se añadió, detectó, identificó y
subió, y por qué está en el estado que está."""

import time

from core.api_client import MediaInfo
from core.file_entry import EVENT_LIMIT, FileEntry, entry_details, upload_record


def _info():
    return MediaInfo(tmdb_id=1399, media_type="tv", title="Juego de tronos", original_title="",
                     year="2011", season=1, episode=2)


def _texts(entry):
    return [t for _ts, t in entry_details(entry)["events"]]


def test_al_anadir_apunta_cuando_y_lo_detectado(tmp_path):
    e = FileEntry(str(tmp_path / "Juego.de.tronos.S01E02.mkv"))
    kinds = [ev[1] for ev in e.events]
    assert kinds == ["added", "detected"]
    assert abs(e.events[0][0] - time.time()) < 5
    assert "S01E02" in _texts(e)[1]


def test_cambios_de_estado_con_su_motivo(tmp_path):
    e = FileEntry(str(tmp_path / "a.mkv"))
    e.media_info = _info()
    e.confidence = 92
    e.new_name = "Juego de tronos 1x02.mkv"
    e.status = "en_cola"
    e.status = "subiendo"
    e.status = "subiendo"                   # cada tramo de progreso: no repite
    e.status = "omitido"
    e.error_msg = "Ya está en el servidor"   # el motivo llega después del estado
    t = _texts(e)
    assert t[2:] == ["Identificado como Juego de tronos (2011) 1x02",
                     "Nuevo nombre: Juego de tronos 1x02.mkv",
                     "En cola", "Subiendo", "Omitido: Ya está en el servidor"]
    d = entry_details(e)
    assert d["reason"] == "Ya está en el servidor"
    assert "saltado" in d["status_help"]
    assert ("Identificado como", "Juego de tronos (2011) 1x02 (serie · TMDB · confianza 92%)") in d["rows"]


def test_motivo_puesto_antes_que_el_estado(tmp_path):
    e = FileEntry(str(tmp_path / "a.mkv"))
    e.error_msg = "Omitido: ya existía un archivo local con ese nombre"
    e.status = "omitido"
    assert _texts(e)[-1] == "Omitido: ya existía un archivo local con ese nombre"
    assert [ev[1] for ev in e.events].count("note") == 0


def test_se_guarda_en_la_sesion_con_sus_horas(tmp_path):
    e = FileEntry(str(tmp_path / "a.mkv"))
    e.status = "subido"
    e.events[-1][0] = 1_700_000_000.0        # una hora "real" antigua
    r = FileEntry.from_dict(e.to_dict())
    assert r.events == e.events               # no las de reconstruir ahora
    assert entry_details(r)["rows"][-2] == ("Subido el", 1_700_000_000.0)


def test_sesion_antigua_sin_registro_no_inventa_fechas(tmp_path):
    d = FileEntry(str(tmp_path / "a.mkv")).to_dict()
    d.pop("events")
    d["status"] = "subido"
    r = FileEntry.from_dict(d)
    assert r.events == []
    # La fecha de subida sale entonces del historial de subidas.
    hist = [{"ts": 1_600_000_000.0, "filename": "a.mkv", "status": "ok", "remote": "/series/a.mkv",
             "person": "Ana", "local_path": ""},
            {"ts": 1_650_000_000.0, "filename": "a.mkv", "status": "error"}]
    rec = upload_record(hist, r)
    assert rec["ts"] == 1_600_000_000.0
    rows = dict(entry_details(r, record=rec)["rows"])
    assert rows["Subido el"] == 1_600_000_000.0
    assert rows["En el servidor"] == "/series/a.mkv" and rows["Subido por"] == "Ana"


def test_el_registro_no_crece_sin_limite(tmp_path):
    e = FileEntry(str(tmp_path / "a.mkv"))
    for i in range(EVENT_LIMIT * 2):
        e.status = "en_cola" if i % 2 else "subiendo"
    assert len(e.events) == EVENT_LIMIT
    assert e.events[-1][2] == "en_cola"


def test_tooltip_de_la_fila(qtbot, tmp_path):
    from PySide6.QtCore import Qt
    from gui_qt.files.model import COL_FAV, COL_NAME, COL_STAT, FilesModel

    class Host:
        def __init__(self):
            self.files = []

        def _preview_remote_path(self, entry):
            return "/series/Juego de tronos/Temporada 01/"

        def _file_size_text(self, entry):
            return "1,2 GB"

        def _history_path(self):
            return tmp_path / "upload_history.json"

        def _load_history(self):
            return []

        def _fav_symbol(self, entry):
            return "☆"

        def _entry_is_favorite(self, entry):
            return False

    host = Host()
    e = FileEntry(str(tmp_path / "Juego.de.tronos.S01E02.mkv"))
    e.media_info = _info()
    e.status = "omitido"
    e.error_msg = "Ya está en el servidor"
    host.files = [e]
    m = FilesModel(host)
    m.reset()
    tip = m.data(m.index(0, COL_STAT), Qt.ToolTipRole)
    assert m.data(m.index(0, COL_NAME), Qt.ToolTipRole) == tip     # igual en toda la fila
    for text in ("Omitido", "Motivo:", "Ya está en el servidor", "Juego de tronos (2011) 1x02",
                 "Temporada 01", "1,2 GB", "Qué ha pasado", "Añadido a la lista", "hoy "):
        assert text in tip, text
    assert "Ya está en el servidor" in tip
    assert m.data(m.index(0, COL_FAV), Qt.ToolTipRole) != tip       # ★ conserva el suyo


def test_filtro_por_palabras_sin_acentos_ni_mayusculas(tmp_path):
    from core.file_entry import entry_matches
    e = FileEntry(str(tmp_path / "Juego.de.tronos.S01E02.mkv"))
    e.media_info = MediaInfo(tmdb_id=1, media_type="tv", title="Pokémon", original_title="", year="1997")
    assert entry_matches(e, "")
    assert entry_matches(e, "JUEGO s01e02")
    assert entry_matches(e, "pokemon")             # título identificado, sin tilde
    assert entry_matches(e, "pendiente")           # por estado
    assert not entry_matches(e, "juego vikingos")  # todas las palabras


def test_filtro_por_grupo_de_estado(tmp_path):
    from core.file_entry import FILTER_GROUPS, entry_matches
    e = FileEntry(str(tmp_path / "a.mkv"))
    e.status = "omitido"
    assert entry_matches(e, group="omitidos") and entry_matches(e, group="todos")
    assert not entry_matches(e, group="subidos")
    e.status = "subiendo"
    assert entry_matches(e, group="en_curso")
    # Ningún estado se queda fuera de todos los grupos.
    todos = set().union(*(g for g in FILTER_GROUPS.values() if g))
    from core.file_entry import STATUS_HELP
    assert set(STATUS_HELP) <= todos
