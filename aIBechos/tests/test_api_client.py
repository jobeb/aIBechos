import pytest

import core.api_client as api_client_mod
from core.api_client import detect_episode, TMDBClient


def test_detect_series_standard_sxxexx():
    r = detect_episode("Breaking.Bad.S03E07.1080p.BluRay.x264.mkv")
    assert r["title"] == "Breaking Bad"
    assert r["season"] == 3
    assert r["episode"] == 7
    assert r["media_type"] == "tv"


def test_detect_series_1x01_format():
    r = detect_episode("The Office 4x12 [HDTV].avi")
    assert r["title"] == "The Office"
    assert r["season"] == 4
    assert r["episode"] == 12
    assert r["media_type"] == "tv"


def test_detect_series_1x01_format_uppercase_x():
    # Algunos grupos de release usan la "X" en mayúscula ("2X01" en vez de
    # "2x01") -- caso real: "Arcadia.2X01...", que sin esto se detectaba
    # como película (sin temporada/episodio) al no coincidir el patrón.
    r = detect_episode("Arcadia.2X01.El.Guardian.WEB-DL.1080p.mkv")
    assert r["title"] == "Arcadia"
    assert r["season"] == 2
    assert r["episode"] == 1
    assert r["media_type"] == "tv"


def test_detect_anime_episode_word():
    r = detect_episode("One Piece Episode 1078.mkv")
    assert r["title"] == "One Piece"
    assert r["season"] == 1
    assert r["episode"] == 1078
    assert r["media_type"] == "anime"


def test_detect_anime_ep_abbrev():
    r = detect_episode("Naruto Shippuden Ep.05.mkv")
    assert r["title"] == "Naruto Shippuden"
    assert r["season"] == 1
    assert r["episode"] == 5
    assert r["media_type"] == "anime"


def test_detect_anime_cap_spanish():
    r = detect_episode("Dragon Ball Super Capitulo 12.mp4")
    assert r["title"] == "Dragon Ball Super"
    assert r["season"] == 1
    assert r["episode"] == 12
    assert r["media_type"] == "anime"


def test_detect_anime_isolated_number_at_end_of_name():
    # "NSCast - 320.mp4": el número va pegado al final del nombre (sin
    # separador tras él, solo la extensión) -- caso real visto en release
    # groups de anime que antes se quedaba sin season/episode reconocido.
    r = detect_episode("NSCast - 320.mp4")
    assert r["season"] == 1
    assert r["episode"] == 320
    assert r["media_type"] == "anime"


def test_detect_movie_fallback_strips_year():
    r = detect_episode("The.Dark.Knight.2008.1080p.BluRay.x264.mkv")
    assert r["title"] == "The Dark Knight"
    assert r["season"] is None
    assert r["episode"] is None
    assert r["media_type"] == "movie"


def test_detect_cleans_junk_tokens():
    r = detect_episode("Inception (2010) [1080p] WEB-DL x265 AAC.mkv")
    assert r["title"] == "Inception"
    assert r["media_type"] == "movie"


def test_detect_strips_bare_hd_quality_tag():
    # Caso real: "Shelter, El Protector HD.mkv" se buscaba en TMDB como
    # "Shelter, El Protector Hd" porque "HD" a secas no estaba en los
    # marcadores de ruido (solo HDTV/HDRip lo estaban) y no encontraba nada.
    r = detect_episode("Shelter, El Protector HD.mkv")
    assert r["title"] == "Shelter, El Protector"
    assert r["media_type"] == "movie"
    r2 = detect_episode("Shelter, El Protector HD 1080p x264.mkv")
    assert r2["title"] == "Shelter, El Protector"


def test_detect_strips_bdrip_and_uploader_credit():
    r = detect_episode(
        "The.Brutalist.(2024).(Spanish.English.Subs).BDRip.1080p.x264-AC3.by.xusman.(nocturniap2p).mkv")
    assert r["title"] == "The Brutalist"
    assert r["media_type"] == "movie"


def test_detect_strips_trailing_uploader_credit_only_at_end():
    # "by" al final tras limpiar todo lo demas se quita; en medio del titulo se respeta
    r = detect_episode("Catch.Me.If.You.Can.2002.BDRip.1080p.x264.by.someuploader.mkv")
    assert r["title"] == "Catch Me If You Can"


