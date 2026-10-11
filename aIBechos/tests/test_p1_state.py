"""Limpiezas P1: estado auto por serie al completar, huella de servidor en
típicos, purga de dl_state en Episodios y TTL del fallback de huecos."""

from types import SimpleNamespace

import pytest


class _Cfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        pass


def test_clear_auto_series_state():
    from core.app_auto_complete import AutoCompleteMixin

    class _Host(AutoCompleteMixin):
        def __init__(self, cfg):
            self.config_data = cfg

    host = _Host(_Cfg({
        "missing_ep_auto_checked": {"7": {"1": {"2": {}}}},
        "missing_ep_auto_retries": {"7": {"1": {"2": {"tries": 3}}}, "9": {}},
        "missing_ep_auto_downloads": {"7": {"1": {"2": {}}}},
    }))
    host._clear_auto_series_state(7)
    cfg = host.config_data
    assert "7" not in cfg["missing_ep_auto_checked"]
    assert "7" not in cfg["missing_ep_auto_retries"]
    assert "7" not in cfg["missing_ep_auto_downloads"]
    assert cfg["missing_ep_auto_retries"] == {"9": {}}


def test_typical_cache_otro_servidor_recalcula():
    import time
    from core import amule_download as ad
    cache = {}
    cfg_a = {"ftp_host": "a", "ftp_port": 21, "ftp_categories": {"tv": [{"root": "/s"}]}}
    cfg_b = {"ftp_host": "b", "ftp_port": 21, "ftp_categories": {"tv": [{"root": "/s"}]}}
    cache[("serie", 1)] = (time.time(), 999, ad._typical_fingerprint(cfg_a))
    # Mismo servidor: tira de caché sin red.
    assert ad.typical_size_for_series(cfg_a, "serie", 1, cache) == 999
    # Otro servidor: no vale, reintenta (sin FTP -> None, pero NO el 999 viejo).
    assert ad.typical_size_for_series(cfg_b, "serie", 1, cache) is None


def test_missing_prune_dl_state():
    from gui_qt.missing_episodes.model import MissingEpisodesModel
    ctx = SimpleNamespace(config={"tv_template": "x"})
    m = MissingEpisodesModel(ctx)
    m.dl_state = {(7, 1): "x", (7, 1, 2): "x", (9, 1): "x", ("otro",): "x"}
    rows = [{"tmdb_id": 7, "missing": {1: [2, 3]}}]
    m.set_rows(rows, {})
    assert (7, 1) in m.dl_state and (7, 1, 2) in m.dl_state
    assert (9, 1) not in m.dl_state and ("otro",) not in m.dl_state


def test_fallback_missing_con_ttl(monkeypatch):
    import time
    from gui_qt.core_host import QtAppCore
    import core.missing_episodes_cache as mec
    calls = []
    monkeypatch.setattr(mec, "load_cache", lambda: calls.append(1) or {"_meta": {}})
    import core.missing_ep_rows as mer
    monkeypatch.setattr(mer, "rows_from_cache", lambda cache, complete=False: ["FOTO"])
    host = SimpleNamespace(missing_view=SimpleNamespace(_results=None),
                           _fallback_missing_rows=None, _fallback_missing_ts=0.0)
    get = QtAppCore._missing_ep_results.fget
    assert get(host) == ["FOTO"] and len(calls) == 1
    assert get(host) == ["FOTO"] and len(calls) == 1  # dentro del TTL: sin releer
    host._fallback_missing_ts = time.time() - 301
    assert get(host) == ["FOTO"] and len(calls) == 2  # caducado: relee
