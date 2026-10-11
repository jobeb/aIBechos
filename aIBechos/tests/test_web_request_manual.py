"""Botones de Solicitudes web: buscar a mano (query del lanzamiento) y
descarga automática inmediata (reclama + lanza sin esperar al ciclo)."""

from types import SimpleNamespace

import core.download_requests as dr
from core.app_download_requests import DownloadRequestsMixin as App


def _host(data, launched=None, pushed=None):
    app = SimpleNamespace(config_data={"series_search_patterns": {}})
    app._download_requests_user = lambda: "Jose"
    app._download_requests_remote_path = lambda: "/x/solicitudes.json"
    app._download_requests_ftp = lambda: SimpleNamespace(disconnect=lambda: None)
    app._download_requests_push = lambda d, path: pushed.append(dict(d)) or True
    app._series_prefers_castellano = lambda name: False

    def _launch(d, rid):
        launched.append(rid)
        return dr.mark_status(d, rid, "downloading", user="Jose"), True
    app._download_request_launch = _launch
    return app


def _req(status="pending", **kw):
    e = {"tmdb_id": 1, "media_type": "movie", "season": None, "episode": None,
         "title": "Peli", "year": "2024", "status": status, "claimed_by": "",
         "claimed_at": 0, "requested_at": 1.0, "attempts": 0}
    e.update(kw)
    return e


def test_now_pending_reclama_y_lanza(monkeypatch):
    import core.shared_data as sd
    data = {"r1": _req("pending")}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed = [], []
    msg = App._download_now_request(_host(data, launched, pushed), "r1")
    assert launched == ["r1"]
    assert msg.startswith("Lanzada:")
    final = pushed[-1]["r1"]
    assert final["status"] == "downloading" and final["claimed_by"] == "Jose"


def test_now_failed_resetea_y_lanza(monkeypatch):
    import core.shared_data as sd
    data = {"r1": _req("failed", attempts=5, last_error="sin candidato")}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed = [], []
    msg = App._download_now_request(_host(data, launched, pushed), "r1")
    assert launched == ["r1"] and msg.startswith("Lanzada:")
    final = pushed[-1]["r1"]
    assert final["status"] == "downloading" and final["attempts"] == 0


def test_now_de_otro_equipo_no_toca(monkeypatch):
    import core.shared_data as sd
    data = {"r1": _req("claimed", claimed_by="Otro")}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed = [], []
    msg = App._download_now_request(_host(data, launched, pushed), "r1")
    assert launched == [] and pushed == []
    assert "marcha" in msg


def test_now_sin_cola_o_sin_ftp(monkeypatch):
    import core.shared_data as sd
    app = _host({}, [], [])
    app._download_requests_remote_path = lambda: ""
    assert "carpeta" in App._download_now_request(app, "r1")
    app._download_requests_remote_path = lambda: "/x/solicitudes.json"
    app._download_requests_ftp = lambda: None
    assert "conectar" in App._download_now_request(app, "r1")
    app._download_requests_ftp = lambda: SimpleNamespace(disconnect=lambda: None)
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: None)
    assert "leer" in App._download_now_request(app, "r1")


def test_search_query_igual_que_lanzamiento():
    app = SimpleNamespace(config_data={"series_search_patterns": {}})
    app._download_request_canonical = lambda entry: ("Serie", "2024")
    app._series_prefers_castellano = lambda name: False
    app._download_request_targets = lambda entry, name: [("episode", 1, 2)]
    query, year, is_movie = App._web_request_search_query(
        app, {"media_type": "tv", "title": "Serie", "year": "2024"})
    assert "Serie" in query and "1x02" in query.replace("1x2", "1x02")
    assert year is None and is_movie is False


def test_search_query_peli_con_ano():
    app = SimpleNamespace(config_data={"series_search_patterns": {}})
    app._download_request_canonical = lambda entry: ("Peli", "2024")
    app._series_prefers_castellano = lambda name: False
    app._download_request_targets = lambda entry, name: [("movie",)]
    query, year, is_movie = App._web_request_search_query(
        app, {"media_type": "movie", "title": "Peli", "year": "2024"})
    assert "Peli" in query and year == 2024 and is_movie is True


def test_search_query_con_fila_de_tabla():
    # Regresión: la fila trae "name" (y media/season/episode), no "title";
    # sin esto salía "Sin título que buscar" y no se abría Descargas.
    import core.download_requests as dr
    app = SimpleNamespace(config_data={"series_search_patterns": {}})
    app._download_request_canonical = lambda entry: (entry.get("title") or "", "")
    app._series_prefers_castellano = lambda name: False
    app._download_request_targets = lambda entry, name: [("episode", 1, 2)]
    row = dr.history_rows({"r1": {"title": "Serie", "media_type": "tv", "season": 1,
                                  "episode": 2, "requested_by": "Jose",
                                  "requested_at": 1.0, "status": "pending"}})[0]
    assert "title" in row and row["season"] == 1
    query, year, is_movie = App._web_request_search_query(app, dict(row))
    assert "Serie" in query and "1x02" in query.replace("1x2", "1x02")
    assert is_movie is False
    # Y si el canónico viene vacío, tira del "name" de la fila.
    app._download_request_canonical = lambda entry: ("", "")
    query2, _, _ = App._web_request_search_query(app, dict(row))
    assert query2 != ""


def test_launch_con_carrera_no_canta_exito():
    # Otro equipo reclamó entre medias: mark_status da None -> (None, False),
    # nunca (None, True) con descarga huérfana.
    app = SimpleNamespace(config_data={"series_search_patterns": {}})
    app._download_requests_user = lambda: "Jose"
    app._download_request_canonical = lambda entry: ("Peli", "2024")
    app._series_prefers_castellano = lambda name: False
    app._download_request_targets = lambda entry, name: [("movie",)]
    app._typical_size_for_series = lambda *a: None
    app._download_request_record_replacement = lambda *a: None
    app._auto_amule_download_series = lambda *a, **k: (True, "", "b" * 32)
    data = {"r1": _req("pending", claimed_by="Otro")}
    nd, ok = App._download_request_launch(app, data, "r1")
    assert (nd, ok) == (None, False)


def test_wait_dialog_con_timeout():
    import threading
    from core.app_files_core import FilesCoreMixin
    assert FilesCoreMixin._wait_dialog(threading.Event(), "x", timeout=0.05) is False
    ev = threading.Event()
    ev.set()
    assert FilesCoreMixin._wait_dialog(ev, "x", timeout=5) is True
