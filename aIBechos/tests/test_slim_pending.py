"""Reemplazo automático al adelgazar: la ligera borra al gordo (ver core/slim_pending.py)."""

import time

import pytest
from unittest.mock import MagicMock

import core.auto_watcher as autowatcher_mod
import core.slim_pending as slim_pending_mod
from core.slim_pending import (
    upsert, find_match, load_pending, save_pending, record, consume, norm_key,
)
from core.auto_watcher import AutoWatcher
from core.api_client import MediaInfo


@pytest.fixture(autouse=True)
def _isolate_dbs(tmp_path, monkeypatch):
    db_path = tmp_path / "auto_processed_test.json"
    monkeypatch.setattr(autowatcher_mod, "_processed_db_path", lambda: db_path)
    pend_path = tmp_path / "slim_pending_test.json"
    monkeypatch.setattr(slim_pending_mod, "_path", lambda: pend_path)


# --- puras -----------------------------------------------------------------

def _tv_entry(**kw):
    e = {"media_type": "tv", "key_norm": norm_key("Mi Serie"),
         "season": 1, "episode": 6, "year": "",
         "heavy_remote_file": "/series/Mi Serie 1x06 Gordo.mkv",
         "heavy_size": 100, "max_light_size": 85,
         "query": "Mi Serie 1x06", "added_ts": time.time(), "added_by": "tester"}
    e.update(kw)
    return e


def test_upsert_reemplaza_misma_clave():
    entries = upsert([], _tv_entry(query="q1"))
    entries = upsert(entries, _tv_entry(query="q2"))
    assert len(entries) == 1
    assert entries[0]["query"] == "q2"
    other = _tv_entry(season=2)
    assert len(upsert(entries, other)) == 2


def test_find_match_tv_acierta_y_falla():
    entries = [_tv_entry()]
    assert find_match(entries, "tv", "Mi Serie", 1, 6, "", 80) is not None
    # Título con otra grafía casa por normalización.
    assert find_match(entries, "tv", "mi serie", 1, 6, "", 80) is not None
    # Otro episodio, otra serie u otro tipo: no hay match.
    assert find_match(entries, "tv", "Mi Serie", 1, 7, "", 80) is None
    assert find_match(entries, "tv", "Otra Serie", 1, 6, "", 80) is None
    assert find_match(entries, "movie", "Mi Serie", 1, 6, "", 80) is None
    # Más pesada que el tope (no es "ligera"): no se borra nada.
    assert find_match(entries, "tv", "Mi Serie", 1, 6, "", 86) is None
    assert find_match(entries, "tv", "Mi Serie", 1, 6, "", 0) is None


def test_find_match_movie_por_titulo_y_ano():
    e = {"media_type": "movie", "key_norm": norm_key("Mi Pelicula"),
         "season": None, "episode": None, "year": "2024",
         "heavy_remote_file": "/pelis/Mi Pelicula Gorda.mkv",
         "heavy_size": 1000, "max_light_size": 850,
         "query": "Mi Pelicula", "added_ts": time.time(), "added_by": "t"}
    assert find_match([e], "movie", "Mi Pelicula", None, None, "2024", 800) is not None
    assert find_match([e], "movie", "Mi Pelicula", None, None, "2023", 800) is None
    # Sin año en el pendiente: solo manda título + peso.
    e2 = dict(e, year="")
    assert find_match([e2], "movie", "Mi Pelicula", None, None, "2023", 800) is not None


def test_load_quita_caducados_y_consume_borra():
    old = _tv_entry(added_ts=time.time() - 31 * 24 * 3600)
    save_pending([old])
    assert load_pending() == []
    save_pending([_tv_entry()])
    assert len(load_pending()) == 1
    consume(_tv_entry())
    assert load_pending() == []


# --- integración con watcher (FTP falso) ------------------------------------

class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


HEAVY = "/series/Mi Serie 1x06 Gordo.mkv"


