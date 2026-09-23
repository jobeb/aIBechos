"""Pruebas de _find_category_with_existing_folder: la organización real
del servidor debe prevalecer sobre la clasificación automática por
género -- ver core/ftp_categories.py::find_existing_category_folder y el
caso real que lo motivó (Rick y Morty, animación para adultos ya
archivada a mano fuera de la categoría infantil)."""

from unittest.mock import MagicMock

from core.auto_watcher import AutoWatcher


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def _make_watcher(list_dirs_by_root: dict):
    config = _FakeConfig({"poll_interval": 10})
    tmdb = MagicMock()
    ftp = MagicMock()
    ftp.list_dirs.side_effect = lambda root: list(list_dirs_by_root.get(root, []))
    watcher = AutoWatcher("/watch", config, tmdb, ftp, on_event=lambda *a: None)
    return watcher


def test_finds_existing_folder_in_a_different_category_than_genre_would_pick():
    # Reproduce el caso real: "SeriesPeques" iria primero por genero, pero
    # la serie ya existe en "Series" -- debe ganar "Series".
    watcher = _make_watcher({
        "/datos2/seriespeques": [],
        "/datos2/series": ["Rick Y Morty"],
    })
    categories = [
        {"name": "SeriesPeques", "root": "/datos2/seriespeques", "genre_ids": [16, 10762]},
        {"name": "Series", "root": "/datos2/series", "genre_ids": []},
    ]
    cat, folder = watcher._find_category_with_existing_folder(categories, "Rick y Morty")
    assert cat["name"] == "Series"
    assert folder == "Rick Y Morty"


def test_returns_none_when_series_is_genuinely_new():
    watcher = _make_watcher({"/datos2/series": ["Otra Serie"]})
    categories = [{"name": "Series", "root": "/datos2/series", "genre_ids": []}]
    cat, folder = watcher._find_category_with_existing_folder(categories, "Serie Totalmente Nueva")
    assert cat is None
    assert folder is None


def test_caches_directory_listing_across_calls():
    watcher = _make_watcher({"/datos2/series": ["Mi Serie"]})
    categories = [{"name": "Series", "root": "/datos2/series", "genre_ids": []}]

    watcher._find_category_with_existing_folder(categories, "Mi Serie")
    watcher._find_category_with_existing_folder(categories, "Mi Serie")

    # list_dirs se llama 2 veces (doble listado, ver el motivo en la
    # implementación) en la PRIMERA consulta, y ninguna más en la segunda
    # -- la raíz ya quedó en self._ftp_dir_cache.
    assert watcher.ftp.list_dirs.call_count == 2
