import pytest

from core.api_client import MediaInfo
from core.renamer import (
    DEFAULT_ANIME_TEMPLATE,
    DEFAULT_LIBRO_TEMPLATE,
    DEFAULT_MOVIE_TEMPLATE,
    DEFAULT_TV_TEMPLATE,
    build_new_name,
    is_archive_file,
    is_book_file,
    is_comic_file,
    is_video_file,
    rename_file,
)


def _tv_info(**overrides):
    base = dict(
        tmdb_id=1, media_type="tv", title="Breaking Bad", original_title="Breaking Bad",
        year="2008", season=3, episode=7, episode_title="One Minute",
    )
    base.update(overrides)
    return MediaInfo(**base)


def test_build_new_name_tv():
    name = build_new_name(_tv_info(), DEFAULT_TV_TEMPLATE, ".mkv")
    assert name == "Breaking Bad 3x07 One Minute.mkv"


def test_build_new_name_movie():
    info = MediaInfo(
        tmdb_id=2, media_type="movie", title="The Dark Knight",
        original_title="The Dark Knight", year="2008",
    )
    name = build_new_name(info, DEFAULT_MOVIE_TEMPLATE, ".mkv")
    assert name == "The Dark Knight (2008).mkv"


def test_build_new_name_anime_pads_episode_to_three_digits():
    info = _tv_info(title="One Piece", episode_title="The New Era", season=1, episode=1078)
    name = build_new_name(info, DEFAULT_ANIME_TEMPLATE, ".mkv")
    assert name == "One Piece 1x1078 The New Era.mkv"


def test_build_new_name_collapses_space_when_title_empty():
    info = _tv_info(episode_title="")
    name = build_new_name(info, DEFAULT_TV_TEMPLATE, ".mkv")
    assert name == "Breaking Bad 3x07.mkv"


def test_build_new_name_defaults_missing_season_episode_to_one():
    info = _tv_info(season=None, episode=None)
    name = build_new_name(info, DEFAULT_TV_TEMPLATE, ".mkv")
    assert name == "Breaking Bad 1x01 One Minute.mkv"


def test_build_new_name_invalid_template_raises():
    with pytest.raises(ValueError):
        build_new_name(_tv_info(), "{campo_inexistente}", ".mkv")


def test_build_new_name_sanitizes_invalid_filesystem_chars():
    info = _tv_info(title='Series: The "Return"?', episode_title="Part <1>")
    name = build_new_name(info, DEFAULT_TV_TEMPLATE, ".mkv")
    assert not any(c in name for c in '<>:"/\\|?*')


def test_build_new_name_normalizes_extension_without_dot():
    name = build_new_name(_tv_info(), DEFAULT_TV_TEMPLATE, "mkv")
    assert name.endswith(".mkv")


def test_rename_file_success(tmp_path):
    src = tmp_path / "old_name.mkv"
    src.write_text("contenido")
    ok, result = rename_file(str(src), "Breaking Bad 3x07 One Minute.mkv")
    assert ok is True
    assert not src.exists()
    assert (tmp_path / "Breaking Bad 3x07 One Minute.mkv").exists()
    assert result == str(tmp_path / "Breaking Bad 3x07 One Minute.mkv")


def test_rename_file_missing_source(tmp_path):
    missing = tmp_path / "no_existe.mkv"
    ok, msg = rename_file(str(missing), "nuevo.mkv")
    assert ok is False
    assert "no encontrado" in msg.lower()


def test_rename_file_refuses_overwrite_existing_destination(tmp_path):
    src = tmp_path / "origen.mkv"
    dst = tmp_path / "destino.mkv"
    src.write_text("a")
    dst.write_text("b")
    ok, msg = rename_file(str(src), "destino.mkv")
    assert ok is False
    assert "ya existe" in msg.lower()
    assert src.exists()
    assert dst.read_text() == "b"


def test_rename_file_force_overwrite_replaces_existing_destination(tmp_path):
    src = tmp_path / "origen.mkv"
    dst = tmp_path / "destino.mkv"
    src.write_text("a")
    dst.write_text("b")
    ok, result = rename_file(str(src), "destino.mkv", force_overwrite=True)
    assert ok is True
    assert not src.exists()
    assert dst.exists()
    assert dst.read_text() == "a"   # el contenido del destino es el del ORIGEN
    assert result == str(dst)


