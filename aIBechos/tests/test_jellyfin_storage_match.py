from core.jellyfin_storage_match import match_root_to_folder


def test_matches_real_world_case():
    folders = [
        {"Path": "/home/administrador/datos", "FreeSpace": 100},
        {"Path": "/home/administrador/datos2/series", "FreeSpace": 5000},
        {"Path": "/home/administrador/datos2/seriespeques", "FreeSpace": 200},
    ]
    match = match_root_to_folder("/datos2/series/", folders)
    assert match is not None
    assert match["FreeSpace"] == 5000


def test_no_match_when_no_folder_ends_with_root_segments():
    folders = [{"Path": "/home/administrador/datos/peliculas", "FreeSpace": 100}]
    assert match_root_to_folder("/datos2/series/", folders) is None


def test_case_insensitive_match():
    folders = [{"Path": "/Home/Administrador/DATOS2/Series", "FreeSpace": 42}]
    match = match_root_to_folder("/datos2/series/", folders)
    assert match is not None and match["FreeSpace"] == 42


def test_avoids_partial_segment_false_positive():
    # "eries" no debe confundirse con "series" solo por terminar en las mismas letras
    folders = [{"Path": "/home/datos2/moreries", "FreeSpace": 999}]
    assert match_root_to_folder("/datos2/series/", folders) is None


def test_empty_category_root_returns_none():
    folders = [{"Path": "/home/administrador/datos2/series", "FreeSpace": 5000}]
    assert match_root_to_folder("", folders) is None
    assert match_root_to_folder("/", folders) is None


def test_empty_folder_list_returns_none():
    assert match_root_to_folder("/datos2/series/", []) is None
    assert match_root_to_folder("/datos2/series/", None) is None


def test_matches_single_segment_root():
    folders = [
        {"Path": "/home/administrador/datos/peliculas", "FreeSpace": 100},
        {"Path": "/home/administrador/datos2/series", "FreeSpace": 5000},
    ]
    match = match_root_to_folder("/peliculas", folders)
    assert match is not None and match["FreeSpace"] == 100


def test_folder_missing_path_key_is_skipped_not_crashed():
    folders = [{"FreeSpace": 100}, {"Path": "/home/datos2/series", "FreeSpace": 5000}]
    match = match_root_to_folder("/datos2/series/", folders)
    assert match is not None and match["FreeSpace"] == 5000
