"""Sustituir no debe descargar el mismo archivo: al lanzar un reemplazo se
excluyen los hashes del archivo original (guardados al lanzarlo) para que
aMule elija otro distinto, nunca idéntico."""

from types import SimpleNamespace

from core.amule_client import AmuleSearchResult as R
from core.amule_download import _best_not_excluded
from core.app_download_requests import DownloadRequestsMixin as App


def _r(name, h, sources=10):
    r = R(number=1, name=name, size_human="1.4 GB", sources=sources, complete=True)
    r._ec_hash = bytes.fromhex(h)
    return r


OLD = "a" * 32
NEW = "b" * 32


def test_excluye_el_original_y_coge_el_siguiente():
    old = _r("Peli 2024 castellano 1080p.mkv", OLD, sources=50)
    new = _r("Peli 2024 castellano 720p.mkv", NEW, sources=5)
    best = _best_not_excluded([old, new], "Peli", "2024", True, None, None, {OLD})
    assert best is new


def test_todo_excluido_es_none():
    old = _r("Peli 2024 castellano.mkv", OLD)
    assert _best_not_excluded([old], "Peli", "2024", True, None, None, {OLD}) is None
    assert _best_not_excluded([], "Peli", "2024", True, None, None, {OLD}) is None


def test_sin_hash_no_se_puede_excluir():
    from core.download_quality import best_result
    r = R(number=1, name="Peli 2024 castellano 1080p.mkv", size_human="1.4 GB", sources=50,
          complete=True)
    # Sin hash no hay con qué comparar: mismo resultado que sin excluir.
    assert _best_not_excluded([r], "Peli", "2024", True, None, None, {OLD}) == \
        best_result([r], "Peli", "2024", True, None, None)


def test_launch_pasa_hashes_originales(monkeypatch):
    seen = {}

    def fake_launch(query, is_movie=False, expected_year=None, typical_size=None,
                    exclude_hashes=()):
        seen.update(query=query, exclude=tuple(exclude_hashes))
        return True, "", NEW
    app = SimpleNamespace(config_data={"series_search_patterns": {}})
    app._download_requests_user = lambda: "Jose"
    app._download_request_canonical = lambda entry: ("Peli", "2024")
    app._download_request_targets = lambda entry, name: [("movie",)]
    app._typical_size_for_series = lambda *a: None
    app._series_prefers_castellano = lambda *a: False
    app._download_request_record_replacement = lambda *a: None
    app._auto_amule_download_series = fake_launch
    entry = {"media_type": "movie", "title": "Peli", "year": "2024",
             "replace": True, "amule_hashes": [OLD]}
    nd, ok = App._download_request_launch(app, {"r1": entry}, "r1")
    assert ok is True
    assert seen["exclude"] == (OLD,)
    assert nd["r1"]["amule_hashes"] == [NEW]


def test_launch_normal_no_excluye(monkeypatch):
    seen = {}

    def fake_launch(query, is_movie=False, expected_year=None, typical_size=None,
                    exclude_hashes=()):
        seen.update(exclude=tuple(exclude_hashes))
        return True, "", NEW
    app = SimpleNamespace(config_data={"series_search_patterns": {}})
    app._download_requests_user = lambda: "Jose"
    app._download_request_canonical = lambda entry: ("Peli", "2024")
    app._download_request_targets = lambda entry, name: [("movie",)]
    app._typical_size_for_series = lambda *a: None
    app._series_prefers_castellano = lambda *a: False
    app._download_request_record_replacement = lambda *a: None
    app._auto_amule_download_series = fake_launch
    entry = {"media_type": "movie", "title": "Peli", "year": "2024",
             "amule_hashes": [OLD]}
    nd, ok = App._download_request_launch(app, {"r1": entry}, "r1")
    assert ok is True
    assert seen["exclude"] == ()
