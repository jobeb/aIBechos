"""Juez IA de descargas (core/ai_title_fallback.judge_download_candidate y
core/amule_download.pick_with_judge): Groq fingido, sin red."""

import pytest

import core.ai_title_fallback as aif
from core.amule_download import pick_with_judge


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._payload}}]}


def _fake_post(content):
    return lambda *a, **k: _Resp(content)


def _cand(name, size="1.4 GB", sources=10):
    from types import SimpleNamespace
    return SimpleNamespace(name=name, size_human=size, sources=sources)


WANTED = {"title": "Serie 1x02", "year": "", "is_movie": False, "expected_size": "350 MB"}


def test_sin_key_no_consulta(monkeypatch):
    called = []
    monkeypatch.setattr(aif.requests, "post", lambda *a, **k: called.append(1) or _Resp("{}"))
    assert aif.judge_download_candidate(WANTED, {}, "") is None
    assert not called


def test_veredicto_ok_y_malo(monkeypatch, tmp_path):
    import core.applog as applog
    monkeypatch.setattr(applog, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(applog, "_handlers", {})
    monkeypatch.setattr(aif.requests, "post",
                        _fake_post('{"verdict": "ok", "reason": "coincide"}'))
    res = aif.judge_download_candidate(WANTED, {"name": "Serie 1x02 castellano", "size_human": "350 MB",
                                                "sources": 5}, "K")
    assert res == {"verdict": "ok", "reason": "coincide"}
    monkeypatch.setattr(aif.requests, "post",
                        _fake_post('{"verdict": "malo", "reason": "es porno"}'))
    res = aif.judge_download_candidate(WANTED, {"name": "Amateur Sex", "size_human": "108 MB",
                                                "sources": 5}, "K")
    assert res["verdict"] == "malo"


def test_fallo_red_y_basura_es_none(monkeypatch):
    def _raise(*a, **k):
        raise ConnectionError("caído")
    monkeypatch.setattr(aif.requests, "post", _raise)
    assert aif.judge_download_candidate(WANTED, {"name": "x"}, "K") is None
    monkeypatch.setattr(aif.requests, "post", _fake_post("no es json"))
    assert aif.judge_download_candidate(WANTED, {"name": "x"}, "K") is None
    monkeypatch.setattr(aif.requests, "post", _fake_post('{"verdict": "ni idea"}'))
    assert aif.judge_download_candidate(WANTED, {"name": "x"}, "K") is None


def test_pick_with_judge():
    a, b, c = _cand("Serie 1x02 castellano"), _cand("Serie 1x02 latino"), _cand("Serie 1x03 castellano")
    calls = []

    def judge(c):
        calls.append(c.name)
        if "latino" in c.name:
            return {"verdict": "malo", "reason": "latino, se pide castellano"}
        return {"verdict": "ok", "reason": "bien"}
    chosen, notes = pick_with_judge([a, b, c], judge)
    assert chosen is a and calls == ["Serie 1x02 castellano"]
    chosen, notes = pick_with_judge([b, c], judge)
    assert chosen is c and notes == ["latino, se pide castellano"]
    chosen, notes = pick_with_judge([b], judge)
    assert chosen is None and notes == ["latino, se pide castellano"]


def test_pick_con_ia_caida_sigue_como_antes():
    a = _cand("lo que sea")
    chosen, notes = pick_with_judge([a], lambda _c: None)
    assert chosen is a and notes == []
    chosen, notes = pick_with_judge([a], lambda _c: {"verdict": "dudoso", "reason": "raro el tamaño"})
    assert chosen is a and notes == ["dudoso: raro el tamaño"]


def test_judge_or_keep_llega_a_consultar_al_juez(monkeypatch):
    """Regresión: _judge_or_keep ordenaba con score_download(..., max_size)
    (6 argumentos, la función acepta 5); el TypeError se capturaba y el juez
    no se ejecutaba NUNCA -- se descargaba italiano/1999 sin control."""
    from core import amule_download as ad
    from core.amule_client import AmuleSearchResult as R
    vistos = []

    def _judge(wanted, cand, key, model=None):
        vistos.append((wanted, cand["name"]))
        return {"verdict": "malo", "reason": "italiano"}
    monkeypatch.setattr(aif, "judge_download_candidate", _judge)
    cfg = {"ai_fallback_enabled": True, "ai_api_key": "k"}
    r = R(1, "Hunter x Hunter - 02x01 - Offerta.mkv", "300 MB", 5, True)
    best, motivo = ad._judge_or_keep(cfg, "Hunter x Hunter 2x01", r, [r], False,
                                     2011, 300 * 2**20, 400 * 2**20)
    assert vistos and vistos[0][0]["year"] == 2011
    assert best is None and motivo.startswith("IA:")


def test_juez_al_vetar_no_recurre_a_lo_que_supera_el_techo_de_adelgazar(monkeypatch):
    """Caso real: Adelgazar «Noche en el museo 2» (11,1 GB, techo 85%). El
    juez vetó al mejor y bajó el 2160p de 11,1 GB -- el mismo peso que ya
    había en el servidor -- porque al vetar probaba con TODOS los
    resultados, sin el techo de tamaño ni los filtros de best_result."""
    from core import amule_download as ad
    from core.amule_client import AmuleSearchResult as R
    juzgados = []

    def _judge(wanted, cand, key, model=None):
        juzgados.append(cand["name"])
        return {"verdict": "malo" if "1080p" in cand["name"] else "ok", "reason": "x"}
    monkeypatch.setattr(aif, "judge_download_candidate", _judge)
    cfg = {"ai_fallback_enabled": True, "ai_api_key": "k"}
    ligera = R(1, "Noche en el museo 2 (2009) BDRip 1080p Castellano.mkv", "4.0 GB", 9, True)
    gorda = R(2, "Noche en el museo 2 (2009) WEB-DL 2160p DUAL Castellano.mkv", "11.1 GB", 30, True)
    italiana = R(3, "Una notte al museo 2 (2009) 720p ITA.mkv", "2.0 GB", 30, True)
    heavy = int(11.1 * 2**30)
    best, motivo = ad._judge_or_keep(cfg, "Noche en el museo 2", ligera, [ligera, gorda, italiana], True,
                                     2009, 2 * 2**30, int(heavy * 0.85))
    assert best is None and motivo.startswith("IA:"), (best, motivo)
    assert gorda.name not in juzgados and italiana.name not in juzgados