def test_detect_truncates_at_first_junk_marker_even_if_unlisted_credit_follows(monkeypatch, tmp_path):
    # "Kowalski&Xusman" y "Nocturniap2p" no estan en ninguna lista de basura
    # conocida, pero como aparecen DESPUES de "Eac3"/"Castellano" (que si lo
    # estan), se descartan igualmente por venir despues del primer indicio
    # tecnico -- no hace falta reconocer cada credito de subida posible.
    # Aislado de los términos aprendidos reales del usuario (ver
    # test_detect_picks_up_persisted_learned_terms) -- sin esto, un término
    # aprendido de verdad tan corto como "LA" (visto en la práctica) corta
    # "La Residencia" entera por el propio título, no por ningún crédito.
    import core.learned_terms as lt
    monkeypatch.setattr(lt, "app_data_dir", lambda: tmp_path)
    lt._reset_cache_for_tests()

    r = detect_episode(
        "La.Residencia.Eac3.Castellano.Frances+Forzados+Completos."
        "Kowalski&Xusman.Para.Nocturniap2p.mkv")
    assert r["title"] == "La Residencia"
    assert r["media_type"] == "movie"


def test_detect_eac3_recognized_not_just_ac3():
    # "EAC3" no matchea \bAC3\b (no hay limite de palabra entre la E y la A)
    r = detect_episode("Movie.Name.2020.WEB-DL.EAC3.mkv")
    assert r["title"] == "Movie Name"


def test_detect_strips_bit_depth_marker():
    # "10Bit"/"8-bit" (profundidad de color) no estaba en ninguna lista
    r = detect_episode("El.Abismo.Secreto.10Bit.WEB-DL.1080p.mkv")
    assert r["title"] == "El Abismo Secreto"


def test_detect_strips_webdl_without_hyphen():
    # "WEB-DL" exigia el guion literal -- "Webdl"/"WEBDL" sin guion no se reconocia
    r = detect_episode("Balls.Up.Webdl.mkv")
    assert r["title"] == "Balls Up"


def test_detect_uses_extra_junk_terms_candidate(monkeypatch, tmp_path):
    # "DSNYP" no esta en ninguna lista (ni estatica ni aprendida) -- sin
    # pasarlo como candidato, se queda pegado al titulo.
    import core.learned_terms as lt
    monkeypatch.setattr(lt, "app_data_dir", lambda: tmp_path)
    lt._reset_cache_for_tests()

    r_sin = detect_episode("Cazadores De Sombras DSNYP HEVC.mkv")
    assert "dsnyp" in r_sin["title"].lower()

    r_con = detect_episode("Cazadores De Sombras DSNYP HEVC.mkv", extra_junk_terms=["DSNYP"])
    assert r_con["title"] == "Cazadores De Sombras"


def test_detect_picks_up_persisted_learned_terms(monkeypatch, tmp_path):
    # Una vez aprendido (persistido), detect_episode lo reconoce solo, sin
    # necesidad de volver a pasarlo como extra_junk_terms cada vez.
    import core.learned_terms as lt
    monkeypatch.setattr(lt, "app_data_dir", lambda: tmp_path)
    lt._reset_cache_for_tests()
    lt.add_learned_terms(["DSNYP"])

    r = detect_episode("Cazadores De Sombras DSNYP HEVC.mkv")
    assert r["title"] == "Cazadores De Sombras"


def test_detect_strips_spanish_language_tags():
    r = detect_episode("Alguna Serie 1x05 Castellano Latino VOSE.mkv")
    assert r["title"] == "Alguna Serie"
    assert r["season"] == 1
    assert r["episode"] == 5


def test_detect_junk_marker_inside_parens_cuts_before_the_paren_not_mid_group():
    # Si el termino de basura cae DENTRO de un parentesis/corchete sin
    # cerrar, el corte debe hacerse antes de abrirlo, no a la mitad (si no,
    # queda un "(" o "[" suelto en el titulo).
    r = detect_episode("Titulo Pelicula (2019) (Spanish English Subs) BDRip.mkv")
    assert r["title"] == "Titulo Pelicula"
    assert "(" not in r["title"] and "[" not in r["title"]


