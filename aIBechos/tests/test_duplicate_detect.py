from core.api_client import MediaInfo
from core.duplicate_detect import find_duplicate


def _tv_info(season=1, episode=6):
    return MediaInfo(tmdb_id=1, media_type="tv", title="Serie", original_title="Serie",
                      year="2024", season=season, episode=episode, genre_ids=[])


def _tv_info_with_title(title, season=1, episode=1):
    return MediaInfo(tmdb_id=999, media_type="tv", title=title, original_title=title,
                     year="2024", season=season, episode=episode, genre_ids=[])


def test_tv_same_episode_number_of_a_sibling_series_is_not_duplicate():
    # Bug real: al subir "Dragon Ball Daima" 1x01 la app decía que ya había
    # otro archivo para el mismo contenido, cuando en el servidor no había
    # ni carpeta de la serie. La resolución de carpeta había fusionado Daima
    # con "Dragon Ball" (prefijo literal) y aquí solo se comparaba S/E, así
    # que el 1x01 ajeno casaba. Series distintas con igual S/E no son
    # duplicado aunque compartan carpeta.
    info = _tv_info_with_title("Dragon Ball Daima", season=1, episode=1)
    existing = ["Dragon Ball 1x01.mkv", "Dragon Ball 1x02.mkv"]
    assert find_duplicate(existing, info,
                          current_filename="Dragon Ball Daima 1x01.mkv") is None


def test_tv_same_episode_same_series_under_different_release_name_still_detected():
    info = _tv_info_with_title("Dragon Ball Daima", season=1, episode=1)
    existing = ["Dragon Ball Daima 1x01 OtroGrupo WEB-DL.mkv"]
    dup = find_duplicate(existing, info,
                         current_filename="Dragon Ball Daima 1x01.mkv")
    assert dup == "Dragon Ball Daima 1x01 OtroGrupo WEB-DL.mkv"


def test_tv_duplicate_with_original_title_in_parentheses_still_detected():
    # La guarda de título no debe romper la reutilización legítima con el
    # título original entre paréntesis (patrón de anotación).
    info = _tv_info_with_title("Desencanto", season=1, episode=3)
    existing = ["Desencanto (Disenchantment) 1x03.mkv"]
    dup = find_duplicate(existing, info,
                         current_filename="Desencanto 1x03.mkv")
    assert dup == "Desencanto (Disenchantment) 1x03.mkv"


def _movie_info():
    return MediaInfo(tmdb_id=1, media_type="movie", title="Pelicula",
                      original_title="Pelicula", year="2024", genre_ids=[])


def test_tv_finds_same_episode_under_different_filename():
    existing = ["Serie.1x06.OtroGrupo.WEB-DL.mkv", "Serie 1x05.mkv"]
    dup = find_duplicate(existing, _tv_info(season=1, episode=6), current_filename="Serie 1x06.mkv")
    assert dup == "Serie.1x06.OtroGrupo.WEB-DL.mkv"


def test_tv_no_duplicate_when_episode_not_present():
    existing = ["Serie 1x01.mkv", "Serie 1x02.mkv"]
    dup = find_duplicate(existing, _tv_info(season=1, episode=6), current_filename="Serie 1x06.mkv")
    assert dup is None


def test_tv_ignores_the_file_being_uploaded_itself():
    existing = ["Serie 1x06.mkv"]
    dup = find_duplicate(existing, _tv_info(season=1, episode=6), current_filename="Serie 1x06.mkv")
    assert dup is None


def test_tv_ignores_non_video_files():
    existing = ["Serie 1x06.srt", "Serie 1x06.nfo"]
    dup = find_duplicate(existing, _tv_info(season=1, episode=6), current_filename="Serie 1x06 Nuevo.mkv")
    assert dup is None


def test_tv_without_season_or_episode_never_flags_duplicate():
    info = _tv_info(season=None, episode=None)
    existing = ["Serie 1x06.mkv"]
    assert find_duplicate(existing, info, current_filename="otro.mkv") is None


def test_movie_finds_same_title_under_different_release_name():
    existing = ["Pelicula.2024.OtraVersion.WEB-DL.mkv"]
    dup = find_duplicate(existing, _movie_info(), current_filename="Pelicula (2024).mkv")
    assert dup == "Pelicula.2024.OtraVersion.WEB-DL.mkv"


