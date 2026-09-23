from core.missing_movies import (build_movie_rows, MOVIE_LIST_LABELS,
                                 apply_in_server_filter, filter_by_text,
                                 sort_movie_rows, apply_watch_availability_filter,
                                 watch_available, format_watch_display,
                                 apply_genre_filter, row_matches_genre,
                                 apply_origin_filter, is_asian_origin)


def _tmdb_result(tid, title="Pelicula", release_date="2024-05-01",
                 poster_path="/p.jpg", vote_average=7.0, popularity=10.0,
                 overview="Sinopsis", genre_ids=(28,)):
    return {"id": tid, "title": title, "original_title": title,
            "release_date": release_date, "poster_path": poster_path,
            "vote_average": vote_average, "popularity": popularity,
            "overview": overview, "genre_ids": list(genre_ids)}


def _tv_result(tid, name="Serie", first_air_date="2024-05-01",
               poster_path="/p.jpg", vote_average=8.0, popularity=9.0,
               overview="Sinopsis", genre_ids=(18,)):
    # Mismo formato de salida que TMDBClient._tv_list: normalizado a
    # title/release_date y con media_type="tv" explícito.
    return {"id": tid, "name": name, "original_name": name,
            "title": name, "release_date": first_air_date, "first_air_date": first_air_date,
            "poster_path": poster_path, "media_type": "tv",
            "vote_average": vote_average, "popularity": popularity,
            "overview": overview, "genre_ids": list(genre_ids)}


def test_build_movie_rows_basic():
    rows = build_movie_rows({"trending": [_tmdb_result(1, "Dune")]}, {})
    assert len(rows) == 1
    r = rows[0]
    assert r["tmdb_id"] == 1
    assert r["title"] == "Dune"
    assert r["year"] == "2024"
    assert r["release_date"] == "2024-05-01"
    assert r["poster_url"] == "https://image.tmdb.org/t/p/w300/p.jpg"
    assert r["list"] == "trending"
    assert r["in_server"] is False


def test_build_movie_rows_dedup_by_tmdb_id():
    # Misma película en trending y popular -- solo una fila, gana la primera lista.
    rows = build_movie_rows({
        "trending": [_tmdb_result(1, "Dune")],
        "popular": [_tmdb_result(1, "Dune")],
    }, {})
    assert len(rows) == 1
    assert rows[0]["list"] == "trending"


def test_build_movie_rows_marks_in_server():
    rows = build_movie_rows({
        "trending": [_tmdb_result(1, "Dune"), _tmdb_result(2, "Oppenheimer")],
    }, {1})
    by_id = {r["tmdb_id"]: r for r in rows}
    assert by_id[1]["in_server"] is True
    assert by_id[2]["in_server"] is False


def test_build_movie_rows_no_poster_keeps_none():
    rows = build_movie_rows({"trending": [{**_tmdb_result(1), "poster_path": None}]}, {})
    assert rows[0]["poster_url"] is None


def test_apply_in_server_filter():
    rows = [
        {"tmdb_id": 1, "in_server": True},
        {"tmdb_id": 2, "in_server": False},
    ]
    assert apply_in_server_filter(rows, hide_in_server=False) == rows
    assert [r["tmdb_id"] for r in apply_in_server_filter(rows, hide_in_server=True)] == [2]


def test_filter_by_text():
    rows = [
        {"tmdb_id": 1, "title": "Dune: Parte Dos", "year": "2024"},
        {"tmdb_id": 2, "title": "Oppenheimer", "year": "2023"},
    ]
    assert len(filter_by_text(rows, "")) == 2
    assert [r["tmdb_id"] for r in filter_by_text(rows, "dune")] == [1]
    assert [r["tmdb_id"] for r in filter_by_text(rows, "2023")] == [2]
    assert filter_by_text(rows, "nada") == []


def test_sort_movie_rows_by_title_asc_desc():
    rows = [
        {"tmdb_id": 1, "title": "Zeta", "year": "", "popularity": 5, "vote_average": 6, "list": "trending"},
        {"tmdb_id": 2, "title": "Alfa", "year": "", "popularity": 9, "vote_average": 8, "list": "popular"},
    ]
    assert [r["tmdb_id"] for r in sort_movie_rows(rows, "title", asc=True)] == [2, 1]
    assert [r["tmdb_id"] for r in sort_movie_rows(rows, "title", asc=False)] == [1, 2]


