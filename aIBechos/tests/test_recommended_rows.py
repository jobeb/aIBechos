"""Filas de Recomendado (core/recommended_rows.py): mismas categorías que la
web, normalizado, relleno multipágina y filtros. Puro, sin red ni Qt."""

from core.recommended_rows import (apply_filters, fetch_row, movie_available, movie_providers_available,
                                   normalize_item, row_limit, rows_for, tv_available)


class _Client:
    """Finge TMDBClient.list_endpoint con páginas programadas."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def list_endpoint(self, path, kind, page=1, params=None):
        self.calls.append((path, kind, page, params))
        items, total = self.pages[min(page, len(self.pages)) - 1]
        return [dict(r) for r in items], total


def _item(i, **kw):
    d = {"id": i, "media_type": "movie", "title": f"Peli {i}", "release_date": f"202{i % 10}-03-04",
         "poster_path": f"/p{i}.jpg", "vote_average": 7.5, "overview": f"Sinopsis {i}",
         "genre_ids": [28], "original_language": "en", "origin_country": []}
    d.update(kw)
    return d


def test_mismas_categorias_que_la_web():
    movies, tv = rows_for("movie"), rows_for("tv")
    assert [r["id"] for r in movies] == ["trend", "top", "top-prev", "cines", "pronto", "best",
                                         "plat-Netflix", "plat-Prime Video", "plat-Disney+", "plat-Max",
                                         "plat-Movistar+", "plat-Apple TV",
                                         "anim", "anime", "comedia", "terror", "scifi", "accion", "docu"]
    assert [r["id"] for r in tv] == ["trend", "top", "top-prev", "emision", "best",
                                     "plat-Netflix", "plat-Prime Video", "plat-Disney+", "plat-Max",
                                     "plat-Movistar+", "plat-Apple TV",
                                     "anim", "anime", "comedia", "misterio", "scifi", "accion", "docu", "mini"]
    assert movies[1]["numbered"] and row_limit(movies[1]) == 10
    assert not movies[0]["numbered"] and row_limit(movies[0]) == 20
    assert movies[4]["no_avail"] and movies[4]["hide_if_unavail"]  # Próximamente
    assert "202" in movies[1]["title"]  # Top 10 del año en curso


def test_normalize_item():
    it = normalize_item(_item(7, title=""), "movie")
    assert it["tmdb_id"] == 7 and it["year"] == "2027" and it["vote"] == 7.5
    assert it["poster_url"].endswith("/p7.jpg")
    tv = normalize_item({"id": 9, "name": "Serie Nueve", "first_air_date": "2024-01-09",
                         "vote_average": None, "genre_ids": [18],
                         "original_language": "es", "origin_country": ["ES"]}, "tv")
    assert tv["title"] == "Serie Nueve" and tv["year"] == "2024" and tv["vote"] == 0.0
    assert tv["poster_url"] is None


def test_fetch_row_rellena_y_deduplica():
    rowdef = {"id": "x", "title": "X", "path": "/discover/movie", "params": {"a": 1}}
    client = _Client([([_item(1), _item(2)], 3), ([_item(2), _item(3)], 3), ([_item(4)], 3)])
    items = fetch_row(client, "movie", rowdef, limit=4)
    assert [i["tmdb_id"] for i in items] == [1, 2, 3, 4]
    assert len(client.calls) == 3  # paró al llenar el tope
    assert client.calls[0][3] == {"a": 1}


def test_fetch_row_top_con_rank():
    rowdef = {"id": "top", "title": "Top", "path": "/discover/movie", "params": {}, "numbered": True}
    items = fetch_row(_Client([([_item(1), _item(2)], 1)]), "movie", rowdef)
    assert [i["rank"] for i in items] == [1, 2]


def test_apply_filters():
    items = [normalize_item(_item(1, title="Acción total"), "movie"),
             normalize_item(_item(2, title="Drama total", original_language="ja",
                                  genre_ids=[18]), "movie"),
             normalize_item(_item(3, title="Otra acción", release_date="1999-05-05"), "movie")]
    assert len(apply_filters(items, owned_ids={("movie", 1)}, hide_owned=True)) == 2
    assert [i["tmdb_id"] for i in apply_filters(items, hide_asian=True)] == [1, 3]
    assert [i["tmdb_id"] for i in apply_filters(items, genre_ids={18})] == [2]
    assert [i["tmdb_id"] for i in apply_filters(items, year_min=2020)] == [1, 2]
    assert [i["tmdb_id"] for i in apply_filters(items, text="drama")] == [2]
    assert len(apply_filters(items)) == 3


def test_disponibilidad():
    assert tv_available({"results": {"ES": {"flatrate": [{"a": 1}]}}}) is True
    assert tv_available({"results": {"ES": {}}}) is False
    assert tv_available({"results": {}}) is None  # sin dato: se deja
    assert tv_available({"results": {"US": {"flatrate": [1]}}}) is None  # sin ES: se deja
    assert movie_providers_available({"ES": {"rent": [1]}}) is True
    assert movie_providers_available({"ES": {}}) is False
    assert movie_providers_available({}) is None
    assert movie_providers_available(None) is None
    assert tv_available(None) is None
    assert tv_available({"results": None}) is None
    assert movie_available({"flatrate": ["Netflix"]}) is True
    assert movie_available({}) is False
    assert movie_available(None) is None
