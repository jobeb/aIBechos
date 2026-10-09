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