def test_detect_empty_filename_returns_movie_dict():
    r = detect_episode("")
    assert r["media_type"] == "movie"
    assert r["season"] is None
    assert r["episode"] is None


def test_build_media_info_populates_genre_ids_from_search_result():
    client = TMDBClient(api_key="dummy")
    result = {
        "id": 1396,
        "media_type": "tv",
        "name": "Breaking Bad",
        "first_air_date": "2008-01-20",
        "genre_ids": [18, 80],
    }
    info = client.build_media_info(result, season=None, episode=None)
    assert info.genre_ids == [18, 80]


def test_build_media_info_genre_ids_defaults_to_empty_list():
    client = TMDBClient(api_key="dummy")
    result = {"id": 1, "media_type": "movie", "title": "X", "release_date": "2020-01-01"}
    info = client.build_media_info(result, season=None, episode=None)
    assert info.genre_ids == []


class _FakeTmdbResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.headers = {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_get_season_episodes_returns_episode_numbers_and_names(monkeypatch):
    client = TMDBClient(api_key="dummy")
    payload = {"episodes": [
        {"episode_number": 1, "name": "Piloto", "air_date": "2020-01-01"},
        {"episode_number": 2, "name": "Segundo episodio", "air_date": "2020-01-08"},
    ]}
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeTmdbResponse(payload))
    episodes = client.get_season_episodes(1396, 1)
    assert episodes == [
        {"episode_number": 1, "name": "Piloto", "air_date": "2020-01-01"},
        {"episode_number": 2, "name": "Segundo episodio", "air_date": "2020-01-08"},
    ]


def test_get_season_episodes_excludes_unaired_episodes(monkeypatch):
    # TMDB a veces ya lista episodios de una temporada anunciada/en emisión
    # que aún no han salido -- no deben contar como "que faltan".
    client = TMDBClient(api_key="dummy")
    payload = {"episodes": [
        {"episode_number": 1, "name": "Ya emitido", "air_date": "2020-01-01"},
        {"episode_number": 2, "name": "Fecha futura", "air_date": "2099-01-01"},
        {"episode_number": 3, "name": "Sin fecha todavia", "air_date": None},
        {"episode_number": 4, "name": "Sin campo air_date"},
    ]}
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeTmdbResponse(payload))
    episodes = client.get_season_episodes(1396, 1)
    assert episodes == [{"episode_number": 1, "name": "Ya emitido", "air_date": "2020-01-01"}]


def test_get_movie_certification_uses_primary_region(monkeypatch):
    client = TMDBClient(api_key="dummy")
    payload = {"results": [
        {"iso_3166_1": "US", "release_dates": [{"certification": "PG-13"}]},
        {"iso_3166_1": "ES", "release_dates": [{"certification": "12"}]},
    ]}
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeTmdbResponse(payload))
    assert client.get_movie_certification(123) == "12"


def test_get_movie_certification_falls_back_to_secondary_region(monkeypatch):
    client = TMDBClient(api_key="dummy")
    payload = {"results": [
        {"iso_3166_1": "US", "release_dates": [{"certification": "PG-13"}]},
        {"iso_3166_1": "ES", "release_dates": [{"certification": ""}]},
    ]}
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeTmdbResponse(payload))
    assert client.get_movie_certification(123) == "PG-13"


def test_get_movie_certification_no_cert_returns_empty(monkeypatch):
    client = TMDBClient(api_key="dummy")
    payload = {"results": [
        {"iso_3166_1": "US", "release_dates": [{"certification": ""}]},
        {"iso_3166_1": "ES", "release_dates": [{"certification": ""}]},
    ]}
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeTmdbResponse(payload))
    assert client.get_movie_certification(123) == ""


def test_get_movie_certification_no_release_dates_returns_empty(monkeypatch):
    client = TMDBClient(api_key="dummy")
    payload = {"results": [{"iso_3166_1": "ES", "release_dates": []}]}
    monkeypatch.setattr(client.session, "get", lambda url, **kw: _FakeTmdbResponse(payload))
    assert client.get_movie_certification(123) == ""


