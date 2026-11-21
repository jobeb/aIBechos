"""Arreglos salidos de revisar los logs: tokens fuera de los logs, veredicto
de huecos en lotes (413 con 102 series) y juez IA sin repetir consultas."""
import json
import logging

import requests

import core.ai_title_fallback as aif
import core.missing_episodes_ai as mea
from core import applog


def test_redacta_credenciales():
    msg = ("Max retries exceeded with url: /library/sections?X-Plex-Token=wMv7pAoZ "
           "(Caused by X) /Items?api_key=abc123&Recursive=true monkey=ok")
    out = applog.redact(msg)
    assert "wMv7pAoZ" not in out and "abc123" not in out
    assert "X-Plex-Token=***" in out and "Recursive=true" in out and "monkey=ok" in out


def test_el_formatter_de_los_logs_redacta():
    fmt = applog._RedactingFormatter("%(message)s")
    rec = logging.LogRecord("x", logging.WARNING, "", 0, "fallo: %s", ("?X-Plex-Token=SECRETO",), None)
    assert "SECRETO" not in fmt.format(rec)


class _Resp:
    def __init__(self, status, content=None):
        self.status_code = status
        self._content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return {"choices": [{"message": {"content": self._content}}], "usage": {}}


def _shows(n):
    return [{"tmdb_id": i, "name": f"S{i}", "tmdb_seasons": {1: 10}, "server_seasons": {1: 8}}
            for i in range(n)]


def test_veredicto_de_huecos_en_lotes_y_partiendo_si_413(monkeypatch):
    sizes = []

    def _post(url, json=None, **kw):
        sent = __import__("json").loads(json["messages"][1]["content"])
        sizes.append(len(sent))
        if len(sent) > 10:                          # "demasiado grande"
            return _Resp(413)
        ver = [{"tmdb_id": s["tmdb_id"], "veredicto": "hueco_real", "motivo": ""} for s in sent]
        return _Resp(200, __import__("json").dumps({"veredictos": ver}))

    monkeypatch.setattr(mea.requests, "post", _post)
    res = mea.analyze_missing_episodes(_shows(45), api_key="k")
    assert sorted(res) == list(range(45))
    assert max(sizes) <= mea.BATCH_SIZE


def test_veredicto_de_huecos_un_lote_que_falla_no_tumba_los_demas(monkeypatch):
    calls = []

    def _post(url, json=None, **kw):
        sent = __import__("json").loads(json["messages"][1]["content"])
        calls.append(1)
        if len(calls) == 1:
            return _Resp(500)
        ver = [{"tmdb_id": s["tmdb_id"], "veredicto": "hueco_real"} for s in sent]
        return _Resp(200, __import__("json").dumps({"veredictos": ver}))

    monkeypatch.setattr(mea.requests, "post", _post)
    res = mea.analyze_missing_episodes(_shows(mea.BATCH_SIZE + 5), api_key="k")
    assert len(res) == 5


def test_juez_no_repite_la_consulta_del_mismo_candidato(monkeypatch):
    aif._judge_cache.clear()
    calls = []

    def _post(*a, **k):
        calls.append(1)
        return _Resp(200, json.dumps({"verdict": "malo", "reason": "italiano"}))

    monkeypatch.setattr(aif.requests, "post", _post)
    wanted = {"title": "Hunter × Hunter 1x25", "year": "2011"}
    for sources in (3, 7):                         # las fuentes cambian entre búsquedas
        r = aif.judge_download_candidate(wanted, {"name": "HxH 01x25 ITA.mkv", "size_human": "300 MB",
                                                  "sources": sources}, "K")
        assert r["verdict"] == "malo"
    assert len(calls) == 1
    aif.judge_download_candidate(wanted, {"name": "otro.mkv", "size_human": "300 MB"}, "K")
    assert len(calls) == 2
    aif._judge_cache.clear()


def test_confianza_tmdb_usa_tambien_el_titulo_original():
    from core.auto_watcher import tmdb_title_confidence
    res = {"title": "Una joven prometedora", "original_title": "Promising Young Woman"}
    assert tmdb_title_confidence("Promising Young Woman", res) == 100
    assert tmdb_title_confidence("Una joven prometedora", res) == 100
    assert tmdb_title_confidence("Otra cosa", {"name": "Fargo"}) < 50
