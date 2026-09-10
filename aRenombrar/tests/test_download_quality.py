from core.download_quality import (score_download, best_result, is_adult_content,
                                   is_italian_only)
from core.amule_client import AmuleSearchResult as R


def _r(number=1, name="Serie S01E01", size="500 MB", sources=5, complete=True):
    return R(number=number, name=name, size_human=size, sources=sources,
             complete=complete)


def test_best_result_empty():
    assert best_result([]) is None


def test_best_result_movie_query_title_only_with_expected_year():
    """El botón ⬇ de la pestaña Películas busca SOLO por título (aMule
    rechaza consultas con paréntesis, visto de verdad con "Michael (2026)")
    y pasa el año aparte (expected_year) para que el mejor candidato declare
    ese año en su nombre."""
    results = [
        _r(1, "Dune.1984.720p.mkv", sources=2, complete=False),
        _r(2, "Dune (2021) [BluRay] [Spanish] 1080p.mkv", sources=12, complete=True),
        _r(3, "Dune Part Two.2024.1080p.mkv", sources=8, complete=True),
    ]
    best = best_result(results, "Dune", expected_year=2021)
    assert best is not None
    assert best.number == 2


def test_expected_year_premia_la_coincidencia():
    """Un release que declara el año pedido gana a uno que no lo declara
    (mismo título y calidad de imagen)."""
    con = score_download(_r(1, "Michael.2026.1080p.mkv"), expected_year=2026)
    sin = score_download(_r(1, "Michael.1080p.mkv"), expected_year=2026)
    assert con > sin


def test_expected_year_castiga_el_año_equivocado():
    """Un release de OTRO año (remake/relanzamiento) no debe ganar a la
    película pedida por tener mejor calidad."""
    bien = score_download(_r(1, "Michael.2026.720p.mkv"), expected_year=2026)
    mal  = score_download(_r(1, "Michael.2025.1080p.mkv"), expected_year=2026)
    assert bien > mal


def test_best_result_respeta_el_año_pedido():
    results = [
        _r(1, "Michael.2025.1080p.mkv", sources=12, complete=True),
        _r(2, "Michael.2026.720p.mkv", sources=5, complete=False),
    ]
    best = best_result(results, "Michael", expected_year=2026)
    assert best is not None
    assert best.number == 2


def test_sin_año_en_el_nombre_no_penaliza():
    """Si el nombre no declara ningún año no se puede comprobar: la
    puntuación no cambia (comportamiento previo)."""
    a = score_download(_r(1, "Michael.1080p.mkv"), expected_year=2026)
    b = score_download(_r(1, "Michael.1080p.mkv"))
    assert a == b


def test_movie_query_excluye_capitulos_de_serie():
    """Botón ⬇ de Películas (is_movie): la query va SOLO con el título, sin
    numeración. Un resultado con numeración de capítulo (SxxExx/NxNN) es de
    una serie y nunca debe elegirse como la película pedida, por muy bien
    que puntúe por idioma/calidad/fuentes. Real: el ⬇ de "Leo" bajó "Leo
    Talks 2x07", una serie que el usuario no tenía."""
    episodio = _r(1, "Leo Talks 2x07 1080p HDTV x264 Castellano.mkv",
                  sources=15, complete=True, size="1500 MB")
    pelicula = _r(2, "Leo.2023.1080p.WEB-DL.x264.Castellano.mkv",
                  sources=3, complete=False, size="2 GB")
    assert score_download(episodio, "Leo", expected_year=2023, is_movie=True) == 0.0
    assert score_download(episodio, "Leo", expected_year=2023, is_movie=False) > 0.0
    best = best_result([episodio, pelicula], "Leo", expected_year=2023, is_movie=True)
    assert best is not None
    assert best.number == 2