# ── Límite de peticiones a TMDB (~40/10s por IP) ────────────────────────

def _fake_clock(monkeypatch):
    """Reloj falso controlable a mano -- avanza solo cuando se llama a
    time.sleep(), para poder probar el throttle sin esperar de verdad ni
    caer en un bucle infinito (el fake de time.sleep no hace pasar el
    tiempo real por sí solo)."""
    now = [1000.0]
    monkeypatch.setattr(api_client_mod.time, "monotonic", lambda: now[0])
    sleeps = []
    def _sleep(s):
        sleeps.append(s)
        now[0] += s
    monkeypatch.setattr(api_client_mod.time, "sleep", _sleep)
    return sleeps


def test_throttle_allows_up_to_the_limit_without_waiting(monkeypatch):
    client = TMDBClient(api_key="dummy")
    sleeps = _fake_clock(monkeypatch)
    for _ in range(client._MAX_REQUESTS_PER_WINDOW):
        client._throttle()
    assert sleeps == []


def test_throttle_waits_once_the_limit_is_exceeded(monkeypatch):
    client = TMDBClient(api_key="dummy")
    sleeps = _fake_clock(monkeypatch)
    for _ in range(client._MAX_REQUESTS_PER_WINDOW):
        client._throttle()
    client._throttle()   # el siguiente ya supera el limite -- deberia esperar
    assert len(sleeps) == 1
    assert sleeps[0] > 0


def test_get_retries_on_429_then_succeeds(monkeypatch):
    client = TMDBClient(api_key="dummy")
    monkeypatch.setattr(api_client_mod.time, "sleep", lambda s: None)
    responses = [_FakeTmdbResponse({}, status_code=429),
                 _FakeTmdbResponse({"ok": True}, status_code=200)]
    calls = []
    def _fake_get(url, **kw):
        calls.append(url)
        return responses.pop(0)
    monkeypatch.setattr(client.session, "get", _fake_get)

    result = client._get("/tv/1")
    assert result == {"ok": True}
    assert len(calls) == 2


