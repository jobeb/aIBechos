from core.season_grouping import classify_series_files, season_folder_name


def test_season_folder_name_pads_to_two_digits():
    assert season_folder_name(1) == "Temporada 01"
    assert season_folder_name(12) == "Temporada 12"


def test_single_season_loose_files_go_to_temporada_01():
    files = [
        ("", "Breaking Bad 1x01 Pilot.mkv"),
        ("", "Breaking Bad 1x02 Cat's in the Bag.mkv"),
    ]
    plan = classify_series_files(files)
    assert len(plan.moves) == 2
    assert all(m.target_relative_folder == "Temporada 01" for m in plan.moves)
    assert plan.already_correct == []
    assert plan.unclassified == []


def test_anime_absolute_numbering_goes_to_single_season_folder():
    """detect_episode() siempre da season=1 para numeracion absoluta --
    por eso el anime termina en una sola "Temporada 01/", nunca suelto."""
    files = [
        ("", "One Piece Ep1078.mkv"),
        ("", "One Piece Ep1079.mkv"),
    ]
    plan = classify_series_files(files)
    assert len(plan.moves) == 2
    assert all(m.target_relative_folder == "Temporada 01" for m in plan.moves)


def test_multiple_seasons_go_to_their_own_folder():
    files = [
        ("", "Breaking Bad 1x01 Pilot.mkv"),
        ("", "Breaking Bad 3x07 One Minute.mkv"),
        ("Temporada 02", "Breaking Bad 2x01 Seven Thirty-Seven.mkv"),
    ]
    plan = classify_series_files(files)
    targets = {m.filename: m.target_relative_folder for m in plan.moves + plan.already_correct}
    assert targets["Breaking Bad 1x01 Pilot.mkv"] == "Temporada 01"
    assert targets["Breaking Bad 3x07 One Minute.mkv"] == "Temporada 03"
    assert targets["Breaking Bad 2x01 Seven Thirty-Seven.mkv"] == "Temporada 02"


def test_file_already_in_correct_season_folder_is_not_a_move():
    files = [("Temporada 01", "Breaking Bad 1x01 Pilot.mkv")]
    plan = classify_series_files(files)
    assert plan.moves == []
    assert len(plan.already_correct) == 1


def test_non_canonical_season_folder_name_still_needs_a_move():
    """"Temporada 1" (sin cero) no es la carpeta canonica "Temporada 01"
    -- debe normalizarse tambien, no solo mover lo suelto."""
    files = [("Temporada 1", "Breaking Bad 1x01 Pilot.mkv")]
    plan = classify_series_files(files)
    assert len(plan.moves) == 1
    assert plan.moves[0].target_relative_folder == "Temporada 01"


def test_folder_comparison_is_case_insensitive():
    files = [("temporada 01", "Breaking Bad 1x01 Pilot.mkv")]
    plan = classify_series_files(files)
    assert plan.moves == []
    assert len(plan.already_correct) == 1


def test_file_without_recognizable_episode_pattern_is_unclassified():
    files = [("", "The Dark Knight (2008).mkv")]   # pelicula suelta por error
    plan = classify_series_files(files)
    assert plan.moves == []
    assert plan.already_correct == []
    assert plan.unclassified == [("", "The Dark Knight (2008).mkv")]


def test_companion_files_follow_their_video_season():
    files = [
        ("", "Breaking Bad 3x07 One Minute.mkv"),
        ("", "Breaking Bad 3x07 One Minute-poster.jpg"),
        ("", "Breaking Bad 3x07 One Minute.nfo"),
    ]
    plan = classify_series_files(files)
    assert len(plan.moves) == 3
    assert all(m.target_relative_folder == "Temporada 03" for m in plan.moves)


def test_orphan_companion_without_video_is_unclassified():
    files = [("", "Breaking Bad 3x07 One Minute-poster.jpg")]
    plan = classify_series_files(files)
    assert plan.moves == []
    assert plan.unclassified == [("", "Breaking Bad 3x07 One Minute-poster.jpg")]


def test_fallback_bare_number_filename():
    # "1.mp4" / "100.mp4": el nombre ES el numero, sin nada mas alrededor
    # -- detect_episode() no lo reconoce (exige separador a ambos lados),
    # pero aqui ya sabemos que esta dentro de una carpeta de serie de TV.
    files = [("", "1.mp4"), ("", "100.mp4")]
    plan = classify_series_files(files)
    assert len(plan.moves) == 2
    assert all(m.target_relative_folder == "Temporada 01" for m in plan.moves)


def test_fallback_number_at_start_of_name():
    files = [("", "001 Sailor Moon - La nina llorona.mp4")]
    plan = classify_series_files(files)
    assert len(plan.moves) == 1
    assert plan.moves[0].target_relative_folder == "Temporada 01"


def test_fallback_dot_separated_number():
    files = [("", "Shin.Chan.372.-A cousa animase en Australia.mkv")]
    plan = classify_series_files(files)
    assert len(plan.moves) == 1
    assert plan.moves[0].target_relative_folder == "Temporada 01"


def test_fallback_underscore_separated_number():
    files = [("", "Naruto_006_Una_Mision_Peligrosa.mp4")]
    plan = classify_series_files(files)
    assert len(plan.moves) == 1
    assert plan.moves[0].target_relative_folder == "Temporada 01"


def test_fallback_ignores_number_inside_parens_when_real_episode_present():
    # "(2011)" es parte del titulo (año), "001" es el episodio real -- no
    # debe confundirse el uno con el otro (aunque aqui daria igual, ambos
    # producirian season=1 de todas formas).
    files = [("", "Hunter x Hunter (2011).001.mkv")]
    plan = classify_series_files(files)
    assert len(plan.moves) == 1
    assert plan.moves[0].target_relative_folder == "Temporada 01"


def test_fallback_does_not_rescue_files_with_no_number_at_all():
    files = [("", "Serie Sin Ningun Numero De Episodio.mkv")]
    plan = classify_series_files(files)
    assert plan.moves == []
    assert plan.unclassified == [("", "Serie Sin Ningun Numero De Episodio.mkv")]


def test_empty_input_returns_empty_plan():
    plan = classify_series_files([])
    assert plan.moves == []
    assert plan.already_correct == []
    assert plan.unclassified == []