def test_sort_movie_rows_by_popularity_and_vote():
    rows = [
        {"tmdb_id": 1, "title": "A", "year": "", "popularity": 5, "vote_average": 6, "list": "trending"},
        {"tmdb_id": 2, "title": "B", "year": "", "popularity": 9, "vote_average": 8, "list": "popular"},
    ]
    assert [r["tmdb_id"] for r in sort_movie_rows(rows, "popularity", asc=False)] == [2, 1]
    assert [r["tmdb_id"] for r in sort_movie_rows(rows, "vote_average", asc=False)] == [2, 1]
    assert [r["tmdb_id"] for r in sort_movie_rows(rows, "popularity", asc=True)] == [1, 2]


def test_sort_movie_rows_unknown_key_falls_back_to_title():
    rows = [
        {"tmdb_id": 1, "title": "Zeta", "year": "", "popularity": 5, "vote_average": 6, "list": "trending"},
        {"tmdb_id": 2, "title": "Alfa", "year": "", "popularity": 9, "vote_average": 8, "list": "popular"},
    ]
    assert [r["tmdb_id"] for r in sort_movie_rows(rows, "otra_cosa", asc=True)] == [2, 1]


def test_movie_list_labels_cover_all_lists():
    for key in ("trending", "popular", "upcoming", "now_playing", "on_the_air"):
        assert key in MOVIE_LIST_LABELS


def test_build_tv_rows_uses_name_and_first_air_date():
    # Los resultados de series vienen con media_type="tv" (ver
    # TMDBClient._tv_list): se normalizan a title/release_date y las filas
    # llevan media_type="tv" para que la GUI sepa distinguirlas.
    rows = build_movie_rows({"on_the_air": [_tv_result(500, "Casa de papel")]}, {})
    assert len(rows) == 1
    r = rows[0]
    assert r["tmdb_id"] == 500
    assert r["media_type"] == "tv"
    assert r["title"] == "Casa de papel"
    assert r["year"] == "2024"
    assert r["release_date"] == "2024-05-01"
    assert r["list"] == "on_the_air"


def test_build_tv_rows_marks_in_server_against_tv_ids():
    rows = build_movie_rows({"on_the_air": [_tv_result(500), _tv_result(501)]}, {500})
    by_id = {r["tmdb_id"]: r for r in rows}
    assert by_id[500]["in_server"] is True
    assert by_id[501]["in_server"] is False


def test_build_tv_rows_watch_is_empty():
    # Las series no consultan watch providers -- siempre quedan con "watch": {}
    rows = build_movie_rows({"on_the_air": [_tv_result(500)]}, {})
    assert rows[0]["watch"] == {}


def test_build_rows_keeps_movie_and_tv_with_same_numeric_id_separate():
    # TMDB numera películas y series en espacios separados: el mismo tmdb_id
    # puede ser una película Y una serie -- no son la misma cosa, y la
    # deduplicación no debe fusionarlas.
    movie = {**_tmdb_result(123, "Un numero"), "media_type": "movie"}
    tv = {**_tv_result(123, "Mismo numero")}
    rows = build_movie_rows({"trending": [movie, tv]}, {})
    assert len(rows) == 2
    types = {r["media_type"] for r in rows}
    assert types == {"movie", "tv"}
    by_type = {r["media_type"]: r for r in rows}
    assert by_type["movie"]["title"] == "Un numero"
    assert by_type["tv"]["title"] == "Mismo numero"


def test_build_movie_rows_defaults_media_type_to_movie():
    rows = build_movie_rows({"trending": [_tmdb_result(1, "Dune")]}, {})
    assert rows[0]["media_type"] == "movie"


def test_build_movie_rows_watch_defaults_empty():
    rows = build_movie_rows({"trending": [_tmdb_result(1, "Dune")]}, {})
    assert rows[0]["watch"] == {}