def test_rename_file_dry_run_does_not_touch_disk(tmp_path):
    src = tmp_path / "origen.mkv"
    src.write_text("a")
    ok, msg = rename_file(str(src), "destino.mkv", dry_run=True)
    assert ok is True
    assert "[Simulación]" in msg
    assert src.exists()
    assert not (tmp_path / "destino.mkv").exists()


@pytest.mark.parametrize("ext", [".mkv", ".mp4", ".avi", ".mov", ".webm", ".MKV"])
def test_is_video_file_recognizes_known_extensions(ext):
    assert is_video_file(f"pelicula{ext}") is True


@pytest.mark.parametrize("ext", [".srt", ".ass", ".txt", ".nfo", ".jpg"])
def test_is_video_file_rejects_non_video_extensions(ext):
    assert is_video_file(f"archivo{ext}") is False


@pytest.mark.parametrize("ext", [".pdf", ".epub", ".mobi", ".azw3", ".cbz", ".cbr", ".PDF"])
def test_is_book_file_recognizes_known_extensions(ext):
    assert is_book_file(f"libro{ext}") is True


@pytest.mark.parametrize("ext", [".mkv", ".srt", ".jpg"])
def test_is_book_file_rejects_non_book_extensions(ext):
    assert is_book_file(f"archivo{ext}") is False


@pytest.mark.parametrize("ext", [".cbz", ".cbr"])
def test_is_comic_file_recognizes_comic_extensions(ext):
    assert is_comic_file(f"comic{ext}") is True


@pytest.mark.parametrize("ext", [".pdf", ".epub", ".mobi", ".azw3"])
def test_is_comic_file_rejects_plain_ebook_extensions(ext):
    # Un ebook de texto es "libro" pero NO "cómic" -- is_book_file() es
    # True para ambos, is_comic_file() distingue el subcaso.
    assert is_comic_file(f"libro{ext}") is False


def test_build_new_name_libro_default_template():
    info = MediaInfo(
        tmdb_id="zyTCAlFPjgYC", media_type="libro", title="El Nombre del Viento",
        original_title="El Nombre del Viento", year="2007",
    )
    name = build_new_name(info, DEFAULT_LIBRO_TEMPLATE, ".epub")
    assert name == "El Nombre del Viento.epub"


def test_build_new_name_comic_with_issue_number():
    info = MediaInfo(
        tmdb_id="4050-12345", media_type="libro",
        title="Avatar - The Last Airbender - The Promise",
        original_title="Avatar - The Last Airbender - The Promise",
        year="2012", episode=1, genre_ids=["comic"],
    )
    name = build_new_name(info, "{serie} ({año}) #{episodio:02d}{ext}", ".cbr")
    assert name == "Avatar - The Last Airbender - The Promise (2012) #01.cbr"


@pytest.mark.parametrize("ext", [".zip", ".7z", ".rar", ".tar", ".tgz", ".tbz2", ".txz", ".ZIP"])
def test_is_archive_file_recognizes_single_suffix_extensions(ext):
    assert is_archive_file(f"comprimido{ext}") is True


@pytest.mark.parametrize("compound", [".tar.gz", ".tar.bz2", ".tar.xz", ".TAR.GZ"])
def test_is_archive_file_recognizes_compound_tar_suffixes(compound):
    # Path.suffix (get_extension) solo ve el último punto (".gz"), así que
    # is_archive_file tiene que comprobar también los dos últimos sufijos
    # juntos para reconocer estas variantes compuestas.
    assert is_archive_file(f"Mi.Comic.Favorito{compound}") is True


@pytest.mark.parametrize("ext", [".mkv", ".pdf", ".cbz", ".txt", ".gz"])
def test_is_archive_file_rejects_non_archive_extensions(ext):
    # ".gz" suelto (sin ".tar" delante) no es un archivo comprimido que
    # sepamos descomprimir por sí solo (sería un único archivo comprimido,
    # no un contenedor) -- solo cuenta la forma compuesta ".tar.gz".
    assert is_archive_file(f"archivo{ext}") is False