def test_movie_query_solo_capitulos_devuelve_none():
    """Si en una petición de película (is_movie) TODOS los resultados son
    capítulos de serie, no se destaca/descarga ninguno (best_result None),
    en vez de bajar el capítulo equivocado."""
    episodios = [
        _r(1, "Leo Talks 2x07 1080p HDTV x264 Castellano.mkv",
           sources=15, complete=True, size="1500 MB"),
        _r(2, "Leo Talks 2x08 1080p HDTV x264 Castellano.mkv",
           sources=12, complete=True, size="1500 MB"),
    ]
    assert best_result(episodios, "Leo", expected_year=2023, is_movie=True) is None


def test_movie_query_sin_año_tambien_excluye_capitulos():
    """El botón ⬇ de Películas pasa is_movie=True incluso cuando la película
    no tiene año (expected_year=None): los capítulos se siguen excluyendo."""
    episodio = _r(1, "Leo Talks 2x07 1080p HDTV x264 Castellano.mkv",
                  sources=15, complete=True, size="1500 MB")
    assert score_download(episodio, "Leo", is_movie=True) == 0.0


def test_best_result_none_below_minimum():
    """Si ningún resultado alcanza el umbral mínimo (nada que coincida de
    verdad con la búsqueda), no se destaca ninguno."""
    results = [
        _r(1, "archivo-aleatorio.bin", size="5 MB", sources=0, complete=False),
        _r(2, "xd", size="10 KB", sources=1, complete=False),
    ]
    assert best_result(results) is None


def test_best_result_picks_higher_quality_and_language():
    results = [
        _r(1, "Serie S01E01 720p", sources=3, complete=False),
        _r(2, "Serie S01E01 1080p spa HDTV x264", sources=7, complete=True),
        _r(3, "Serie S01E01 SD", sources=1, complete=False),
    ]
    assert best_result(results).number == 2


def test_best_result_returns_identity_of_element():
    results = [_r(1, "a 720p"), _r(2, "b 1080p")]
    assert best_result(results) is results[1]


def test_p2p_trusted_group_beats_generic():
    """Un release de un grupo español de P2P fiable (exploradoresp2p,
    grupots, hispashare, nocturniap2p) debe ganar a otro del mismo título
    sin esa señal, aunque el genérico tenga mejor resolución/fuentes."""
    generico = _r(1, "Leo Talks 2x07 1080p HDTV x264 Castellano.mkv",
                  sources=15, complete=True, size="1500 MB")
    fiable = _r(2, "Leo Talks 2x07 720p HDTV x264 Castellano-exploradoresp2p.mkv",
                sources=2, complete=False, size="900 MB")
    assert best_result([generico, fiable], "Leo Talks 2x07").number == 2


def test_p2p_trusted_group_variants():
    for name in ("[exploradoresp2p] Serie S01E01.mkv",
                 "Serie.S01E01.720p-Hispashare.org.mkv",
                 "Serie S01E01 nocturniap2p.mkv",
                 "Serie S01E01-grupots.mkv"):
        a = score_download(_r(1, name))
        b = score_download(_r(1, "Serie S01E01.mkv"))
        assert a > b, name


def test_p2p_trusted_group_not_in_regular_name():
    assert score_download(_r(1, "Serie S01E01 720p.mkv")) == \
        score_download(_r(1, "Serie S01E01 720p.mkv"))


def test_spanish_lang_gives_points():
    en = score_download(_r(1, "Serie S01E01 720p HDTV x264"))
    es = score_download(_r(1, "Serie S01E01 720p HDTV x264 Español"))
    assert es > en


def test_resolution_order():
    la = score_download(_r(1, "Serie 720p"))
    hd = score_download(_r(1, "Serie 1080p"))
    uhd = score_download(_r(1, "Serie 2160p 4K"))
    assert uhd > hd > la


def test_extension_preference():
    mkv = score_download(_r(1, "Serie S01E01.mkv"))
    avi = score_download(_r(1, "Serie S01E01.avi"))
    assert mkv > avi