def test_get_gives_up_after_max_429_retries(monkeypatch):
    client = TMDBClient(api_key="dummy")
    monkeypatch.setattr(api_client_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(client.session, "get",
                        lambda url, **kw: _FakeTmdbResponse({}, status_code=429))
    with pytest.raises(RuntimeError):
        client._get("/tv/1")


# ── Episodios dobles (mismo archivo con dos episodios empaquetados) ──

def test_detect_double_episode_nxnn_hyphen():
    # Caso real reportado: "Stargate SG-1 7x21-7x22 La ciudad perdida
    # [DVDRip][Spanish Divx][cifirip][By Darkseid].avi" -- el episodio 22
    # se quedaba sin reconocer, y el cruce con el FTP lo marcaba como hueco
    # real aunque el archivo estuviera ahí.
    r = detect_episode("Stargate SG-1 7x21-7x22 La ciudad perdida "
                        "[DVDRip][Spanish Divx][cifirip][By Darkseid].avi")
    assert r["season"] == 7
    assert r["episode"] == 21
    assert r["extra_episodes"] == [22]
    assert r["media_type"] == "tv"


def test_detect_double_episode_nxnn_space():
    # Segundo caso real reportado, separado por espacio en vez de guion.
    r = detect_episode("Stargate SG1 - 8x01 8x02 - Un nuevo orden "
                        "[DVD+SCL][Spanish Xvid][cifirip].avi")
    assert r["season"] == 8
    assert r["episode"] == 1
    assert r["extra_episodes"] == [2]


def test_detect_double_episode_sxxexx_chained():
    r = detect_episode("Serie.S07E21E22.Titulo.mkv")
    assert r["season"] == 7
    assert r["episode"] == 21
    assert r["extra_episodes"] == [22]


def test_detect_double_episode_sxxexx_repeated():
    r = detect_episode("Serie.S07E21-S07E22.Titulo.mkv")
    assert r["season"] == 7
    assert r["episode"] == 21
    assert r["extra_episodes"] == [22]


def test_detect_double_episode_nxnn_compact_range():
    # Caso real reportado (segunda vez): "Stargate Atlantis 1x01-02
    # Emergiendo (Spa-Eng-Sub) Hd Ia Us 1080P Hevc 10B-Aac By Geot.mkv" --
    # rango compacto SIN temporada repetida (a diferencia de "7x21-7x22").
    # Solo se acepta porque "02" es consecutivo a "01" (episodio+1).
    r = detect_episode(
        "Stargate Atlantis 1x01-02 Emergiendo (Spa-Eng-Sub) Hd Ia Us "
        "1080P Hevc 10B-Aac By Geot.mkv")
    assert r["season"] == 1
    assert r["episode"] == 1
    assert r["extra_episodes"] == [2]


def test_detect_double_episode_sxxexx_compact_range():
    r = detect_episode("Serie.S01E01-02.Titulo.mkv")
    assert r["season"] == 1
    assert r["episode"] == 1
    assert r["extra_episodes"] == [2]


def test_detect_single_episode_has_empty_extra_episodes():
    r = detect_episode("Breaking.Bad.S03E07.1080p.BluRay.x264.mkv")
    assert r["extra_episodes"] == []


def test_detect_episode_does_not_confuse_year_with_double_episode():
    # Sin un segundo indicador de episodio (otra "x"/"E"), un número justo
    # después no debe tratarse como segundo episodio -- aquí "2019" es un
    # año, no un episodio doble.
    r = detect_episode("Serie 1x05 2019 1080p.mkv")
    assert r["season"] == 1
    assert r["episode"] == 5
    assert r["extra_episodes"] == []


def test_detect_episode_does_not_confuse_resolution_with_double_episode():
    # "720"/"1080" pegados tras un guion tampoco deben confundirse con un
    # segundo episodio -- ninguno de los dos es consecutivo al episodio 5
    # (episodio+1 = 6), que es lo que de verdad los distingue de un rango
    # compacto real como "1x05-06".
    r = detect_episode("Serie 1x05-720p.mkv")
    assert r["extra_episodes"] == []
    r2 = detect_episode("Serie 1x05-1080p.mkv")
    assert r2["extra_episodes"] == []


def test_detect_episode_compact_range_requires_consecutive_number():
    # Un rango compacto que NO es consecutivo (aquí "08", no "06") no se
    # acepta como episodio doble -- ninguna forma de doble episodio real
    # salta episodios de por medio.
    r = detect_episode("Serie 1x05-08.mkv")
    assert r["episode"] == 5
    assert r["extra_episodes"] == []


def test_detect_movie_has_empty_extra_episodes():
    r = detect_episode("Inception (2010) 1080p.mkv")
    assert r["media_type"] == "movie"
    assert r["extra_episodes"] == []


def test_detect_ebook_bypasses_episode_patterns():
    r = detect_episode("El Nombre del Viento.epub", is_book=True)
    assert r["media_type"] == "libro"
    assert r["season"] is None
    assert r["episode"] is None
    assert r["title"] == "El Nombre Del Viento"


def test_detect_comic_extracts_issue_number():
    # Formato real confirmado contra cómics ya en el servidor del usuario.
    r = detect_episode("Avatar - The Last Airbender - The Promise (2012) #01.cbr",
                        is_book=True, is_comic=True)
    assert r["media_type"] == "libro"
    assert r["season"] is None
    assert r["episode"] == 1
    assert "#" not in r["title"]


def test_detect_comic_without_issue_number_falls_back_to_no_episode():
    r = detect_episode("Some Random Comic.cbz", is_book=True, is_comic=True)
    assert r["media_type"] == "libro"
    assert r["episode"] is None


def test_detect_comic_regression_numeric_filename_without_is_book_flag_is_unaffected():
    # Sin is_book=True (comportamiento por defecto, para archivos de
    # vídeo), un nombre como este SIGUE cayendo en el patrón numérico de
    # anime -- is_book/is_comic son estrictamente opt-in, el llamador
    # (gui/app.py) decide según la extensión real del archivo.
    r = detect_episode("One Piece - 1078.cbz")
    assert r["media_type"] == "anime"
    assert r["episode"] == 1078


def _isolate_learned_terms(monkeypatch, tmp_path):
    """Aísla core.learned_terms del auto_processed.json/learned_junk_terms.json
    REAL del usuario -- sin esto, un término aprendido en una sesión real
    anterior (p.ej. "LA" de un fallback de IA previo) puede colarse como
    "ruido" en un título de prueba que por casualidad lo contenga."""
    import core.learned_terms as lt
    monkeypatch.setattr(lt, "app_data_dir", lambda: tmp_path)
    lt._reset_cache_for_tests()


def test_detect_comic_extracts_trailing_issue_number_without_hash(monkeypatch, tmp_path):
    # Bug real: escaneos que numeran el número de emisión suelto, sin "#" --
    # antes se quedaba siempre en el episodio 1 por defecto (ver
    # core/renamer.py::build_new_name), y una trilogía entera se subió tres
    # veces con el mismo nombre "#1".
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Avatar La Busqueda 01.cbr", is_book=True, is_comic=True)
    assert r["media_type"] == "libro"
    assert r["episode"] == 1
    assert r["title"] == "Avatar La Busqueda"


def test_detect_comic_extracts_trailing_issue_number_second_part(monkeypatch, tmp_path):
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Avatar La Busqueda 02.cbr", is_book=True, is_comic=True)
    assert r["episode"] == 2
    assert r["title"] == "Avatar La Busqueda"


def test_detect_comic_extracts_trailing_issue_number_with_year_after(monkeypatch, tmp_path):
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Avatar La Busqueda 01 (2012).cbr", is_book=True, is_comic=True)
    assert r["episode"] == 1
    assert r["title"] == "Avatar La Busqueda"
    assert "2012" not in r["title"]


def test_detect_comic_hash_pattern_still_takes_priority_over_trailing_number(monkeypatch, tmp_path):
    # Si hay "#NN", ese es el ancla inequívoca -- no debe interferir con el
    # patrón de respaldo (por si el título también contuviera otro número).
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Volumen 2 - Avatar La Busqueda #01.cbr", is_book=True, is_comic=True)
    assert r["episode"] == 1


def test_detect_comic_trailing_number_with_two_bracket_groups_after(monkeypatch, tmp_path):
    # Bug real: "World War Hulk 2 [por UltronXII][CRG].cbr" -- DOS grupos
    # entre corchetes seguidos tras el número (escaneador + grupo). Antes el
    # patrón solo toleraba UNO, así que no encontraba nada y el número de
    # grapa se perdía (caía siempre al episodio 1 por defecto) pese a estar
    # ahí, justo antes de los corchetes.
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("WWH 03 - World War Hulk 1 [por UltronXII][CRG].cbr", is_book=True, is_comic=True)
    assert r["episode"] == 1
    r = detect_episode("WWH 08 - World War Hulk 2 [por UltronXII][CRG].cbr", is_book=True, is_comic=True)
    assert r["episode"] == 2
    r = detect_episode("WWH 19 - World War Hulk 5 [por UltronXII][CRG].cbr", is_book=True, is_comic=True)
    assert r["episode"] == 5


def test_detect_comic_trailing_number_with_two_bracket_groups_different_credit(monkeypatch, tmp_path):
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("WWH 01 - Hulka Vol4 - Tomo 6 [por Parche][CRG-FPJ].cbr", is_book=True, is_comic=True)
    assert r["episode"] == 6


# ── folder_hint: series/mangas cuyos capítulos vienen numerados a secas y
# el nombre real está en la carpeta que los contiene, no en cada archivo ──

def test_detect_comic_bare_number_uses_folder_hint_as_title(monkeypatch, tmp_path):
    # Bug real: escaneos de manga donde el archivo es solo "01.cbz" y el
    # nombre de la serie está puesto en la carpeta contenedora -- sin
    # folder_hint, el título quedaba vacío y ComicVine no podía buscar nada.
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("01.cbz", is_book=True, is_comic=True,
                        folder_hint="One Piece [Completo][CRG]")
    assert r["episode"] == 1
    assert r["title"] == "One Piece"


def test_detect_comic_generic_chapter_word_uses_folder_hint(monkeypatch, tmp_path):
    # "Capitulo 05.cbr" limpia a "Capitulo" (sin número) -- palabra genérica,
    # no una serie -- debe descartarse en favor de la carpeta.
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Capitulo 05.cbr", is_book=True, is_comic=True,
                        folder_hint="Vagabond (Ed.Panini) [CRG-FPJ]")
    assert r["episode"] == 5
    assert r["title"] == "Vagabond"


