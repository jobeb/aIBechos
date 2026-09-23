from core.ftp_categories import (
    new_category_id,
    choose_category,
    split_template,
    build_wildcard_category,
    find_existing_category_folder,
)


def test_new_category_id_are_unique_and_prefixed():
    ids = {new_category_id() for _ in range(20)}
    assert len(ids) == 20
    assert all(i.startswith("cat_") for i in ids)


def test_choose_category_specific_match_wins_over_order():
    categories = [
        {"name": "Series",       "genre_ids": []},
        {"name": "Documentales", "genre_ids": [99]},
    ]
    chosen = choose_category([99], categories)
    assert chosen["name"] == "Documentales"


def test_choose_category_first_specific_match_wins_when_two_overlap():
    categories = [
        {"name": "Infantil",  "genre_ids": [16, 10751]},
        {"name": "Animacion", "genre_ids": [16]},
    ]
    # el genero 16 esta en ambas -- el orden de la lista desempata
    chosen = choose_category([16], categories)
    assert chosen["name"] == "Infantil"


def test_choose_category_falls_back_to_wildcard():
    categories = [
        {"name": "Documentales", "genre_ids": [99]},
        {"name": "Series",       "genre_ids": []},
    ]
    chosen = choose_category([18], categories)  # 18 = Drama, no matchea ninguna especifica
    assert chosen["name"] == "Series"


def test_choose_category_empty_genre_ids_falls_back_to_wildcard():
    categories = [
        {"name": "Documentales", "genre_ids": [99]},
        {"name": "Series",       "genre_ids": []},
    ]
    chosen = choose_category([], categories)
    assert chosen["name"] == "Series"


def test_choose_category_returns_none_without_categories():
    assert choose_category([16], []) is None


def test_choose_category_works_with_string_genre_ids():
    # "libro" no tiene géneros de TMDB (ints) -- core/book_client.py y
    # core/comicvine_client.py marcan MediaInfo.genre_ids con las
    # etiquetas fijas "ebook"/"comic" en su lugar. choose_category() no
    # debe asumir que son ints -- una intersección de listas funciona
    # igual con strings.
    categories = [
        {"name": "Cómics", "genre_ids": ["comic"]},
        {"name": "Libros",  "genre_ids": []},
    ]
    assert choose_category(["comic"], categories)["name"] == "Cómics"
    assert choose_category(["ebook"], categories)["name"] == "Libros"


def test_choose_category_returns_none_without_wildcard_and_no_match():
    categories = [{"name": "Documentales", "genre_ids": [99]}]
    assert choose_category([18], categories) is None


def test_split_template_with_serie_placeholder():
    root, rel = split_template("/datos2/series/{serie}/Temporada {temporada:02d}/")
    assert root == "/datos2/series"
    assert rel == "{serie}/Temporada {temporada:02d}/"


def test_split_template_without_serie_placeholder_is_all_root():
    root, rel = split_template("/peliculas/varias")
    assert root == "/peliculas/varias"
    assert rel == ""


def test_split_template_empty_string():
    assert split_template("") == ("", "")
    assert split_template(None) == ("", "")


def test_build_wildcard_category_shape():
    cat = build_wildcard_category("Series", "/datos2/series/{serie}/Temporada {temporada:02d}/")
    assert cat["name"] == "Series"
    assert cat["genre_ids"] == []
    assert cat["root"] == "/datos2/series"
    assert cat["template"] == "{serie}/Temporada {temporada:02d}/"
    assert cat["id"].startswith("cat_")


def test_build_wildcard_category_blank_template_has_no_root():
    cat = build_wildcard_category("Películas", "")
    assert cat["root"] == ""


# ── find_existing_category_folder ──

def test_find_existing_category_folder_real_world_case_rick_y_morty():
    # Caso real que motivo esta funcion: Rick y Morty (animacion para
    # adultos) ya archivada a mano en "Series", pero "SeriesPeques" (genero
    # Animacion+Infantil) va PRIMERO en la lista -- sin esta funcion,
    # choose_category() sola habria elegido "SeriesPeques" y creado una
    # carpeta duplicada, exactamente lo que paso de verdad.
    categories = [
        {"name": "SeriesPeques", "root": "/datos2/seriespeques", "genre_ids": [16, 10762]},
        {"name": "Series", "root": "/datos2/series", "genre_ids": []},
    ]
    listings = {
        "/datos2/seriespeques": [],
        "/datos2/series": ["Rick Y Morty"],   # capitalizacion distinta a TMDB
    }
    cat, folder = find_existing_category_folder(
        categories, "Rick y Morty", None, lambda root: listings[root])
    assert cat["name"] == "Series"
    assert folder == "Rick Y Morty"