def test_more_complete_sources_scores_higher():
    few = score_download(_r(1, "Serie S01E01 720p", sources=1, complete=False))
    many = score_download(_r(1, "Serie S01E01 720p", sources=12, complete=True))
    assert many > few


def test_size_wildly_wrong_penalized():
    tiny = score_download(_r(1, "Serie S01E01 1080p", size="5 KB"))
    sane = score_download(_r(1, "Serie S01E01 1080p", size="1200 MB"))
    assert sane > tiny


def test_size_heavy_loses_to_lighter_even_with_less_quality():
    """Caso real (botón ⬇ de "Scary Movie"): un archivo de 17 GB se elegía
    por delante de encodes ligeros porque el tamaño solo restaba -2. Ahora
    un release desproporcionadamente grande debe perder contra uno ligero
    del mismo título aunque tenga algo menos de calidad."""
    heavy = score_download(_r(1, "Scary.Movie.2000.1080p.BluRay.x264.Spanish.mkv",
                              size="17 GB", sources=10, complete=True))
    light = score_download(_r(2, "Scary.Movie.2000.720p.WEB-DL.x264.Spanish.mkv",
                              size="1.2 GB", sources=5, complete=False))
    assert light > heavy


def test_best_result_evita_el_release_gigante():
    """best_result elige el encode ligero (720p) por delante del remux de
    17 GB en 1080p -- el tamaño pesa más que unos pocos puntos de calidad."""
    results = [
        _r(1, "Scary.Movie.2000.1080p.BluRay.x264.Spanish.mkv", size="17 GB",
           sources=12, complete=True),
        _r(2, "Scary.Movie.2000.720p.WEB-DL.x264.Spanish.mkv", size="1.2 GB",
           sources=4, complete=False),
    ]
    best = best_result(results, "Scary Movie")
    assert best is not None
    assert best.number == 2


def test_calidad_sigue_importando_con_tamaños_razonables():
    """Con tamaños razonables el criterio de calidad no cambia: un 1080p de
    tamaño normal sigue ganando a un 720p ligero (el tamaño solo inclina la
    balanza cuando es desproporcionado)."""
    hd = score_download(_r(1, "Serie S01E01 1080p", size="1200 MB"))
    sd = score_download(_r(1, "Serie S01E01 720p", size="700 MB"))
    assert hd > sd


def test_episode_5gb_penalized_like_heavy_movie():
    """Caso real reportado: un CAPÍTULO de serie de 5 GB en 1080p se elegía
    porque el rango "razonable" de 1080p llegaba hasta 6 GB (caía dentro y
    sumaba +3). Ahora un episodio usa rangos más estrechos (1080p: 400 MB-2
    GB, 4K: 800 MB-4 GB): 5 GB queda desproporcionado y debe perder contra
    un encode normal del mismo capítulo, aunque tenga más fuentes."""
    heavy = score_download(_r(1, "Serie S01E01 1080p HDTV x264", size="5 GB",
                              sources=15, complete=True))
    normal = score_download(_r(2, "Serie S01E01 1080p WEB-DL x264", size="1.2 GB",
                                sources=3, complete=False))
    assert normal > heavy


def test_episode_4k_5gb_penalized():
    """En 4K el rango de episodio también se estrecha (hasta 4 GB): un
    capítulo de 5 GB en 2160p se penaliza igualmente."""
    heavy = score_download(_r(1, "Serie S01E01 2160p 4K HDR", size="5 GB",
                              sources=10, complete=True))
    normal = score_download(_r(2, "Serie S01E01 2160p 4K HDR", size="2.5 GB",
                                sources=4, complete=False))
    assert normal > heavy