def test_detect_book_bare_number_uses_folder_hint(monkeypatch, tmp_path):
    # Mismo criterio para ebooks de texto (sin número de emisión propio).
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Tomo 3.epub", is_book=True, folder_hint="El Nombre del Viento")
    assert r["title"] == "El Nombre Del Viento"


def test_detect_comic_real_title_in_filename_ignores_folder_hint(monkeypatch, tmp_path):
    # Si el archivo YA trae un título de verdad, ese gana siempre -- el
    # folder_hint es solo el último recurso, no debe pisar nada útil.
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Avatar La Busqueda 01.cbr", is_book=True, is_comic=True,
                        folder_hint="Carpeta Descargas Varias")
    assert r["title"] == "Avatar La Busqueda"


def test_detect_comic_no_folder_hint_keeps_old_behavior(monkeypatch, tmp_path):
    # Sin folder_hint (valor por defecto ""), el comportamiento no cambia.
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("01.cbz", is_book=True, is_comic=True)
    assert r["episode"] == 1
    assert r["title"] == ""


def test_detect_comic_folder_hint_that_also_cleans_to_nothing_keeps_original(monkeypatch, tmp_path):
    # Si el folder_hint tampoco deja nada útil tras limpiarlo, no debe
    # sustituir por una cadena vacía -- se conserva el título original
    # (aunque tampoco sirva de mucho, es preferible a perderlo del todo).
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Capitulo 05.cbr", is_book=True, is_comic=True,
                        folder_hint="[CRG-FPJ]")
    assert r["title"] == "Capitulo"


