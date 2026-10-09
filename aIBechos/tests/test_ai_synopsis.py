"""Sinopsis en castellano (core/ai_synopsis): caché y traducción con Groq
fingido, sin red."""

import json

import pytest

import core.ai_synopsis as syn


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _setup(monkeypatch, tmp_path):
    import core.applog as applog
    import core.appdirs as appdirs
    monkeypatch.setattr(applog, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(applog, "_handlers", {})
    monkeypatch.setattr(appdirs, "app_data_dir", lambda: tmp_path)
    # Releer el módulo ya importa appdirs por nombre: parchear el símbolo.
    monkeypatch.setattr(syn, "app_data_dir", lambda: tmp_path)


def test_cache_evita_repetir(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    (tmp_path / "ai_overview_cache.json").write_text(
        json.dumps({"movie:7": {"es": "Guardada"}}), encoding="utf-8")

    def _boom(*a, **k):
        raise AssertionError("con caché no hay red")
    monkeypatch.setattr(syn.requests, "get", _boom)
    monkeypatch.setattr(syn.requests, "post", _boom)
    assert syn.es_overview("movie", 7, "TMDB", "AI") == "Guardada"


def test_traduce_y_guarda(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(syn.requests, "get",
                        lambda *a, **k: _Resp({"overview": "A desert saga."}))
    monkeypatch.setattr(syn.requests, "post",
                        lambda *a, **k: _Resp({"choices": [{"message": {"content": '{"es": "Una saga del desierto."}'}}]}))
    assert syn.es_overview("movie", 9, "TMDB", "AI") == "Una saga del desierto."
    saved = json.loads((tmp_path / "ai_overview_cache.json").read_text(encoding="utf-8"))
    assert saved["movie:9"]["es"] == "Una saga del desierto."


def test_sin_nada_vacio(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert syn.es_overview("tv", 0, "TMDB", "AI") == ""
    assert syn.es_overview("libro", "x", "TMDB", "AI") == ""
    assert syn.es_overview("movie", 9, "", "AI") == ""
    assert syn.es_overview("movie", 9, "TMDB", "") == ""
    monkeypatch.setattr(syn.requests, "get", lambda *a, **k: _Resp({"overview": ""}))
    assert syn.es_overview("movie", 9, "TMDB", "AI") == ""
    assert syn.ai_key_if_enabled({"ai_fallback_enabled": True, "ai_api_key": "K"}) == "K"
    assert syn.ai_key_if_enabled({"ai_fallback_enabled": False, "ai_api_key": "K"}) == ""
