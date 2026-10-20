"""Lo que la web enseña de cada solicitud debe ser verdad:
- "ya en completados de aMule" no es un fallo (real: "Sin piedad" acabó
  "Fallida" y se subió 18 minutos después);
- una "failed" reciente que llega al servidor pasa a "done";
- al completarse se limpia el error de un intento anterior."""

from types import SimpleNamespace

from core import download_requests as dr
from core.amule_download import ALREADY_COMPLETED_REASON
from core.app_download_requests import DownloadRequestsMixin as App

H = "c" * 32


def _app(result):
    app = SimpleNamespace(config_data={"series_search_patterns": {}})
    app._download_requests_user = lambda: "Jose"
    app._download_request_canonical = lambda entry: ("Sin piedad", "2026")
    app._download_request_targets = lambda entry, name: [("movie",)]
    app._typical_size_for_series = lambda *a: None
    app._series_prefers_castellano = lambda *a: False
    app._download_request_record_replacement = lambda *a: None
    app._auto_amule_download_series = lambda *a, **k: result
    return app


def _entry(**kw):
    e = {"media_type": "movie", "title": "Sin piedad", "year": "2026",
         "status": "claimed", "claimed_by": "Jose", "claimed_at": 1, "attempts": 4}
    e.update(kw)
    return e


def test_ya_en_completados_queda_descargando_sin_gastar_intento():
    nd, ok = App._download_request_launch(
        _app((False, ALREADY_COMPLETED_REASON, H)), {"r1": _entry()}, "r1")
    assert ok is True
    assert nd["r1"]["status"] == "downloading"
    assert nd["r1"]["attempts"] == 4
    assert nd["r1"]["amule_hashes"] == [H]
    assert nd["r1"]["amule_completed"] is True


def test_lanzamiento_normal_no_marca_completado():
    nd, ok = App._download_request_launch(_app((True, "", H)), {"r1": _entry()}, "r1")
    assert ok is True
    assert nd["r1"]["amule_completed"] is False


def test_fallo_de_verdad_sigue_soltando():
    nd, ok = App._download_request_launch(
        _app((False, "no encontrado", "")), {"r1": _entry()}, "r1")
    assert ok is False
    assert nd["r1"]["status"] == "failed"
    assert nd["r1"]["last_error"] == "no encontrado"


def test_done_limpia_el_error_anterior():
    data = {"r1": _entry(status="downloading", last_error="descarga incompleta")}
    nd = dr.mark_status(data, "r1", "done", user="Jose")
    assert nd["r1"]["last_error"] == ""


def test_failed_to_recheck_solo_mias_y_recientes():
    now = 30 * 86400
    data = {
        "a": {"status": "failed", "claimed_by": "Jose", "updated_at": now - 3600},
        "b": {"status": "failed", "claimed_by": "Efren", "updated_at": now - 3600},
        "c": {"status": "failed", "claimed_by": "Jose", "updated_at": now - 20 * 86400},
        "d": {"status": "done", "claimed_by": "Jose", "updated_at": now - 3600},
    }
    assert dr.failed_to_recheck(data, "Jose", now=now) == ["a"]


def test_reintento_con_espera_creciente():
    now = 100000.0
    base = {"status": "pending", "claimed_by": "", "requested_at": 1}
    fresh = {**base, "attempts": 0, "updated_at": now}
    once = {**base, "attempts": 1, "updated_at": now - 10 * 60}
    once_old = {**base, "attempts": 1, "updated_at": now - 31 * 60}
    thrice = {**base, "attempts": 3, "updated_at": now - 90 * 60}
    data = {"a": fresh, "b": once, "c": once_old, "d": thrice}
    assert [rid for rid, _e in dr.pending_for_worker(data, now=now)] == ["a", "c"]
    assert dr.retry_wait(thrice) == 2 * 3600