# ── "Capítulo N — subtítulo del capítulo": el número NO está al final del
# nombre, sino seguido del título propio de ese capítulo (no de la serie) ──

def test_detect_comic_chapter_keyword_with_subtitle_after_number(monkeypatch, tmp_path):
    # Bug real: capítulos 128/129/193 de una colección de 266 subidos todos
    # como "#01" -- el número no estaba al final del nombre (le seguía un
    # guion/raya y el subtítulo de ESE capítulo), así que el patrón de
    # "número al final" no encontraba nada.
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Capítulo 128  — Capítulo final de la segunda temporada.cbz",
                        is_book=True, is_comic=True, folder_hint="Dungeon Reset")
    assert r["episode"] == 128
    assert r["title"] == "Dungeon Reset"

    r = detect_episode("Capítulo 129  — Inicio de la tercera temporada.cbz",
                        is_book=True, is_comic=True, folder_hint="Dungeon Reset")
    assert r["episode"] == 129

    r = detect_episode("Capítulo 193  — Capítulo final de la tercera temporada.cbz",
                        is_book=True, is_comic=True, folder_hint="Dungeon Reset")
    assert r["episode"] == 193


def test_detect_comic_chapter_keyword_in_english(monkeypatch, tmp_path):
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Chapter 45 - The Reckoning.cbz", is_book=True, is_comic=True,
                        folder_hint="Some Manga")
    assert r["episode"] == 45
    assert r["title"] == "Some Manga"


def test_detect_comic_chapter_keyword_only_tried_when_trailing_number_pattern_fails(monkeypatch, tmp_path):
    # Si el número SÍ está al final (sin subtítulo detrás), el patrón
    # existente (número al final) ya lo captura bien -- no debe cambiar el
    # comportamiento de siempre para este caso simple.
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Capitulo 05.cbr", is_book=True, is_comic=True,
                        folder_hint="[CRG-FPJ]")   # folder_hint inútil a propósito
    assert r["episode"] == 5
    assert r["title"] == "Capitulo"   # comportamiento de siempre, sin cambios


def test_detect_comic_chapter_keyword_no_useful_title_falls_back_to_folder_hint(monkeypatch, tmp_path):
    _isolate_learned_terms(monkeypatch, tmp_path)
    r = detect_episode("Capítulo 10.cbz", is_book=True, is_comic=True, folder_hint="Dungeon Reset")
    assert r["episode"] == 10
    assert r["title"] == "Dungeon Reset"