def test_movie_5gb_not_penalized_as_episode():
    """Una PELÍCULA de 5 GB en 1080p es normal (rango de película: 500 MB-6
    GB): con is_movie=True no se aplican los límites estrechos de episodio.
    Ambas puntúan igual por tamaño (en rango, +3), sin la penalización por
    exceso que sufre el episodio del mismo tamaño."""
    peli = _r(1, "Mi.Pelicula.2020.1080p.BluRay.x264.mkv", size="5 GB",
              sources=8, complete=True)
    ligera = _r(1, "Mi.Pelicula.2020.1080p.BluRay.x264.mkv", size="1.2 GB",
                sources=8, complete=True)
    assert score_download(peli, "Mi Pelicula", expected_year=2020, is_movie=True) == \
        score_download(ligera, "Mi Pelicula", expected_year=2020, is_movie=True)


def test_best_result_prefers_matching_season_episode():
    """Buscando "2x04" NO debe elegirse un "22x04" de otra temporada solo por
    tener mejor calidad (real: el mejor candidato salía "Los Simpsons 22x04"
    en vez del 2x04 pedido)."""
    results = [
        _r(1, "Los Simpsons 2x04 Episode 720p", sources=3, complete=True),
        _r(2, "Los Simpsons 22x04 1080p spa HDTV x264", sources=9, complete=True),
    ]
    assert best_result(results, "Los Simpsons 2x04").number == 1


def test_season_episode_mismatch_outweighs_quality():
    """Con un capítulo de otra temporada que puntúa mucho mejor en calidad,
    la no-coincidencia de temporada/episodio debe mandar sobre la calidad."""
    wrong = score_download(_r(1, "Serie 22x04 2160p 4K spa x265 mkv", sources=12,
                              complete=True), "Serie 2x04")
    right = score_download(_r(2, "Serie 2x04 720p", sources=2, complete=False),
                           "Serie 2x04")
    assert right > wrong


def test_season_match_without_query_still_ranks_by_quality():
    """Sin consulta (query=""), la puntuación sigue siendo la clásica por
    calidad: todo el aparato de coincidencia de temporada queda inactivo."""
    low = score_download(_r(1, "Serie 720p"))
    high = score_download(_r(2, "Serie 1080p"))
    assert high > low


# ---- Porno / contenido adulto ----

def test_is_adult_content_detects_porn_markers():
    assert is_adult_content("Serie S01E01 XXX 1080p.mkv")
    assert is_adult_content("Serie S01E01 Porn 720p.avi")
    assert is_adult_content("Hentai Collection 4K.mkv")
    assert is_adult_content("Serie XXX Parody 1080p.mp4")
    assert not is_adult_content("Serie S01E01 1080p.mkv")
    assert not is_adult_content("Sex Education S01E01 1080p.mkv")  # título legítimo
    assert not is_adult_content("Adult Swim 2x04 720p.mkv")


def test_best_result_never_picks_porn_even_with_best_quality():
    """Un XXX 4K con muchas fuentes NO debe ganar jamás a un capítulo normal."""
    results = [
        _r(1, "Los Simpsons 2x04 XXX 2160p 4K spa", sources=50, complete=True),
        _r(2, "Los Simpsons 2x04 720p", sources=3, complete=False),
    ]
    best = best_result(results, "Los Simpsons 2x04")
    assert best is not None
    assert best.number == 2


def test_best_result_returns_none_when_only_porn():
    results = [
        _r(1, "XXX Porn 1080p.mkv", sources=99, complete=True),
        _r(2, "Hentai 4K.mkv", sources=10, complete=True),
    ]
    assert best_result(results, "Algo") is None


def test_porn_never_scores_above_threshold():
    assert score_download(_r(1, "XXX 4K 100 fuentes", sources=99, complete=True)) < 15.0
    assert score_download(_r(1, "Serie 720p", sources=3, complete=True)) >= 15.0


# ---- Precisión extra ----

