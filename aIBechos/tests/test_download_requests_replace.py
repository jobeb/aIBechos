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


def _cycle_app(data, launched, pushed):
    app = SimpleNamespace(config_data={"download_requests_enabled": True,
                                       "download_requests_max_active": 5},
                          _download_requests_carry={})
    app._download_requests_remote_path = lambda: "/x/solicitudes.json"
    app._download_requests_user = lambda: "Jose"
    app._download_requests_ftp = lambda: SimpleNamespace(disconnect=lambda: None)
    app._download_requests_server_index = lambda wanted: {}
    app._download_request_check_done = lambda entry, idx=None: None

    def _launch(d, rid):
        launched.append(rid)
        return dr.mark_status(d, rid, "downloading", user="Jose"), True
    app._download_request_launch = _launch
    app._download_requests_push = lambda d, path: pushed.append(d) or True
    return app


def test_ciclo_descargas_viejas_no_bloquean_la_cola(monkeypatch):
    """Caso real: 5 descargas propias sin fuentes desde hace horas
    ocupaban los 5 huecos y nada nuevo se lanzaba (Unabomber en espera)."""
    import core.shared_data as sd
    old = time.time() - 5 * 3600
    data = {f"viejo{i}": {"tmdb_id": i, "media_type": "tv", "season": 1,
                          "episode": i, "title": "Serie", "status": "downloading",
                          "claimed_by": "Jose", "claimed_at": old,
                          "requested_at": old, "attempts": 0}
            for i in range(5)}
    data["nuevo"] = {"tmdb_id": 99, "media_type": "movie", "title": "Unabomber",
                     "status": "pending", "claimed_by": "", "claimed_at": 0,
                     "requested_at": time.time(), "attempts": 0}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed = [], []
    App._download_requests_cycle(_cycle_app(data, launched, pushed))
    assert launched == ["nuevo"], "lo nuevo se lanza; lo viejo sigue en aMule sin relanzarse"
    final = pushed[-1]
    assert all(final[f"viejo{i}"]["status"] == "downloading" for i in range(5))
    assert all(final[f"viejo{i}"]["attempts"] == 0 for i in range(5))


def test_ciclo_respeta_tope_con_descargas_recientes(monkeypatch):
    import core.shared_data as sd
    now = time.time()
    data = {f"r{i}": {"tmdb_id": i, "media_type": "movie", "title": "P",
                      "status": "downloading", "claimed_by": "Jose",
                      "claimed_at": now - 60, "requested_at": now - 60, "attempts": 0}
            for i in range(5)}
    data["nuevo"] = {"tmdb_id": 99, "media_type": "movie", "title": "U",
                     "status": "pending", "claimed_by": "", "claimed_at": 0,
                     "requested_at": now, "attempts": 0}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed = [], []
    App._download_requests_cycle(_cycle_app(data, launched, pushed))
    assert launched == [], "con 5 recientes el tope sigue mandando"
