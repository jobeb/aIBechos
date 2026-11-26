"""⚡: capítulos sin datos de doblaje se intentan, pero solo con un
candidato que declare castellano; los que se sabe que no están doblados
no se bajan (real: Blade Runner 2099, sin datos de doblaje, no bajaba
nada; Daima 1x11/1x14 se bajaron en japonés)."""

import threading

from core import app_auto_complete as acm
from core.missing_episodes import ep_dub_status


def test_ep_dub_status():
    assert ep_dub_status(1, 3, {}, {1: 5}) is True
    assert ep_dub_status(1, 6, {}, {1: 5}) is False
    assert ep_dub_status(1, 6, {"1x06": True}, None) is True
    assert ep_dub_status(1, 6, {"1x06": False}, None) is False
    assert ep_dub_status(1, 6, {}, None) is None
    assert ep_dub_status(2, 1, {}, {1: 5}) is None


class _Host(acm.AutoCompleteMixin):
    def __init__(self, missing, dub_eps=None, cutoff=None, cfg=None):
        self.config_data = dict(cfg or {})
        self._amule_ec_lock = threading.Lock()
        self._spanish_dub_cache = {"7": {"episodes": dub_eps or {}}}
        self._cutoff = cutoff
        self._missing = missing
        self._missing_ep_results = [{"tmdb_id": 7, "name": "Serie", "source": "jellyfin", "server_id": "x"}]
        self.launched = []
        self.files = []

    def __getattr__(self, name):   # resto de ganchos: no-op
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None

    def _rescan_single_series_worker(self, row):
        return [{"name": "Serie", "missing": self._missing}], []

    def _missing_ep_known_year(self, r):
        return None

    def _auto_checked_map(self):
        return {}

    def _auto_retry_map(self):
        return {}

    def _auto_complete_series(self):
        return {"7"}

    def _auto_requested_episodes(self, tmdb_id):
        return set()

    def _dub_cutoff_for_series(self, tmdb_id, ai):
        return self._cutoff

    def _set_auto_checked_episode(self, *a):
        pass

    def _clear_auto_retry_episode(self, *a):
        pass

    def _set_auto_retry_episode(self, *a):
        pass

    def _typical_size_for_series(self, *a):
        return None

    def _auto_amule_download_series(self, query, **kw):
        self.launched.append((query, kw.get("require_castellano")))
        return True, "", ""


class _NoEc:
    def __init__(self, *a, **k):
        pass

    def connect(self):
        raise OSError("sin aMule en tests")


def _run(host, monkeypatch):
    monkeypatch.setattr(acm, "EcClient", _NoEc)
    host._auto_complete_series_single_impl(7, force=True)
    return {q[-4:]: rc for q, rc in host.launched}


def test_sin_datos_solo_castellano_y_sin_doblar_fuera(monkeypatch):
    host = _Host({1: [1, 2, 3]}, dub_eps={"1x01": True, "1x02": False})
    assert _run(host, monkeypatch) == {"1x01": False, "1x03": True}


def test_descargar_sin_doblaje_baja_todo_sin_exigir(monkeypatch):
    host = _Host({1: [1, 2, 3]}, dub_eps={"1x02": False}, cfg={"download_undubbed": True})
    assert _run(host, monkeypatch) == {"1x01": False, "1x02": False, "1x03": False}


def test_corte_de_doblaje_manda(monkeypatch):
    host = _Host({1: [4, 6], 2: [1]}, cutoff={1: 5})
    assert _run(host, monkeypatch) == {"1x04": False, "2x01": True}