def test_movie_does_not_flag_different_movie_sharing_the_same_folder():
    # Carpeta compartida por varios títulos (colección numerada, por
    # ejemplo) -- antes se asumía que cualquier otro vídeo era el mismo
    # contenido, dando falsos positivos. Ver core/duplicate_detect.py.
    info = MediaInfo(tmdb_id=1, media_type="movie", title="Daniel El Travieso",
                      original_title="Dennis the Menace", year="1993", genre_ids=[])
    existing = ["0519-La Sirenita (1989).mkv"]
    dup = find_duplicate(existing, info, current_filename="0520-Daniel El Travieso (1993).mkv")
    assert dup is None


def test_movie_with_short_title_does_not_false_positive_on_unrelated_movie():
    # Bug real: "El 47" se normaliza a "47" al quitarle el artículo "El" --
    # una cadena de 2 caracteres que aparecía como subcadena de cualquier
    # otro título con un "47" en cualquier parte (un año, un número...),
    # marcando dos películas sin ninguna relación como "el mismo contenido".
    info = MediaInfo(tmdb_id=1, media_type="movie", title="El 47",
                      original_title="El 47", year="2024", genre_ids=[])
    existing = ["Otra Pelicula Cualquiera (1947).mkv"]
    dup = find_duplicate(existing, info, current_filename="El 47 (2024).mkv")
    assert dup is None


def test_movie_ignores_non_video_files_like_posters():
    existing = ["poster.jpg", "Pelicula.nfo"]
    dup = find_duplicate(existing, _movie_info(), current_filename="Pelicula (2024).mkv")
    assert dup is None


def test_movie_no_duplicate_when_folder_empty():
    assert find_duplicate([], _movie_info(), current_filename="Pelicula (2024).mkv") is None


def test_movie_same_title_different_year_is_not_duplicate():
    # Bug real (modo automático): "Toy Story 5" (2026) se omitía porque se
    # encontraba "Toy Story (1995).avi" en la carpeta -- mismo nombre base,
    # distinta película. detect_episode le quita el año al título, así que
    # el desempate por año tiene que mirar el nombre de archivo crudo.
    info = MediaInfo(tmdb_id=1, media_type="movie", title="Toy Story 5",
                      original_title="Toy Story 5", year="2026", genre_ids=[])
    existing = ["Toy Story (1995).avi", "Toy Story 2 (1999).mkv"]
    dup = find_duplicate(existing, info, current_filename="Toy Story 5 (2026).mkv")
    assert dup is None


def test_movie_same_base_title_other_franchise_is_not_duplicate():
    # Bug real: "Minions and Monsters" (2026) se omitía por "Los Minions
    # (2015).mkv" -- título base parecido, película distinta.
    info = MediaInfo(tmdb_id=1, media_type="movie", title="Minions and Monsters",
                      original_title="Minions and Monsters", year="2026", genre_ids=[])
    existing = ["Los Minions (2015).mkv"]
    dup = find_duplicate(existing, info, current_filename="Minions and Monsters (2026).mkv")
    assert dup is None


def test_movie_same_title_and_year_is_duplicate():
    # El desempate por año no debe romper la detección legítima: mismo
    # título base y mismo año = mismo contenido, aunque el nombre de
    # archivo sea de otro release.
    info = MediaInfo(tmdb_id=1, media_type="movie", title="Toy Story 5",
                      original_title="Toy Story 5", year="2026", genre_ids=[])
    existing = ["Toy Story.5.2026.2160p.WEB-DL.mkv"]
    dup = find_duplicate(existing, info, current_filename="Toy Story 5 (2026).mkv")
    assert dup == "Toy Story.5.2026.2160p.WEB-DL.mkv"


def test_movie_without_year_in_existing_name_still_detected():
    # Si el archivo existente no trae año, no se puede descartar por ese
    # criterio y se mantiene la comparación por título.
    info = MediaInfo(tmdb_id=1, media_type="movie", title="Pelicula",
                      original_title="Pelicula", year="2024", genre_ids=[])
    existing = ["Pelicula.OtraVersion.WEB-DL.mkv"]
    dup = find_duplicate(existing, info, current_filename="Pelicula (2024).mkv")
    assert dup == "Pelicula.OtraVersion.WEB-DL.mkv"
