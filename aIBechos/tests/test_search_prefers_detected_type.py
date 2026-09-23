"""La búsqueda en TMDB debe respetar el tipo que ya dedujo el nombre del
archivo. Caso real: "Amadeus.1x05.Episodio.5...mkv" se renombraba como
"Amadeus (1984).mkv" -- la película, mucho más popular que la serie, ganaba
aunque el "1x05" no deje ninguna duda de que es un episodio."""
import pytest

from core.api_client import TMDBClient, detect_episode


# Lo que devuelve /search/multi para "Amadeus": la película es un clásico
# con 8 Óscar, así que arrasa en popularidad frente a la serie.
_RESULTADOS_AMADEUS = [
    {"media_type": "movie", "title": "Amadeus", "popularity": 45.0, "id": 279},
    {"media_type": "tv", "name": "Amadeus", "popularity": 3.2, "id": 999},
]


@pytest.fixture
def tmdb(monkeypatch):
    c = TMDBClient(api_key="x")
    monkeypatch.setattr(c, "_get", lambda path, **kw: {"results": list(_RESULTADOS_AMADEUS)})
    return c


def test_filename_with_episode_number_is_detected_as_tv():
    det = detect_episode("Amadeus.1x05.Episodio.5.(Spanish.English.Subs)"
                         ".WEBRip.1080p.x265-EAC3.by.piter332.mkv")
    assert det["media_type"] == "tv"
    assert (det["season"], det["episode"]) == (1, 5)


def test_tv_result_wins_when_the_filename_says_tv(tmdb):
    results = tmdb.search_multi("Amadeus", prefer_type="tv")
    assert results[0]["media_type"] == "tv", "la serie debe ir primero, no la película"


def test_movie_result_wins_when_the_filename_says_movie(tmdb):
    results = tmdb.search_multi("Amadeus", prefer_type="movie")
    assert results[0]["media_type"] == "movie"


def test_anime_counts_as_tv(tmdb):
    # detect_episode devuelve "anime" para algunos patrones; TMDB no
    # distingue anime de serie.
    results = tmdb.search_multi("Amadeus", prefer_type="anime")
    assert results[0]["media_type"] == "tv"


def test_without_a_preference_popularity_still_decides(tmdb):
    # Comportamiento de siempre para quien no pase prefer_type.
    results = tmdb.search_multi("Amadeus")
    assert results[0]["media_type"] == "movie"


def test_preference_reorders_but_never_discards(tmdb):
    # Si el tipo preferido no existe entre los resultados, los del otro
    # tipo siguen estando -- nunca se deja al usuario sin nada que elegir.
    solo_pelicula = [{"media_type": "movie", "title": "Amadeus", "popularity": 45.0}]
    tmdb._get = lambda path, **kw: {"results": list(solo_pelicula)}
    results = tmdb.search_multi("Amadeus", prefer_type="tv")
    assert len(results) == 1
    assert results[0]["media_type"] == "movie"


def test_popularity_still_orders_within_the_preferred_type(tmdb):
    varias = [
        {"media_type": "tv", "name": "Amadeus B", "popularity": 1.0},
        {"media_type": "movie", "title": "Amadeus peli", "popularity": 99.0},
        {"media_type": "tv", "name": "Amadeus A", "popularity": 8.0},
    ]
    tmdb._get = lambda path, **kw: {"results": list(varias)}
    results = tmdb.search_multi("Amadeus", prefer_type="tv")
    assert [r.get("name") or r.get("title") for r in results] == [
        "Amadeus A", "Amadeus B", "Amadeus peli"]
