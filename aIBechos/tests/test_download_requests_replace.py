"""Sustituciones pedidas desde la web (flag "replace", ver
core/download_requests.py): el worker del escritorio no debe darlas por
hechas al ver que ya están en el servidor (estaban antes de pedirlas);
solo cuenta una subida posterior al lanzamiento."""

import time
from types import MethodType, SimpleNamespace

import core.download_requests as dr
from core.app_download_requests import DownloadRequestsMixin as App


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
    # Progreso: métodos reales del mixin (sin aMule no publican nada).
    # MethodType porque van colgados de un SimpleNamespace, no de una
    # instancia del mixin (si no, no les llegaría el self).
    app._download_request_up_key = App._download_request_up_key
    app._download_request_dl_match = App._download_request_dl_match
    app._download_requests_publish_progress = MethodType(
        App._download_requests_publish_progress, app)
    app._download_request_cancel_amule = lambda entry, data=None: True
    app._download_requests_sweep_ts = 9999999999.0   # barrido apagado salvo test propio

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
    pendiente (y se relanza tras la espera de reintento, ver
    retry_wait); si aMule no responde, no se toca."""
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
    assert pushed[-1]["obs"]["status"] == "pending", "liberada"
    assert pushed[-1]["obs"]["attempts"] == 1
    assert launched == [], "no se relanza al momento: espera su retry_wait"
    # Pasada la espera, sí se relanza.
    data = {"obs": dict(pushed[-1]["obs"], updated_at=now - dr.retry_wait(pushed[-1]["obs"]) - 1)}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    App._download_requests_cycle(app)
    assert "obs" in launched
    # aMule sin responder: no se decide nada
    data = {"obs": dict(data["obs"], status="downloading", claimed_by="Jose",
                        claimed_at=now - 3 * 86400, attempts=0)}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
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
    app._download_request_cancel_amule = lambda entry, data=None: False   # aMule caído
    App._download_requests_cycle(app)
    assert launched == [] and not pushed
    app._download_request_cancel_amule = lambda entry, data=None: cancelled.append(entry["amule_hashes"]) or True
    App._download_requests_cycle(app)
    assert cancelled == [["aa"]] and launched == []
    assert pushed[-1]["c"]["status"] == "cancelled" and pushed[-1]["c"]["cancel_done"] is True


def test_carry_sobrevive_si_push_falla(monkeypatch):
    """El carry (cambios sin subir) solo se vacía tras un push OK: si el
    push final falla, el done sigue en carry para el siguiente ciclo."""
    import core.shared_data as sd
    now = time.time()
    data = {"r1": {"tmdb_id": 1, "media_type": "movie", "title": "P",
                   "status": "downloading", "claimed_by": "Jose",
                   "claimed_at": now - 60, "requested_at": now - 60, "attempts": 0}}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed = [], []
    app = _cycle_app(data, launched, pushed)
    app._download_request_check_done = lambda entry, idx=None: "done"
    app._download_requests_notify_web = lambda: None
    app._download_requests_push = lambda d, path: pushed.append(d) or False
    App._download_requests_cycle(app)
    carry = getattr(app, "_download_requests_carry", {})
    assert carry.get("r1", {}).get("status") == "done", "el done debe sobrevivir en carry"
    app._download_requests_push = lambda d, path: pushed.append(d) or True
    App._download_requests_cycle(app)
    assert getattr(app, "_download_requests_carry", None) == {}
    assert pushed[-1]["r1"]["status"] == "done"


def test_ciclo_avisa_a_la_web_al_fallar(monkeypatch):
    """Al agotar los intentos (failed) también se avisa a la web para
    que mande el push; no solo al completar."""
    import core.shared_data as sd
    now = time.time()
    data = {"r1": {"tmdb_id": 1, "media_type": "movie", "title": "P",
                   "status": "downloading", "claimed_by": "Jose",
                   "claimed_at": now - 3 * 86400, "requested_at": now - 3 * 86400,
                   "attempts": dr.MAX_ATTEMPTS - 1}}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed = [], []
    app = _cycle_app(data, launched, pushed)
    app._download_requests_amule_state = lambda needed: ([{"hash_hex": "x", "name": "Otra.mkv"}], [])
    avisos = []
    app._download_requests_notify_web = lambda: avisos.append(1)
    App._download_requests_cycle(app)
    assert pushed and pushed[-1]["r1"]["status"] == "failed"
    assert avisos == [1]


def test_ciclo_no_da_done_mientras_sube(monkeypatch):
    """El parcial en el servidor puede parecer 'ya está' (indexado a
    medias) mientras AutoWatcher lo sube: si hay subida en curso de lo
    pedido, el ciclo no marca done aunque check_done lo diga."""
    import core.shared_data as sd
    from core import upload_progress as up
    now = time.time()
    data = {"r1": {"tmdb_id": 1, "media_type": "movie", "title": "P",
                   "status": "downloading", "claimed_by": "Jose",
                   "claimed_at": now - 60, "requested_at": now - 60, "attempts": 0}}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed = [], []
    app = _cycle_app(data, launched, pushed)
    app._download_request_check_done = lambda entry, idx=None: "done"
    app._download_requests_notify_web = lambda: None
    up.report("movie", 1, None, None, 0.5)
    try:
        App._download_requests_cycle(app)
    finally:
        up.discard("movie", 1, None, None)
    assert pushed, "debe subir el up_percent aunque no haya done"
    assert pushed[-1]["r1"]["status"] == "downloading"
    assert pushed[-1]["r1"]["up_percent"] == 50


def test_publish_conserva_ultimo_porcentaje_sin_dato():
    """Sin cola de aMule ni subida en curso no se limpia el último %
    (la web lo sigue enseñando hasta que haya dato fresco)."""
    app = _cycle_app({}, [], [])
    data = {"r1": {"tmdb_id": 1, "media_type": "movie", "title": "P",
                   "status": "downloading", "claimed_by": "Jose",
                   "dl_percent": 63}}
    nd, changed = App._download_requests_publish_progress(app, data, ["r1"], [])
    assert not changed and nd["r1"]["dl_percent"] == 63


def test_ciclo_quita_de_amule_al_marcar_done(monkeypatch):
    """Al darse por hecha (llegó por otra vía), su descarga se quita de
    aMule en el mismo ciclo para que no termine días después (caso
    real: Andrómeda, completando en aMule lo ya subido a mano)."""
    import core.shared_data as sd
    now = time.time()
    data = {"r1": {"tmdb_id": 1, "media_type": "movie", "title": "P",
                   "status": "downloading", "claimed_by": "Jose",
                   "amule_hashes": ["aa"],
                   "claimed_at": now - 60, "requested_at": now - 60, "attempts": 0}}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed, quitadas = [], [], []
    app = _cycle_app(data, launched, pushed)
    app._download_request_check_done = lambda entry, idx=None: "done"
    app._download_requests_notify_web = lambda: None
    app._download_request_cancel_amule = lambda entry, data=None: quitadas.append(entry.get("amule_hashes")) or True
    App._download_requests_cycle(app)
    assert pushed and pushed[-1]["r1"]["status"] == "done"
    assert quitadas == [["aa"]]


def test_sweep_quita_huerfanas_finales(monkeypatch):
    """Barrido: solo hashes exactos de solicitudes finales que sigan en
    la cola; respeta el throttle horario."""
    import threading
    import core.app_download_requests as mod
    app = _cycle_app({}, [], [])
    app.config_data = {}
    app._amule_ec_lock = threading.Lock()
    app._download_requests_sweep_ts = 0.0
    cancelled = []

    class FakeEC:
        def __init__(self, **kw):
            pass

        def connect(self):
            pass

        def cancel_download(self, h):
            cancelled.append(h)
            return True, "ok"

        def close(self):
            pass

    monkeypatch.setattr(mod, "EcClient", FakeEC)
    data = {
        "done": {"status": "done", "amule_hashes": ["aa", "bb"]},
        "failed": {"status": "failed", "amule_hashes": ["cc"]},
        "down": {"status": "downloading", "claimed_by": "Jose", "amule_hashes": ["dd"]},
    }
    queue = [{"hash_hex": "aa"}, {"hash_hex": "cc"}, {"hash_hex": "dd"}, {"hash_hex": "zz"}]
    App._download_requests_sweep_finished(app, data, queue)
    assert sorted(cancelled) == ["aa", "cc"]
    # Segunda llamada seguida: throttle, sin más conexiones.
    cancelled.clear()
    App._download_requests_sweep_finished(app, data, queue)
    assert cancelled == []


def test_ciclo_fallida_reciente_que_ya_esta_pasa_a_done(monkeypatch):
    """Real: "Sin piedad" se dio por fallida a las 08:25 y se subió a las
    08:43; la web la siguió enseñando "Fallida"."""
    import core.shared_data as sd
    now = time.time()
    data = {"r1": {"tmdb_id": 1, "media_type": "movie", "title": "Sin piedad",
                   "status": "failed", "claimed_by": "Jose", "claimed_at": now - 7200,
                   "requested_at": now - 9000, "updated_at": now - 3600, "attempts": 5,
                   "last_error": "ya en completados de aMule (no se vuelve a bajar)"},
            "r2": {"tmdb_id": 2, "media_type": "movie", "title": "Otra",
                   "status": "failed", "claimed_by": "Efren", "claimed_at": now - 7200,
                   "requested_at": now - 9000, "updated_at": now - 3600, "attempts": 5}}
    monkeypatch.setattr(sd, "read_shared_json", lambda ftp, path, kind: (dict(data), False))
    launched, pushed = [], []
    app = _cycle_app(data, launched, pushed)
    app._download_requests_notify_web = lambda: None
    app._download_request_check_done = lambda entry, idx=None: "done"
    App._download_requests_cycle(app)
    assert pushed[-1]["r1"]["status"] == "done"
    assert pushed[-1]["r1"]["last_error"] == ""
    assert pushed[-1]["r2"]["status"] == "failed", "las de otro equipo las mira ese equipo"


def test_peli_con_fila_atrasada_mira_el_historial(monkeypatch):
    """La fila de Películas dice que no está (caché vieja) pero este PC la
    subió: done (real: "Sin piedad")."""
    app = _fake_app()
    app._movies_results = [{"tmdb_id": 1, "in_server": False}]
    monkeypatch.setattr(dr, "load_upload_history", lambda path=None: [])
    assert App._download_request_check_done(app, dict(MOVIE)) is None
    monkeypatch.setattr(dr, "load_upload_history",
                        lambda path=None: [{"status": "ok", "remote": REMOTE, "ts": 1000}])
    assert App._download_request_check_done(app, dict(MOVIE)) == "done"
