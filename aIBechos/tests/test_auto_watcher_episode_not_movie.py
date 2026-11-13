"""Un archivo con numeración de capítulo no se identifica como película
(real: "2x41 - Patata bollente ... by animehunter.mkv" se subió como la
película "La patata caliente (1979)")."""
from core.auto_watcher import AutoWatcher

_only = AutoWatcher._only_series_if_episode


def test_capitulo_descarta_peliculas():
    res = [{"media_type": "movie", "title": "La patata caliente"},
           {"media_type": "tv", "name": "Otra"}]
    assert _only(res, "tv", 2, 41) == [res[1]]
    assert _only(res[:1], "tv", 2, 41) == []


def test_sin_numeracion_o_pelicula_no_cambia():
    res = [{"media_type": "movie", "title": "X"}]
    assert _only(res, "movie", None, None) == res
    assert _only(res, "tv", None, None) == res
    assert _only([], "tv", 1, 1) == []
