"""Presolicitudes ("waiting"): pelis aún no disponibles que la web pasa a
"pending" cuando ya se pueden conseguir. El escritorio no las reclama,
solo pide a la web que las revise (con tope de frecuencia)."""

from types import SimpleNamespace

from core import download_requests as dr
from core.app_download_requests import DownloadRequestsMixin as App


def _waiting(**kw):
    _, e = dr.new_request(5, "movie", "Peli", "Ana", now=100.0)
    e.update(status="waiting", **kw)
    return e


def test_ningun_equipo_reclama_una_presolicitada():
    data = {"r1": _waiting()}
    assert dr.pending_for_worker(data, now=10_000.0) == []
    assert dr.claim(data, "r1", "Jose") is None
    assert dr.has_waiting(data)
    assert not dr.has_waiting({"r2": {**_waiting(), "status": "pending"}})


def test_presolicitada_es_activa_y_lanzable():
    assert "waiting" in dr.ACTIVE_STATUSES and "waiting" in dr.LAUNCHABLE_STATUSES
    assert dr.STATUS_ES["waiting"] == "Presolicitada"
    assert dr.mark_status({"r1": _waiting()}, "r1", "cancelled", user="Jose") is not None


def test_aviso_a_la_web_con_tope():
    pings = []
    host = SimpleNamespace(_download_requests_waiting_ts=0.0,
                           _WAITING_CHECK_EVERY=App._WAITING_CHECK_EVERY,
                           _download_requests_ping_web=pings.append)
    App._download_requests_waiting_check_web(host)
    App._download_requests_waiting_check_web(host)   # dentro del tope: nada
    assert pings == ["waiting_check"]
    host._download_requests_waiting_ts -= App._WAITING_CHECK_EVERY + 1
    App._download_requests_waiting_check_web(host)
    assert pings == ["waiting_check", "waiting_check"]
