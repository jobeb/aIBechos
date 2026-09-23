from core.category_stats import (
    resolve_category_and_folder, add_folder_bytes, remove_folder,
    category_summaries, total_bytes, bytes_by_disk, load_local_cache, save_local_cache,
    SCAN_VERSION, wrap_for_remote, unwrap_from_remote,
)


TV_CAT = {"id": "cat-tv", "name": "Series", "root": "/datos2/series"}
MOVIE_CAT = {"id": "cat-movie", "name": "Peliculas", "root": "/datos/peliculas"}
FTP_CATEGORIES = {"tv": [TV_CAT], "movie": [MOVIE_CAT]}


def test_resolve_category_and_folder_matches_correct_root():
    result = resolve_category_and_folder(
        "/datos2/series/Stargate Atlantis/Temporada 01/ep01.mkv", FTP_CATEGORIES)
    assert result == ("cat-tv", "Series", "Stargate Atlantis")


def test_resolve_category_and_folder_matches_folder_without_subfolders():
    result = resolve_category_and_folder("/datos/peliculas/Matrix (1999)/Matrix.mkv", FTP_CATEGORIES)
    assert result == ("cat-movie", "Peliculas", "Matrix (1999)")


def test_resolve_category_and_folder_groups_loose_file_at_root_by_base_name():
    # Típico de películas guardadas como un único archivo, sin carpeta
    # propia -- antes esto se ignoraba por completo (bug real: el panel
    # de Estadísticas contaba muchas menos películas de las reales).
    result = resolve_category_and_folder("/datos/peliculas/Matrix (1999).mkv", FTP_CATEGORIES)
    assert result == ("cat-movie", "Peliculas", "Matrix (1999)")


def test_resolve_category_and_folder_groups_companion_files_with_their_video():
    video = resolve_category_and_folder("/datos/peliculas/Matrix (1999).mkv", FTP_CATEGORIES)
    poster = resolve_category_and_folder("/datos/peliculas/Matrix (1999)-poster.jpg", FTP_CATEGORIES)
    assert video == poster


def test_resolve_category_and_folder_none_when_no_root_matches():
    assert resolve_category_and_folder("/otro/sitio/archivo.mkv", FTP_CATEGORIES) is None


def test_resolve_category_and_folder_picks_longest_matching_root():
    cats = {"tv": [
        {"id": "cat-a", "name": "Series", "root": "/datos2"},
        {"id": "cat-b", "name": "SeriesPeques", "root": "/datos2/series/peques"},
    ]}
    result = resolve_category_and_folder("/datos2/series/peques/Bluey/ep01.mkv", cats)
    assert result == ("cat-b", "SeriesPeques", "Bluey")


def test_resolve_category_and_folder_handles_backslashes():
    result = resolve_category_and_folder(
        r"/datos2/series/Stargate Atlantis\Temporada 01\ep01.mkv", FTP_CATEGORIES)
    assert result == ("cat-tv", "Series", "Stargate Atlantis")


def test_resolve_category_and_folder_is_folder_path_resolves_the_folder_itself():
    # Un borrado pasa la ruta de la CARPETA en sí (delete_folder_recursive
    # borra la carpeta entera), no la de un archivo dentro de ella -- sin
    # is_folder_path=True esto se confundiría con un archivo suelto.
    result = resolve_category_and_folder(
        "/datos2/series/Stargate Atlantis", FTP_CATEGORIES, is_folder_path=True)
    assert result == ("cat-tv", "Series", "Stargate Atlantis")


def test_resolve_category_and_folder_is_folder_path_ignores_trailing_slash():
    result = resolve_category_and_folder(
        "/datos2/series/Stargate Atlantis/", FTP_CATEGORIES, is_folder_path=True)
    assert result == ("cat-tv", "Series", "Stargate Atlantis")


