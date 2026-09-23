"""Pruebas del fallback de IA (Groq) en AutoWatcher._try_ai_fallback()."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import core.auto_watcher as autowatcher_mod
import core.learned_terms as learned_terms_mod
from core.auto_watcher import AutoWatcher


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(autowatcher_mod, "_processed_db_path", lambda: tmp_path / "processed.json")
    monkeypatch.setattr(learned_terms_mod, "app_data_dir", lambda: tmp_path)
    learned_terms_mod._reset_cache_for_tests()


class _FakeConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _make_watcher(tmp_path, ai_enabled=True, ai_key="fake-key"):
    config = _FakeConfig({
        "poll_interval": 10,
        "ai_fallback_enabled": ai_enabled,
        "ai_api_key": ai_key,
    })
    tmdb = MagicMock()
    ftp = MagicMock()
    watcher = AutoWatcher(str(tmp_path), config, tmdb, ftp, on_event=lambda *a: None)
    return watcher, tmdb


def test_returns_none_when_disabled(tmp_path, monkeypatch):
    watcher, tmdb = _make_watcher(tmp_path, ai_enabled=False)
    called = []
    monkeypatch.setattr("core.ai_title_fallback.guess_title_via_ai",
                         lambda *a, **kw: called.append(1))
    result = watcher._try_ai_fallback(Path("Algo.WEBDL.mkv"), {"title": "Algo"})
    assert result is None
    assert not called


def test_returns_none_without_api_key(tmp_path):
    watcher, tmdb = _make_watcher(tmp_path, ai_enabled=True, ai_key="")
    result = watcher._try_ai_fallback(Path("Algo.WEBDL.mkv"), {"title": "Algo"})
    assert result is None


def test_successful_fallback_learns_terms_and_returns_results(tmp_path, monkeypatch):
    watcher, tmdb = _make_watcher(tmp_path)
    monkeypatch.setattr(
        "core.ai_title_fallback.guess_title_via_ai",
        lambda stem, api_key, **kw: {"title": "Cazadores De Sombras", "junk_tokens": ["DSNYP"]},
    )
    tmdb.search_tv.return_value = [{"id": 1, "name": "Cazadores De Sombras"}]

    result = watcher._try_ai_fallback(
        Path("Cazadores De Sombras S01E01 DSNYP HEVC.mkv"), {"title": "", "media_type": "tv"})

    assert result is not None
    results, detected = result
    assert results == [{"id": 1, "name": "Cazadores De Sombras", "media_type": "tv"}]
    assert detected["title"] == "Cazadores De Sombras"
    # El termino nuevo queda aprendido para la proxima vez
    assert "DSNYP" in learned_terms_mod.load_learned_terms()


def test_does_not_learn_terms_when_retry_still_finds_nothing(tmp_path, monkeypatch):
    watcher, tmdb = _make_watcher(tmp_path)
    monkeypatch.setattr(
        "core.ai_title_fallback.guess_title_via_ai",
        lambda stem, api_key, **kw: {"title": "Titulo Inventado", "junk_tokens": ["RUIDO"]},
    )
    tmdb.search_tv.return_value = []
    tmdb.search_multi.return_value = []

    result = watcher._try_ai_fallback(Path("Algo.RUIDO.mkv"), {"title": "", "media_type": "tv"})

    assert result is None
    assert learned_terms_mod.load_learned_terms() == []


def test_does_not_learn_terms_when_ai_call_itself_fails(tmp_path, monkeypatch):
    watcher, tmdb = _make_watcher(tmp_path)
    monkeypatch.setattr("core.ai_title_fallback.guess_title_via_ai", lambda *a, **kw: None)

    result = watcher._try_ai_fallback(Path("Algo.mkv"), {"title": "", "media_type": "tv"})

    assert result is None
    assert learned_terms_mod.load_learned_terms() == []