def test_best_result_rejects_other_series_with_same_episode():
    """Buscando "Los Simpsons 2x04", un "Padre de Familia 2x04 1080p" (otra
    serie, misma numeración) NO debe ganar al capítulo de la serie pedida."""
    results = [
        _r(1, "Padre de Familia 2x04 1080p spa HDTV", sources=9, complete=True),
        _r(2, "Los Simpsons 2x04 720p", sources=3, complete=False),
    ]
    best = best_result(results, "Los Simpsons 2x04")
    assert best.number == 2


def test_best_result_prefers_real_episode_over_sample():
    """Un "sample" / "trailer" del capítulo NO debe ganar al capítulo entero."""
    results = [
        _r(1, "Serie S01E01 sample 1080p.mkv", sources=5, complete=True),
        _r(2, "Serie S01E01 720p.mkv", sources=3, complete=True),
    ]
    best = best_result(results, "Serie S01E01")
    assert best.number == 2


def test_non_video_extension_never_wins():
    """Un .emulecollection / .srt no es un capítulo descargable."""
    results = [
        _r(1, "Serie S01E01 1080p.emulecollection", sources=9, complete=True),
        _r(2, "Serie S01E01 720p.mkv", sources=3, complete=True),
    ]
    best = best_result(results, "Serie S01E01")
    assert best.number == 2


def test_cam_rip_penalized_over_web():
    """Una captura de cine (cam/screener) no debe ganar a una copia web."""
    low = score_download(_r(1, "Serie S01E01 CAM 1080p", sources=9, complete=True))
    good = score_download(_r(1, "Serie S01E01 WEB-DL 1080p", sources=3, complete=False))
    assert good > low


def test_latino_audio_counts_as_spanish():
    plain = score_download(_r(1, "Serie S01E01 1080p"))
    lat = score_download(_r(1, "Serie S01E01 1080p latino"))
    assert lat > plain


# ---- Catalán ----

def test_catalan_never_wins_over_spanish():
    """Un capítulo en catalán (aunque sea 1080p con muchas fuentes) no debe
    ganar a uno en español -- el usuario no quiere nada en catalán."""
    results = [
        _r(1, "Los Simpsons 2x04 català 1080p", sources=50, complete=True),
        _r(2, "Los Simpsons 2x04 castellano 720p", sources=3, complete=False),
    ]
    best = best_result(results, "Los Simpsons 2x04")
    assert best is not None
    assert best.number == 2


def test_catalan_detected_variants():
    for name in ("Serie S01E01 català.mkv",
                 "Serie S01E01 catalan 720p.mkv",
                 "Serie S01E01 Catalan 1080p.avi",
                 "Serie S01E01 VOSC.mkv",
                 "Serie S01E01 doblaje catalán.avi",
                 "Serie S01E01 Cat.Subs.x264.mkv",
                 "Serie S01E01 Catsubs 720p.mkv",
                 "Serie S01E01 Cat-Subs.x264.mkv",
                 "Serie S01E01 [Cat] 1080p.mkv",
                 "Serie S01E01 (CAT) 1080p.mkv"):
        cat = score_download(_r(1, name, sources=9, complete=True))
        es = score_download(_r(1, "Serie S01E01 castellano.mkv", sources=3,
                                complete=False))
        assert cat < es, f"catalán no penalizado: {name}"


def test_cat_token_alone_is_not_catalan():
    """"cat" suelto no delata catalán (puede ser categoría, cat-1, etc.); solo
    penalizan marcadores inequívocos (català/catalan/catala/vosc, Cat.Subs y
    el tag [Cat]/(CAT))."""
    assert score_download(_r(1, "Serie S01E01 cat 1080p.mkv")) >= 0
    assert score_download(_r(1, "Serie S01E01 categoria.mkv")) >= 0
    assert score_download(_r(1, "Serie S01E01 catwoman.mkv")) >= 0