def test_resolve_category_and_folder_without_is_folder_path_treats_same_path_as_loose_file():
    # La misma ruta, sin is_folder_path, se interpreta como un ARCHIVO
    # suelto en la raíz (comportamiento por defecto para subidas) -- aquí
    # "Stargate Atlantis" no tiene extensión, así que su nombre base es él
    # mismo tal cual.
    result = resolve_category_and_folder("/datos2/series/Stargate Atlantis", FTP_CATEGORIES)
    assert result == ("cat-tv", "Series", "Stargate Atlantis")


def test_resolve_category_and_folder_none_when_path_is_exactly_the_root():
    assert resolve_category_and_folder("/datos2/series", FTP_CATEGORIES, is_folder_path=True) is None


def test_add_folder_bytes_returns_new_dict_without_mutating_original():
    original = {}
    result = add_folder_bytes(original, "cat-tv", "Series", "Stargate Atlantis", 1000)
    assert original == {}
    assert result["cat-tv"]["folders"]["Stargate Atlantis"] == 1000


def test_add_folder_bytes_accumulates_across_multiple_uploads():
    data = add_folder_bytes({}, "cat-tv", "Series", "Stargate Atlantis", 1000)
    data = add_folder_bytes(data, "cat-tv", "Series", "Stargate Atlantis", 2000)
    assert data["cat-tv"]["folders"]["Stargate Atlantis"] == 3000


def test_add_folder_bytes_keeps_folders_separate():
    data = add_folder_bytes({}, "cat-tv", "Series", "Stargate Atlantis", 1000)
    data = add_folder_bytes(data, "cat-tv", "Series", "Rick y Morty", 500)
    assert data["cat-tv"]["folders"] == {"Stargate Atlantis": 1000, "Rick y Morty": 500}


def test_add_folder_bytes_keeps_categories_separate():
    data = add_folder_bytes({}, "cat-tv", "Series", "Stargate Atlantis", 1000)
    data = add_folder_bytes(data, "cat-movie", "Peliculas", "Matrix", 2000)
    assert data["cat-tv"]["folders"] == {"Stargate Atlantis": 1000}
    assert data["cat-movie"]["folders"] == {"Matrix": 2000}


def test_remove_folder_returns_new_dict_without_mutating_original():
    data = add_folder_bytes({}, "cat-tv", "Series", "Stargate Atlantis", 1000)
    result = remove_folder(data, "cat-tv", "Stargate Atlantis")
    assert "Stargate Atlantis" in data["cat-tv"]["folders"]
    assert "Stargate Atlantis" not in result["cat-tv"]["folders"]


def test_remove_folder_leaves_other_folders_untouched():
    data = add_folder_bytes({}, "cat-tv", "Series", "Stargate Atlantis", 1000)
    data = add_folder_bytes(data, "cat-tv", "Series", "Rick y Morty", 500)
    result = remove_folder(data, "cat-tv", "Stargate Atlantis")
    assert result["cat-tv"]["folders"] == {"Rick y Morty": 500}


def test_remove_folder_is_a_no_op_when_folder_not_present():
    data = add_folder_bytes({}, "cat-tv", "Series", "Stargate Atlantis", 1000)
    result = remove_folder(data, "cat-tv", "No Existe")
    assert result == data


def test_remove_folder_is_a_no_op_for_unknown_category():
    assert remove_folder({}, "cat-tv", "Stargate Atlantis") == {}


def test_category_summaries_counts_and_sums_per_category():
    data = add_folder_bytes({}, "cat-tv", "Series", "Stargate Atlantis", 1000)
    data = add_folder_bytes(data, "cat-tv", "Series", "Rick y Morty", 500)
    data = add_folder_bytes(data, "cat-movie", "Peliculas", "Matrix", 2000)
    summaries = {s["name"]: s for s in category_summaries(data)}
    assert summaries["Series"] == {"name": "Series", "count": 2, "bytes": 1500}
    assert summaries["Peliculas"] == {"name": "Peliculas", "count": 1, "bytes": 2000}


def test_category_summaries_empty_data():
    assert category_summaries({}) == []


