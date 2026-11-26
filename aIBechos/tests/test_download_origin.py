"""Lo que eligió la app al descargar se recuerda al subir (real:
"DragonBall Daima 1x10(Castellano).mp4", todo junto, no lo encontraba
TMDB aunque se había buscado Dragon Ball Daima 1x10)."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import amule_download as ad
from core import download_origin as do
from core.api_client import detect_episode
from core.auto_watcher import AutoWatcher

NAME = "DragonBall Daima 1x10(Castellano).mp4"


@pytest.fixture(autouse=True)
def _tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(do, "app_data_dir", lambda: tmp_path)


def test_apunta_y_encuentra_por_nombre():
    assert do.record(NAME, 240411, "tv", "Dragon Ball Daima", 1, 10)
    o = do.find(NAME.upper(), detect_episode(NAME))
    assert o["tmdb_id"] == 240411 and (o["season"], o["episode"]) == (1, 10)
    assert do.find("Otro archivo 1x10.mp4") is None


def test_no_se_fia_si_el_nombre_dice_otro_capitulo():
    do.record(NAME, 240411, "tv", "Dragon Ball Daima", 1, 10)
    assert do.find(NAME, {"season": 1, "episode": 11}) is None


def test_pack_de_temporada_toma_el_episodio_del_nombre():
    do.record("Serie T2 pack 2x05.mkv", 7, "tv", "Serie", 2, None)
    o = do.find("Serie T2 pack 2x05.mkv", {"season": 2, "episode": 5})
    assert (o["season"], o["episode"]) == (2, 5)
    assert do.find("Serie T2 pack 2x05.mkv", {}) is None


def test_sin_tmdb_no_se_apunta():
    assert not do.record(NAME, 0, "tv")
    assert not do.record(NAME, 5, "libro")


def test_caduca(monkeypatch):
    do.record(NAME, 240411, "tv", "Dragon Ball Daima", 1, 10)
    real = do.time.time
    monkeypatch.setattr(do.time, "time", lambda: real() + do.MAX_AGE_SECS + 1)
    assert do.find(NAME, {}) is None


class _FakeEc:
    def __init__(self, results):
        self.results = results

    def connect(self):
        pass

    def close(self):
        pass

    def iter_search(self, query, **k):
        yield list(self.results)

    def download(self, r):
        return True, ""


def test_auto_download_apunta_el_origen(monkeypatch):
    from core.amule_client import AmuleSearchResult
    best = AmuleSearchResult(0, NAME, "480.0 MB", 5)
    monkeypatch.setattr(ad, "ec_client_from_config", lambda cfg: _FakeEc([best]))
    monkeypatch.setattr(ad, "_already_completed", lambda ec, b: False)
    ok, why, _h, name = ad.auto_download({}, threading.Lock(), "Dragon Ball Daima 1x10",
                                         typical_size=480 * 1024 * 1024,
                                         origin={"tmdb_id": 240411, "media_type": "tv",
                                                 "title": "Dragon Ball Daima", "season": 1, "episode": 10})
    assert ok, why
    assert do.find(name, {})["tmdb_id"] == 240411


class _FakeTmdb:
    def get_tv_details(self, tid):
        return {"id": tid, "name": "Dragon Ball Daima", "first_air_date": "2024-10-11",
                "genres": [{"id": 16, "name": "Animación"}]}

    def get_episode_info(self, tid, s, e):
        return {"name": "Océano"}

    def build_media_info(self, result, season=None, episode=None):
        from core.api_client import TMDBClient
        return TMDBClient.build_media_info(self, result, season, episode)


def test_watcher_usa_la_ficha_apuntada():
    do.record(NAME, 240411, "tv", "Dragon Ball Daima", 1, 10)
    w = SimpleNamespace(tmdb=_FakeTmdb(), on_event=lambda *a, **k: None)
    info = AutoWatcher._media_info_from_origin(w, Path(NAME), detect_episode(NAME))
    assert (info.tmdb_id, info.title, info.season, info.episode) == (240411, "Dragon Ball Daima", 1, 10)
    assert info.year == "2024" and info.episode_title == "Océano" and 16 in info.genre_ids


def test_watcher_sin_origen_identifica_como_siempre():
    w = SimpleNamespace(tmdb=_FakeTmdb(), on_event=lambda *a, **k: None)
    assert AutoWatcher._media_info_from_origin(w, Path(NAME), detect_episode(NAME)) is None