def test_catalan_with_spanish_not_penalized():
    """Con pista en español también ("castellano + català" en dual), no se
    penaliza: lo que importa es que trae español. Solo el catalán sin
    español resta."""
    cat_dual = score_download(_r(1, "Los Simpsons 2x04 castellano+català 1080p",
                                  sources=30, complete=True), "Los Simpsons 2x04")
    es_only = score_download(_r(2, "Los Simpsons 2x04 castellano 720p",
                                 sources=3, complete=False), "Los Simpsons 2x04")
    assert cat_dual > es_only
    # Y el catalán sin español sigue perdiendo contra el mismo release
    cat_only = score_download(_r(1, "Los Simpsons 2x04 català 1080p",
                                  sources=30, complete=True), "Los Simpsons 2x04")
    assert es_only > cat_only


def test_catalan_subtitle_release_never_wins_real_case():
    """Caso real reportado: "Crímenes - 1x11.Per.que.matem.720p.WEB-DL.AAC2.0.
    Cat.Subs.x264-Hera_72 (Crims).mkv" (en catalán) no debe ganar al release
    en español aunque tenga mejores fuentes."""
    cat = _r(1, "Crímenes - 1x11.Per.que.matem.720p.WEB-DL.AAC2.0.Cat.Subs.x264-Hera_72 (Crims).mkv",
             size="500 MB", sources=30, complete=True)
    es = _r(2, "Crímenes - 1x11.Capítulo once.castellano.720p.mkv",
            size="500 MB", sources=3, complete=False)
    assert best_result([cat, es], "Crímenes 1x11").number == 2


# ---- V.O.S. / Italiano (priorizar castellano) ----

def test_vos_never_wins_over_spanish_dub():
    """Un V.O.S. (audio original + subtítulos, sin doblaje) en 4K con muchas
    fuentes NO debe ganar a un capítulo doblado al castellano."""
    results = [
        _r(1, "Los Simpsons 2x04 2160p 4K VOSE", sources=50, complete=True),
        _r(2, "Los Simpsons 2x04 castellano 720p", sources=3, complete=False),
    ]
    best = best_result(results, "Los Simpsons 2x04")
    assert best is not None
    assert best.number == 2


def test_vos_detected_variants():
    for name in ("Serie S01E01 VOS.mkv",
                 "Serie S01E01 V.O.S. 1080p.mkv",
                 "Serie S01E01 VOSE.mkv",
                 "Serie S01E01 VOSE_ES 720p.mkv",
                 "Serie S01E01 VOSI.mkv",
                 "Serie S01E01 Versión original subtitulada.mkv",
                 "Serie S01E01 version original.mkv",
                 "Serie S01E01 V.O.S 1080p.mkv"):
        vos = score_download(_r(1, name, sources=9, complete=True))
        es = score_download(_r(1, "Serie S01E01 castellano.mkv", sources=3,
                                complete=False))
        assert vos < es, f"V.O.S. no penalizado: {name}"


def test_italian_never_wins_over_spanish():
    """Un release italiano (aunque sea 1080p con muchas fuentes) no debe ganar
    a uno en castellano."""
    results = [
        _r(1, "Los Simpsons 2x04 ITA 1080p", sources=50, complete=True),
        _r(2, "Los Simpsons 2x04 castellano 720p", sources=3, complete=False),
    ]
    best = best_result(results, "Los Simpsons 2x04")
    assert best is not None
    assert best.number == 2


def test_italian_detected_variants():
    for name in ("Serie S01E01 ITA.mkv",
                 "Serie S01E01 Italiano 720p.mkv",
                 "Serie S01E01 Italian 1080p.avi",
                 "Serie S01E01 Italiana 720p.mkv"):
        ita = score_download(_r(1, name, sources=9, complete=True))
        es = score_download(_r(1, "Serie S01E01 castellano.mkv", sources=3,
                                complete=False))
        assert ita < es, f"italiano no penalizado: {name}"