def test_total_bytes_sums_across_all_categories():
    data = add_folder_bytes({}, "cat-tv", "Series", "Stargate Atlantis", 1000)
    data = add_folder_bytes(data, "cat-movie", "Peliculas", "Matrix", 2000)
    assert total_bytes(data) == 3000


def test_total_bytes_empty_data():
    assert total_bytes({}) == 0


def test_bytes_by_disk_groups_categories_sharing_a_disk():
    cats = {"tv": [
        {"id": "cat-a", "name": "Series", "root": "/datos2/series"},
        {"id": "cat-b", "name": "SeriesPeques", "root": "/datos2/series/peques"},
    ], "movie": [
        {"id": "cat-c", "name": "Peliculas", "root": "/datos/peliculas"},
    ]}
    data = add_folder_bytes({}, "cat-a", "Series", "Stargate Atlantis", 1000)
    data = add_folder_bytes(data, "cat-b", "SeriesPeques", "Bluey", 500)
    data = add_folder_bytes(data, "cat-c", "Peliculas", "Matrix", 2000)

    result = bytes_by_disk(data, cats)

    by_name = {c["name"]: c for c in result["datos2"]}
    assert by_name["Series"] == {"name": "Series", "count": 1, "bytes": 1000}
    assert by_name["SeriesPeques"] == {"name": "SeriesPeques", "count": 1, "bytes": 500}
    assert result["datos"] == [{"name": "Peliculas", "count": 1, "bytes": 2000}]


def test_bytes_by_disk_ignores_category_ids_not_in_ftp_categories():
    data = add_folder_bytes({}, "cat-desconocida", "Fantasma", "X", 1000)
    assert bytes_by_disk(data, {"tv": [], "movie": []}) == {}


def test_bytes_by_disk_empty_data():
    assert bytes_by_disk({}, {"tv": [], "movie": []}) == {}


def test_load_local_cache_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("core.category_stats.app_data_dir", lambda: tmp_path)
    assert load_local_cache() == {}


def test_save_and_load_local_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("core.category_stats.app_data_dir", lambda: tmp_path)
    data = add_folder_bytes({}, "cat-tv", "Series", "Stargate Atlantis", 1000)

    save_local_cache(data)

    assert load_local_cache() == data


def test_load_local_cache_returns_empty_dict_on_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.setattr("core.category_stats.app_data_dir", lambda: tmp_path)
    (tmp_path / "category_stats.json").write_text("{esto no es json", encoding="utf-8")
    assert load_local_cache() == {}


def test_wrap_for_remote_round_trips_through_unwrap():
    data = add_folder_bytes({}, "cat-tv", "Series", "Stargate Atlantis", 1000)
    assert unwrap_from_remote(wrap_for_remote(data)) == data


def test_unwrap_from_remote_none_for_old_scan_version():
    # Datos calculados con una versión de escaneo ANTERIOR (p.ej. antes de
    # un fix de conteo) no deben seguir aplicándose tal cual -- deben
    # tratarse como "hace falta un bootstrap nuevo" (ver SCAN_VERSION).
    payload = {"_scan_version": SCAN_VERSION - 1, "data": {"cat-tv": {"category_name": "Series", "folders": {}}}}
    assert unwrap_from_remote(payload) is None


def test_unwrap_from_remote_none_for_bare_dict_without_version_marker():
    # Formato de antes de introducir el versionado -- tampoco es de
    # fiar, mismo criterio que una versión antigua explícita.
    assert unwrap_from_remote({"cat-tv": {"category_name": "Series", "folders": {}}}) is None


def test_unwrap_from_remote_none_for_non_dict_payload():
    assert unwrap_from_remote(None) is None
    assert unwrap_from_remote([]) is None
    assert unwrap_from_remote("no es un dict") is None


def test_unwrap_from_remote_none_when_data_field_is_not_a_dict():
    assert unwrap_from_remote({"_scan_version": SCAN_VERSION, "data": "no es un dict"}) is None