def test_find_existing_category_folder_no_match_returns_none():
    categories = [{"name": "Series", "root": "/datos2/series", "genre_ids": []}]
    cat, folder = find_existing_category_folder(
        categories, "Serie Nueva", None, lambda root: ["Otra Serie"])
    assert cat is None
    assert folder is None


def test_find_existing_category_folder_exact_match_after_sanitizing():
    categories = [{"name": "Series", "root": "/datos2/series", "genre_ids": []}]
    cat, folder = find_existing_category_folder(
        categories, "Breaking Bad", None, lambda root: ["Breaking Bad"])
    assert cat["name"] == "Series"
    assert folder == "Breaking Bad"


def test_find_existing_category_folder_known_folder_name_wins_even_with_low_similarity():
    categories = [{"name": "Series", "root": "/datos2/series", "genre_ids": []}]
    cat, folder = find_existing_category_folder(
        categories, "Acusado", "Accused", lambda root: ["Accused"])
    assert cat["name"] == "Series"
    assert folder == "Accused"


def test_find_existing_category_folder_skips_category_when_dir_lookup_returns_none():
    # None de dir_lookup = "no se pudo comprobar esta categoria ahora" (p.ej.
    # use_cache_only en la GUI) -- se salta, no cuenta como "no existe".
    categories = [
        {"name": "SinComprobar", "root": "/a", "genre_ids": []},
        {"name": "Series", "root": "/b", "genre_ids": []},
    ]
    calls = []

    def dir_lookup(root):
        calls.append(root)
        if root == "/a":
            return None
        return ["Mi Serie"]

    cat, folder = find_existing_category_folder(categories, "Mi Serie", None, dir_lookup)
    assert cat["name"] == "Series"
    assert calls == ["/a", "/b"]


def test_find_existing_category_folder_ignores_category_without_root():
    categories = [{"name": "SinRuta", "root": "", "genre_ids": []}]
    cat, folder = find_existing_category_folder(
        categories, "Mi Serie", None, lambda root: ["Mi Serie"])
    assert cat is None
    assert folder is None


def test_find_existing_category_folder_low_similarity_does_not_match():
    categories = [{"name": "Series", "root": "/datos2/series", "genre_ids": []}]
    cat, folder = find_existing_category_folder(
        categories, "Loki", None, lambda root: ["Lucifer"])
    assert cat is None
    assert folder is None


def test_find_existing_category_folder_known_year_disambiguates_a_remake():
    # Caso real: "Ranma ½" (1989, Jellyfin) y "Ranma1/2" (2024, Plex) --
    # las dos carpetas reales llevan el año entre parentesis para
    # distinguir el remake del original, pero el titulo que da el
    # servidor de medios no trae año -- ni el nombre exacto ni el ratio
    # normal (< 0.90) encuentran la carpeta sin known_year.
    categories = [{"name": "Series", "root": "/datos2/series", "genre_ids": []}]
    listings = ["Ranma (1989)", "Ranma (2024)"]

    cat, folder = find_existing_category_folder(
        categories, "Ranma ½", None, lambda root: listings, known_year="1989")
    assert cat["name"] == "Series"
    assert folder == "Ranma (1989)"

    cat, folder = find_existing_category_folder(
        categories, "Ranma1/2", None, lambda root: listings, known_year="2024")
    assert cat["name"] == "Series"
    assert folder == "Ranma (2024)"


def test_find_existing_category_folder_does_not_merge_unrelated_series_sharing_a_word():
    # Caso real: la carpeta "Arcadia" se fusionó con "Los 3 de Adabo:
    # Cuentos de Arcadia" (dos series sin ninguna relación) y se subieron
    # episodios de una a la carpeta de la otra -- "arcadia" es una
    # subcadena literal del segundo título, pero solo al final, no un
    # título corto de verdad (ver test_similarity_does_not_false_positive_
    # on_word_buried_at_the_end en test_series_match.py).
    categories = [{"name": "Series", "root": "/datos2/series", "genre_ids": []}]
    cat, folder = find_existing_category_folder(
        categories, "Los 3 de Adabo: Cuentos de Arcadia", None, lambda root: ["Arcadia"])
    assert cat is None
    assert folder is None

    cat, folder = find_existing_category_folder(
        categories, "Arcadia", None, lambda root: ["Los 3 de Adabo: Cuentos de Arcadia"])
    assert cat is None
    assert folder is None


def test_find_existing_category_folder_without_known_year_still_fails_on_this_case():
    # Sin known_year, el comportamiento previo (fallo) no cambia -- el
    # fallback solo se activa cuando SE PASA un año conocido de antemano.
    categories = [{"name": "Series", "root": "/datos2/series", "genre_ids": []}]
    cat, folder = find_existing_category_folder(
        categories, "Ranma ½", None, lambda root: ["Ranma (1989)", "Ranma (2024)"])
    assert cat is None
    assert folder is None