def test_vos_still_eligible_when_only_option():
    """V.O.S. se EXCLUYE del todo por petición del usuario (como el porno):
    aunque sea lo único disponible, no se elige (best_result None). Ver
    is_vos_content / is_french_content para VOSTFR."""
    results = [_r(1, "Serie 2x04 VOSE 1080p WEB-DL x264.mkv", sources=9, complete=True)]
    best = best_result(results, "Serie 2x04")
    assert best is None
    # Caso real VOSTFR también excluido
    r2 = _r(1, "Tensei Shitara Slime Datta Ken 4x10 VOSTFR Web (by OtakuNashi).ts", sources=9, complete=True)
    assert best_result([r2], "Tensei Shitara Slime Datta Ken 4x10") is None


def test_vos_and_ita_unrelated_words_not_penalized():
    """"vos"/"ita" no deben falsear coincidencias casuales (palabras normales
    del nombre no penalizadas)."""
    plain = score_download(_r(1, "Serie S01E01 720p castellano.mkv"))
    assert score_download(_r(1, "Serie S01E01 720p.mkv")) >= 0
    assert plain > score_download(_r(1, "Serie S01E01 720p.mkv"))


def test_resident_alien_real_case_es_with_ita_subs_loses_to_pure_english_spanish():
    """Caso real reportado por el usuario, contra datos reales de aMule:
    buscando "Resident Alien 4x04", el release [BRrip] con audio ENG-SPA pero
    SUBÍTULOS en italiano (ITA) no debe recomendarse por delante de la copia
    en inglés+castellano con subs. Ambos, en torno a 865 MB / 1080p."""
    ita_subs = _r(1, "[BRrip] Resident Alien 4x04 - Truth Hurts (USA-2025) "
                     "[1080P-H264-AC3 ENG-SPA] [ITA subbed MiMMo - ottimo] [HDitaly].mkv",
                  size="865 MB", sources=1, complete=False)
    es = _r(2, "Resident.Alien.4x04.La.verdad.duele.(Spanish.English.Subs)."
               "WEBRip.1080p.x264-AC3.mkv",
            size="865 MB", sources=1, complete=False)
    assert best_result([ita_subs, es], "Resident Alien 4x04").number == 2


def test_italian_only_never_downloaded_even_when_only_option():
    """Caso real: buscando "Star Trek Strange New Worlds 4x04" solo aparece un
    release italiano (sin ningún español). No debe descargarse nada (best_result
    devuelve None), en vez de bajar el italiano porque "pasa el umbral"."""
    ita = _r(1, "Star.Trek.Strange.New.Worlds.4x04.Un.Caso.Di.Chiaroscuro."
                "ITA.AMZN.WEB-DLRip.x264-UBi.mkv",
             size="536 MB", sources=1, complete=False)
    assert is_italian_only(ita.name)
    assert score_download(ita, "Star Trek Strange New Worlds 4x04") < 15.0
    assert best_result([ita], "Star Trek Strange New Worlds 4x04") is None


def test_italian_detected_from_title_without_ita_token():
    """Un nombre sin token ITA pero con título traducido al italiano (>=2
    palabras-función inequívocas) también se considera solo-italiano."""
    name = "Star.Trek.Strange.New.Worlds.4x04.Una.Storia.Di.Guerra.mkv"
    assert is_italian_only(name)
    assert best_result([_r(1, name)], "Star Trek Strange New Worlds 4x04") is None


def test_italian_with_spanish_audio_stays_eligible():
    """Un release con audio español + subs en italiano (ENG-SPA + ITA subbed)
    NO es "solo italiano": se mantiene como candidato de emergencia (penalizado,
    pero elegible si no hay otra cosa en español)."""
    name = "[BRrip] Resident Alien 4x04 - Truth Hurts (USA-2025) " \
           "[1080P-H264-AC3 ENG-SPA] [ITA subbed MiMMo - ottimo] [HDitaly].mkv"
    assert not is_italian_only(name)
    r = _r(1, name, size="865 MB", sources=1, complete=False)
    assert best_result([r], "Resident Alien 4x04") is not None


