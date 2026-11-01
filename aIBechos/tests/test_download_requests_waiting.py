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


# ── Peli reciente sin fuentes en aMule: vuelve a presolicitada ──

def _agotada(year, media="movie", **kw):
    _, e = dr.new_request(7, media, "Hot Wheels", "Efren", year=year, now=100.0)
    e.update(status="claimed", claimed_by="Jose", attempts=dr.MAX_ATTEMPTS - 1, **kw)
    return {"r1": e}


def _now(year):
    import time
    return time.mktime((year, 11, 1, 12, 0, 0, 0, 0, -1))


def test_peli_reciente_no_encontrada_vuelve_a_esperar():
    now = _now(2026)
    out = dr.release(_agotada("2026"), "r1", user="Jose",
                     error="sin candidato que cumpla el umbral", now=now)
    e = out["r1"]
    assert e["status"] == "waiting" and e["waiting_reason"] == "sin_fuentes"
    assert e["attempts"] == 0 and e["claimed_by"] == ""
    assert e["retry_at"] == now + dr.SOURCES_RETRY_SECONDS
    assert e["waiting_since"] == now
    assert dr.pending_for_worker(out, now=now + 10 * 86400) == []


def test_sin_fuentes_conserva_desde_cuando_espera_y_se_rinde():
    now = _now(2026)
    first = now - 10 * 86400
    out = dr.release(_agotada("2026", waiting_since=first), "r1", user="Jose",
                     error="no encontrado", now=now)
    assert out["r1"]["status"] == "waiting" and out["r1"]["waiting_since"] == first
    viejo = now - dr.SOURCES_GIVE_UP_SECONDS - 1
    out = dr.release(_agotada("2026", waiting_since=viejo), "r1", user="Jose",
                     error="no encontrado", now=now)
    assert out["r1"]["status"] == "failed"


def test_otros_fallos_o_pelis_viejas_o_series_si_fallan():
    now = _now(2026)
    casos = [(_agotada("2026"), "descarga incompleta"),
             (_agotada("2026"), "aMule: syntax error"),
             (_agotada("2020"), "no encontrado"),
             (_agotada("2026", media="tv"), "no encontrado"),
             (_agotada("2026", replace=True), "no encontrado")]
    for data, err in casos:
        assert dr.release(data, "r1", user="Jose", error=err, now=now)["r1"]["status"] == "failed", err
    # el año anterior todavía cuenta como reciente
    assert dr.release(_agotada("2025"), "r1", user="Jose", error="no encontrado",
                      now=now)["r1"]["status"] == "waiting"