def test_build_movie_rows_carries_watch():
    watch = {1: {"flatrate": ["Netflix", "Prime Video"], "rent": ["Apple TV"]}}
    rows = build_movie_rows({"trending": [_tmdb_result(1, "Dune")]}, {}, watch_by_id=watch)
    assert rows[0]["watch"]["flatrate"] == ["Netflix", "Prime Video"]
    assert rows[0]["watch"]["rent"] == ["Apple TV"]


def test_watch_available():
    assert watch_available({"watch": {"flatrate": ["Netflix"]}}) is True
    assert watch_available({"watch": {"rent": ["Apple TV"]}}) is True
    assert watch_available({"watch": {}}) is False
    assert watch_available({"watch": None}) is False
    assert watch_available({}) is False


def test_apply_watch_availability_filter():
    rows = [
        {"tmdb_id": 1, "watch": {"flatrate": ["Netflix"]}},
        {"tmdb_id": 2, "watch": {}},
    ]
    assert len(apply_watch_availability_filter(rows, only_available=False)) == 2
    assert [r["tmdb_id"] for r in apply_watch_availability_filter(rows, only_available=True)] == [1]


def test_format_watch_display_streaming_first():
    r = {"watch": {"flatrate": ["Netflix", "HBO Max", "Prime Video", "Disney+"],
                   "rent": ["Apple TV"]}}
    # Tres nombres visibles + "y 1 más" por el cuarto.
    assert format_watch_display(r) == "Netflix, HBO Max, Prime Video y 1 más"


def test_format_watch_display_no_streaming_rent_or_buy():
    assert format_watch_display({"watch": {"rent": ["Apple TV"]}}) == "Alquiler/compra"
    assert format_watch_display({"watch": {"buy": ["Google Play"]}}) == "Alquiler/compra"


def test_format_watch_display_only_in_theaters():
    assert format_watch_display({"watch": {}}) == "Solo en cines"
    assert format_watch_display({"watch": None}) == "Solo en cines"


# ---- Filtro por género (barra de filtros de "Recomendado") ----

def _genre_row(tid, *genres):
    return {"tmdb_id": tid, "title": f"Título {tid}", "year": "2024",
            "genres": list(genres)}


def test_apply_genre_filter_exact_match():
    rows = [
        _genre_row(1, "Terror", "Suspense"),
        _genre_row(2, "Comedia"),
        _genre_row(3, "Animación"),
    ]
    assert [r["tmdb_id"] for r in apply_genre_filter(rows, "Terror")] == [1]
    assert [r["tmdb_id"] for r in apply_genre_filter(rows, "Comedia")] == [2]
    assert [r["tmdb_id"] for r in apply_genre_filter(rows, "Animación")] == [3]


def test_apply_genre_filter_case_insensitive():
    rows = [_genre_row(1, "Terror"), _genre_row(2, "Comedia")]
    assert [r["tmdb_id"] for r in apply_genre_filter(rows, "terror")] == [1]
    assert [r["tmdb_id"] for r in apply_genre_filter(rows, "TERROR")] == [1]


def test_apply_genre_filter_anime_is_animation_alias():
    """TMDB no tiene género "Anime": es un alias de usuario de "Animación" --
    un anime (p.ej. "Dragon Ball") viene etiquetado como Animación."""
    rows = [
        _genre_row(1, "Animación", "Acción"),
        _genre_row(2, "Animación"),
        _genre_row(3, "Comedia"),
    ]
    assert [r["tmdb_id"] for r in apply_genre_filter(rows, "Anime")] == [1, 2]
    assert [r["tmdb_id"] for r in apply_genre_filter(rows, "anime")] == [1, 2]


def test_apply_genre_filter_no_match_returns_empty():
    rows = [_genre_row(1, "Comedia")]
    assert apply_genre_filter(rows, "Terror") == []


def test_apply_genre_filter_empty_or_todos_returns_all():
    rows = [_genre_row(1, "Terror"), _genre_row(2, "Comedia")]
    assert apply_genre_filter(rows, "") == rows
    assert apply_genre_filter(rows, "Todos") == rows
    assert apply_genre_filter(rows, None) == rows