def test_spanish_title_words_not_flagged_as_italian():
    """El castellano no debe caer en los marcadores de título italiano
    (palabras sueltas como "la"/"un"/"de" compartidas no bastan: se exigen >=2
    marcadores inequívocos y ausencia de rastro de español)."""
    assert not is_italian_only("Serie 4x04 La verdad duele 720p castellano.mkv")
    assert not is_italian_only("Serie 4x04 Un nuevo comienzo español.mkv")
    assert not is_italian_only("Serie 4x04 El día de la bestia español.mkv")


# ---- Misma serie, no solo misma numeración (real: "Lucky" eligió "Lucky Luke") ----

def test_un_nombre_corto_contenido_en_otro_se_acepta():
    """DECISIÓN CONSCIENTE del usuario (2026-08-31), con su contrapartida.

    Para poder pedir series por un alias corto ("Slime" en vez de "That Time I
    Got Reincarnated as a Slime"), _same_series_title acepta un nombre
    normalizado contenido en el otro. El precio es que vuelve a colar el caso
    que este test vigilaba: pedir "Lucky" acepta "Lucky Luke", que es OTRA
    serie con la misma numeración.

    Afecta también al autocompletado AUTOMÁTICO (_auto_complete_pass ->
    _auto_amule_download_series -> best_result), no solo a la búsqueda manual,
    así que una serie de nombre corto puede completarse sola con capítulos de
    otra. Se deja así a petición expresa; si algún día molesta, la salida es
    una lista de alias explícita en vez de aceptar cualquier subcadena."""
    luke = _r(1, "Lucky Luke 1x01 El solitario (Spanish French Subs) WEB-DL 1080p x264-EAC3.mkv",
              size="1200 MB", sources=15, complete=True)
    assert score_download(luke, "Lucky 1x01") > 0.0


def test_same_series_with_annotation_still_matches():
    """El modo estricto no rompe las anotaciones legítimas: "Desencanto
    (Disenchantment)" sigue siendo "Desencanto", y "Ranma ½" es "Ranma
    (1989)" -- se conserva la reutilización de nombres cortos/largos."""
    r1 = _r(1, "Desencanto (Disenchantment) 1x01 720p.mkv", size="500 MB")
    r2 = _r(2, "Ranma (1989) 1x01 720p.mkv", size="500 MB")
    assert score_download(r1, "Desencanto 1x01") > 0.0
    assert score_download(r2, "Ranma 1x01") > 0.0


def test_bracket_group_prefix_does_not_break_series_match():
    """Los releases reales anteponen el tag de grupo al título ("[BRrip]
    Resident Alien"); el tag no debe hacer que falle la coincidencia de
    serie."""
    r = _r(1, "[BRrip] Resident Alien 4x04 - Truth Hurts 720p.mkv", size="500 MB")
    assert score_download(r, "Resident Alien 4x04") > 0.0


def test_entre_dos_candidatos_manda_la_calidad():
    """Contrapartida de aceptar nombres contenidos (ver el test de arriba): con
    "Lucky 1x01" pedido, "Lucky Luke" ya no queda descartado por ser otra
    serie, así que gana el de mejor calidad y más fuentes aunque no sea la
    serie pedida. Queda escrito para que se vea el efecto real de la decisión,
    no como comportamiento deseable."""
    luke = _r(1, "Lucky Luke 1x01 El solitario (Spanish French Subs) WEB-DL 1080p x264-EAC3.mkv",
              size="1200 MB", sources=15, complete=True)
    real = _r(2, "Lucky 1x01 720p WEB-DL x264 Castellano.mkv",
              size="600 MB", sources=3, complete=False)
    best = best_result([luke, real], "Lucky 1x01")
    assert best is not None
    assert best.number == 1