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
    app._download_requests_amule_state = lambda needed: (None, [])   # sin aMule
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


def test_ciclo_avisa_a_la_web_al_completar(monkeypatch):
    """Al marcar algo como done (y subir la cola) se avisa a la web para
    que mande la notificación push; sin done, no se avisa."""
    import core.shared_data as sd
    now = time.time()
    data = {"r1": {"tmdb_id": 1, "media_type": "movie", "title": "P",
                   "status": "downloading", "claimed_by": "Jose",
                   "claimed_at": now - 60, "requested_at": now - 60, "attempts": 0}}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed = [], []
    app = _cycle_app(data, launched, pushed)
    avisos = []
    app._download_requests_notify_web = lambda: avisos.append(1)
    app._download_request_check_done = lambda entry, idx=None: "done"
    App._download_requests_cycle(app)
    assert pushed and pushed[-1]["r1"]["status"] == "done"
    assert avisos == [1]
    avisos.clear()
    app._download_request_check_done = lambda entry, idx=None: None
    App._download_requests_cycle(app)
    assert avisos == [], "sin nada completado no se molesta a la web"


def test_url_web_es_configuracion_de_servidor():
    from core.server_config import SHARED_CONFIG_KEYS
    from config import DEFAULTS
    assert "solicitudes_web_url" in SHARED_CONFIG_KEYS
    assert DEFAULTS["solicitudes_web_url"] == "https://aibechos.fordema.es/"


def test_ciclo_libera_lo_que_ya_no_esta_en_amule(monkeypatch):
    """Una descarga propia que ya no está en aMule ni terminó vuelve a
    pendiente (y se relanza); si aMule no responde, no se toca."""
    import core.shared_data as sd
    now = time.time()
    data = {"obs": {"tmdb_id": 1339713, "media_type": "movie", "title": "Obsession", "year": "2026",
                    "status": "downloading", "claimed_by": "Jose",
                    "claimed_at": now - 3 * 86400, "requested_at": now - 3 * 86400, "attempts": 0}}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed = [], []
    app = _cycle_app(data, launched, pushed)
    app._download_requests_notify_web = lambda: None
    app._download_requests_amule_state = lambda needed: ([{"hash_hex": "x", "name": "Otra.mkv"}], [])
    App._download_requests_cycle(app)
    assert "obs" in launched, "liberada y relanzada en el mismo ciclo"
    # aMule sin responder: no se decide nada
    launched.clear(); pushed.clear()
    app._download_requests_amule_state = lambda needed: (None, [])
    App._download_requests_cycle(app)
    assert launched == [] and (not pushed or pushed[-1]["obs"]["status"] == "downloading")


def test_ciclo_quita_de_amule_lo_cancelado_en_la_web(monkeypatch):
    """Cancelada desde la web con este PC descargándola: se quita de
    aMule y se marca cancel_done (sin relanzar nada); si aMule no
    responde, se reintenta en el siguiente ciclo."""
    import core.shared_data as sd
    now = time.time()
    data = {"c": {"tmdb_id": 5, "media_type": "movie", "title": "Peli", "status": "cancelled",
                  "cancelled_by": "Efren", "claimed_by": "Jose", "cancel_done": False,
                  "amule_hashes": ["aa"], "claimed_at": now, "requested_at": now, "updated_at": now,
                  "attempts": 0}}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed, cancelled = [], [], []
    app = _cycle_app(data, launched, pushed)
    app._download_request_cancel_amule = lambda entry: False   # aMule caído
    App._download_requests_cycle(app)
    assert launched == [] and not pushed
    app._download_request_cancel_amule = lambda entry: cancelled.append(entry["amule_hashes"]) or True
    App._download_requests_cycle(app)
    assert cancelled == [["aa"]] and launched == []
    assert pushed[-1]["c"]["status"] == "cancelled" and pushed[-1]["c"]["cancel_done"] is True