def _make_watcher(tmp_ok_delete=True):
    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "tv_template": "{serie} {temporada}x{episodio:02d} {titulo}{ext}",
        "min_confidence": 0,
        "rename_local": True,
        "ftp_host": "servidor",
        "ftp_categories": {
            "movie": [{"id": "c1", "name": "Peliculas", "genre_ids": [],
                       "root": "/peliculas", "template": "{serie}/"}],
            "tv": [{"id": "c2", "name": "Series", "genre_ids": [],
                    "root": "/series", "template": "{serie}/Temporada {temporada:02d}/"}],
        },
    })
    tmdb = MagicMock()
    tmdb.search_multi.return_value = [{
        "id": 1, "name": "Mi Serie", "media_type": "tv", "genre_ids": [],
    }]
    tmdb.build_media_info.return_value = MediaInfo(
        tmdb_id=1, media_type="tv", title="Mi Serie",
        original_title="Mi Serie", year="2024",
        season=1, episode=6, genre_ids=[])
    tmdb.get_episode_info.return_value = {}

    state = {"heavy_gone": False}
    ftp = MagicMock()
    ftp.is_connected.return_value = True
    ftp.connect.return_value = (True, "ok")
    ftp.build_remote_path.return_value = "/destino/"
    ftp.get_free_space.return_value = None
    ftp.list_files.return_value = []

    def _size(p):
        if p == HEAVY:
            return None if state["heavy_gone"] else 100
        return None
    ftp.get_remote_size.side_effect = _size

    delete_calls = []

    def _delete(p):
        delete_calls.append(p)
        if tmp_ok_delete and p == HEAVY:
            state["heavy_gone"] = True
            return True, p
        return False, "boom"
    ftp.delete_file.side_effect = _delete

    upload_calls = []

    def _fake_upload(local_path, remote_path, **kw):
        upload_calls.append({"local_path": local_path, **kw})
        return True, "ok"
    ftp.upload_file.side_effect = _fake_upload

    events = []
    file_events = []
    watcher = AutoWatcher("/carpeta", config, tmdb, ftp,
                           on_event=lambda *a, **k: events.append((a, k)),
                           on_file_event=lambda *a, **k: file_events.append((a, k)),
                           ftp_factory=lambda: ftp)
    watcher._is_stable = lambda path: True
    return watcher, ftp, upload_calls, delete_calls, events, file_events


def _ligera(tmp_path):
    # 80 bytes <= max_light 85: cuenta como "ligera" del gordo de 100.
    f = tmp_path / "1.mi.serie.s01e06.WEB-DL.mkv"
    f.write_bytes(b"x" * 80)
    return f


def test_watcher_borra_gordo_y_sube_ligera(tmp_path):
    record(_tv_entry())
    original = _ligera(tmp_path)
    watcher, ftp, upload_calls, delete_calls, events, file_events = _make_watcher()

    watcher._process(original)

    assert delete_calls == [HEAVY], "el gordo debe borrarse antes de subir"
    assert len(upload_calls) == 1, "la ligera debe subir una vez fuera el gordo"
    assert load_pending() == [], "el pendiente se consume tras el reemplazo"
    assert any(v.get("status") == "subido" for v in watcher._processed.values()), watcher._processed
    slim_ev = [kw for (a, kw) in file_events if len(a) > 1 and a[1] == "slim_replaced"]
    assert len(slim_ev) == 1, "la GUI debe enterarse para refrescar Liberar espacio"
    assert slim_ev[0].get("heavy_remote_file") == HEAVY


def test_watcher_sube_si_gordo_ya_no_esta(tmp_path):
    record(_tv_entry())
    original = _ligera(tmp_path)
    watcher, ftp, upload_calls, delete_calls, events, file_events = _make_watcher()
    ftp.get_remote_size.side_effect = lambda p: None  # gordo borrado a mano entre medias

    watcher._process(original)

    assert delete_calls == [], "sin gordo no hay nada que borrar"
    assert len(upload_calls) == 1
    assert load_pending() == [], "el pendiente se consume igualmente"
    assert any(len(a) > 1 and a[1] == "slim_replaced" for (a, kw) in file_events)


def test_watcher_no_marca_si_borrado_falla(tmp_path):
    record(_tv_entry())
    original = _ligera(tmp_path)
    watcher, ftp, upload_calls, delete_calls, events, file_events = _make_watcher(tmp_ok_delete=False)

    watcher._process(original)

    assert delete_calls == [HEAVY]
    assert upload_calls == [], "sin borrar el gordo no se sube nada"
    assert len(load_pending()) == 1, "el pendiente se conserva para reintentar"
    assert str(original) not in watcher._processed, \
        "el archivo no debe marcarse como procesado para que el próximo ciclo reintente"
    assert not any(len(a) > 1 and a[1] == "slim_replaced" for (a, kw) in file_events), \
        "sin subida no hay reemplazo que refrescar"


def test_watcher_normal_no_emite_slim_replaced(tmp_path):
    """Una subida normal (sin pendiente) no debe disparar el refresco."""
    original = _ligera(tmp_path)
    watcher, ftp, upload_calls, delete_calls, events, file_events = _make_watcher()
    # Sin pending registrado y carpeta remota vacía: sube normal.
    watcher._process(original)

    assert len(upload_calls) == 1
    assert not any(len(a) > 1 and a[1] == "slim_replaced" for (a, kw) in file_events)