def test_apply_genre_filter_row_without_genres_is_hidden():
    """Filas sin "genres" (sin datos de géneros TMDB, p.ej. "Happu Ki Ultan
    Paltan", "Hockey Psychology" o "ChocoPro Wrestling") no pertenecen a
    ningún género concreto: al filtrar por un género se ocultan, solo se ven
    con "Todos". Sin esto aparecían mezcladas en TODAS las categorías."""
    rows = [_genre_row(1, "Terror"), {"tmdb_id": 2, "title": "Sin géneros", "genres": []}]
    assert [r["tmdb_id"] for r in apply_genre_filter(rows, "Terror")] == [1]
    assert [r["tmdb_id"] for r in apply_genre_filter(rows, "Todos")] == [1, 2]
    assert [r["tmdb_id"] for r in apply_genre_filter(rows, "")] == [1, 2]


def test_apply_genre_filter_unknown_value_matches_nothing():
    """Un género que no coincide con ninguna fila devuelve lista vacía (el
    selector solo ofrece géneros presentes en las filas, así que un valor
    fuera de esas opciones no se produce en la práctica)."""
    rows = [_genre_row(1, "Comedia")]
    assert apply_genre_filter(rows, "ciencia") == []


# ---- Filtro de origen asiático (interruptor "Ocultar asiáticas") ----

def _origin_row(tid, lang="", countries=None):
    return {"tmdb_id": tid, "title": f"Título {tid}", "original_language": lang,
            "origin_country": list(countries or [])}


def test_is_asian_origin_by_language():
    """El origen se deduce del idioma original (ISO 639-1): el cine y las
    series de China/Japón/Corea/India/Tailandia... no se quieren."""
    for lang in ("zh", "ja", "ko", "hi", "ta", "th", "vi", "id", "ur"):
        assert is_asian_origin(_origin_row(1, lang)), lang
    assert not is_asian_origin(_origin_row(1, "en"))
    assert not is_asian_origin(_origin_row(1, "es"))
    assert not is_asian_origin(_origin_row(1, "fr"))


def test_is_asian_origin_by_country_for_series():
    """Las series traen origin_country (ISO 3166-1) además del idioma: un
    país asiático delata el origen aunque el idioma no (p.ej. una serie
    china doblada/multilingüe)."""
    for country in ("CN", "JP", "KR", "IN", "TH", "TW", "HK", "ID"):
        assert is_asian_origin(_origin_row(1, "", [country])), country
    assert not is_asian_origin(_origin_row(1, "", ["US"]))
    assert not is_asian_origin(_origin_row(1, "", ["ES"]))


def test_is_asian_origin_unknown_is_not_asian():
    """Sin dato de origen no se descarta: el filtro solo excluye lo que
    sabe asiático de verdad (las cachés viejas no guardaban el origen)."""
    assert not is_asian_origin(_origin_row(1))
    assert not is_asian_origin({"tmdb_id": 1, "title": "Sin origen"})


def test_apply_origin_filter_hides_asian():
    rows = [
        _origin_row(1, "es"),               # española: se queda
        _origin_row(2, "en", ["US"]),       # americana: se queda
        _origin_row(3, "ko"),               # coreana: se oculta
        _origin_row(4, "ja"),               # japonesa: se oculta
        _origin_row(5),                     # sin dato: se queda
    ]
    kept = [r["tmdb_id"] for r in apply_origin_filter(rows, hide_asian=True)]
    assert kept == [1, 2, 5]


def test_apply_origin_filter_off_returns_all():
    rows = [_origin_row(1, "es"), _origin_row(2, "ko")]
    assert apply_origin_filter(rows, hide_asian=False) == rows


def test_build_movie_rows_carries_origin_fields():
    """build_movie_rows deja original_language/origin_country en cada fila,
    para que el filtro "Ocultar asiáticas" funcione en el escaneo nuevo (la
    caché vieja sin esos campos no descarta nada, ver arriba)."""
    movie = {**_tmdb_result(1, "Dune"), "original_language": "en"}
    tv = {**_tv_result(500, "Serie"), "original_language": "ko", "origin_country": ["KR"]}
    rows = build_movie_rows({"trending": [movie, tv]}, {})
    by_type = {r["media_type"]: r for r in rows}
    assert by_type["movie"]["original_language"] == "en"
    assert by_type["tv"]["original_language"] == "ko"
    assert by_type["tv"]["origin_country"] == ["KR"]
    assert by_type["movie"]["origin_country"] == []
