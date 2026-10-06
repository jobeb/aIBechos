"""Sustituciones pedidas desde la web (flag "replace", ver
core/download_requests.py): el worker del escritorio no debe darlas por
hechas al ver que ya están en el servidor (estaban antes de pedirlas);
solo cuenta una subida posterior al lanzamiento."""

import time
from types import SimpleNamespace

import core.download_requests as dr
from gui.app import App


def _fake_app():
    app = SimpleNamespace(
        # Todo "ya en el servidor": una solicitud normal sería done al instante.
        _movies_results=[{"tmdb_id": 1, "in_server": True}],
        _missing_ep_results=[],
    )
    app._download_request_check_done_stuck = App._download_request_check_done_stuck
    return app


MOVIE = {"tmdb_id": 1, "media_type": "movie", "season": None, "episode": None,
         "title": "Hit Man. Asesino por casualidad", "year": "2024",
         "status": "downloading", "claimed_at": time.time()}
REMOTE = "/datos/peliculas//Hit Man. Asesino por casualidad (2024).mkv"


def test_normal_presente_es_done():
    assert App._download_request_check_done(_fake_app(), dict(MOVIE)) == "done"


def test_sustitucion_sin_lanzar_se_relanza():
    entry = dict(MOVIE, replace=True)
    assert App._download_request_check_done(_fake_app(), entry) == "relaunch"


def test_sustitucion_solo_done_con_subida_nueva(monkeypatch):
    entry = dict(MOVIE, replace=True, replace_since=2000.0)
    hist = [{"status": "ok", "remote": REMOTE, "ts": 1000}]
    monkeypatch.setattr(dr, "load_upload_history", lambda path=None: list(hist))
    assert App._download_request_check_done(_fake_app(), entry) is None
    hist.append({"status": "ok", "remote": REMOTE, "ts": 2500})
    assert App._download_request_check_done(_fake_app(), entry) == "done"
