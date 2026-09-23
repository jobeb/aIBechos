"""
Detector de episodios que faltan: cruza la lista COMPLETA de episodios que
debería tener una serie (según TMDB) con lo que de verdad hay en Plex/
Jellyfin, para encontrar huecos -- algo que ninguno de los dos servicios
sabe decir por sí solo (TMDB no sabe qué tienes tú, Plex/Jellyfin no saben
qué deberías tener).

Solo informa de los huecos -- no descarga ni gestiona nada, ver CLAUDE.md /
la conversación: aIBechos organiza y sube contenido que el usuario ya
tiene, nunca lo adquiere por su cuenta.
"""


def find_missing_episodes(expected: dict, present: set) -> dict:
    """expected: {temporada: [numero_episodio, ...]} -- lo que debería haber
    según TMDB (excluyendo especiales, temporada 0).
    present: {(temporada, episodio), ...} -- lo que de verdad hay en
    Plex/Jellyfin para esa serie.
    Devuelve {temporada: [episodios_que_faltan]}, solo con las temporadas
    que de verdad tienen algún hueco."""
    missing = {}
    for season, episode_numbers in expected.items():
        gaps = sorted(ep for ep in episode_numbers if (season, ep) not in present)
        if gaps:
            missing[season] = gaps
    return missing


def looks_like_season_split(season_episode_numbers: list, missing_in_season: list) -> bool:
    """True si el hueco reportado en esta temporada es EXACTAMENTE la
    segunda mitad, con la primera mitad completa -- el patrón típico de
    series que Netflix lanza en dos "partes" de episodios (Disenchantment,
    por ejemplo: Parte 1 y Parte 2 de 10 episodios cada una, publicadas con
    un año de diferencia). TMDB cuenta eso como UNA sola temporada de 20
    episodios, pero muchas bibliotecas (siguiendo cómo se organizó al
    descargarlo) lo tienen como dos temporadas de 10 -- así que no es que
    falten episodios de verdad, es un desajuste de numeración con TMDB."""
    total = len(season_episode_numbers)
    if total < 12 or total % 2 != 0 or not missing_in_season:
        return False
    half = total // 2
    second_half = set(sorted(season_episode_numbers)[half:])
    return set(missing_in_season) == second_half


def apply_season_split_filter(missing: dict, expected: dict) -> tuple[dict, set]:
    """Quita de *missing* las temporadas que looks_like_season_split
    identifica con alta confianza como desajuste de numeración con TMDB
    (ver su docstring, caso real de Disenchantment) -- el patrón exacto de
    "falta justo la segunda mitad" es lo bastante específico como para no
    ser una coincidencia, así que esos episodios dejan de contar como
    "que faltan" en vez de solo mostrarse con un aviso. Devuelve
    (missing_filtrado, temporadas_quitadas) -- el segundo se sigue
    guardando en la fila como "split_seasons" para el aviso ⚠ junto al
    resumen, aunque ya no sume al recuento principal."""
    split_seasons = {season for season, eps in missing.items()
                     if looks_like_season_split(expected.get(season, []), eps)}
    if not split_seasons:
        return missing, split_seasons
    filtered = {season: eps for season, eps in missing.items() if season not in split_seasons}
    return filtered, split_seasons


def looks_like_absolute_numbering(tmdb_season_counts: dict, present: set) -> bool:
    """True si *present* parece usar numeración absoluta (todos los
    episodios contados de corrido, sin reiniciar en cada temporada --
    "Naruto Shippuden 347" en vez de "temporada 15 episodio 12") en lugar
    de numeración por temporada. Común en anime de muchos episodios que se
    organiza sin carpetas de temporada.

    Señal: la serie tiene varias temporadas según TMDB, pero lo que hay
    reporta como mucho un par de números de temporada distintos, con
    números de episodio que se pasan de largo de lo que esa temporada
    debería tener -- si tuviera 12 episodios y aparece el "episodio 262",
    ese 262 no puede ser un episodio real de esa temporada."""
    if len(tmdb_season_counts) < 2 or not present:
        return False
    present_seasons = {season for season, _ in present}
    if len(present_seasons) > 2:
        return False   # ya reparte en varias temporadas -- no parece numeración absoluta
    for season in present_seasons:
        season_size = tmdb_season_counts.get(season)
        if not season_size:
            continue
        max_ep = max(ep for s, ep in present if s == season)
        if max_ep > season_size * 1.5:
            return True
    return False


def remap_absolute_episodes(tmdb_season_counts: dict, present: set) -> set:
    """Convierte episodios en numeración absoluta (p.ej. episodio 262 bajo
    "temporada 1") a (temporada, episodio-dentro-de-esa-temporada) usando
    los recuentos POR TEMPORADA de TMDB de forma acumulada -- para poder
    comparar correctamente contra lo que se espera temporada a temporada
    en vez de arrastrar un falso "falta todo" por el desajuste de
    numeración. tmdb_season_counts: {temporada: num_episodios_de_TMDB}."""
    cumulative = []
    total = 0
    for season in sorted(tmdb_season_counts):
        count = tmdb_season_counts[season]
        cumulative.append((total, total + count, season))
        total += count

    remapped = set()
    for _season, absolute_ep in present:
        for start, end, real_season in cumulative:
            if start < absolute_ep <= end:
                remapped.add((real_season, absolute_ep - start))
                break
    return remapped


def tmdb_season_episode_offsets(expected: dict) -> dict:
    """{temporada: desplazamiento} para pasar la numeración de episodios
    de TMDB a numeración por temporada que reinicia en 1 (la que usan los
    releases y las bibliotecas normales): desplazamiento = primer número
    de episodio de TMDB en esa temporada menos 1.

    Lo normal es 0 en todas (TMDB ya empieza en 1 y no cambia nada). Caso
    real: Hunter x Hunter 2011 (TMDB T1: 1-62, T2: 63-136, T3: 137-148)
    -> {1: 0, 2: 62, 3: 136}. Sin esto, una serie así sale con cientos de
    falsos "faltan" (el servidor reinicia en cada temporada) y el
    autocompletado buscaría/descargaría episodios que no existen.

    Si una temporada viene vacía o su primer número no es >= 1 (datos
    raros de TMDB), su desplazamiento es 0: ante la duda no se toca nada.
    """
    offsets = {}
    for season, eps in (expected or {}).items():
        nums = sorted(eps) if eps else []
        first = nums[0] if nums else 1
        offsets[season] = (first - 1) if first >= 1 else 0
    return offsets


def normalize_tmdb_season_numbering(expected: dict, episode_titles: dict = None,
                                    episode_air_dates: dict = None) -> tuple:
    """Pasa *expected* ({temporada: [números de episodio de TMDB]}) y los
    mapas que van indexados por esos mismos números (títulos y fechas de
    emisión) a numeración por temporada desde 1, restando el desplazamiento
    de cada temporada (ver tmdb_season_episode_offsets). Devuelve
    (expected, episode_titles, episode_air_dates, offsets) -- los mapas que
    lleguen como None vuelven como {}.

    Todo lo que cuelga de la comparación (huecos, títulos para mostrar,
    consultas de descarga) trabaja entonces en la numeración del usuario,
    que es la que usan los releases y la que Jellyfin/Plex lee de los
    nombres de archivo. Para series normales (desplazamiento 0 en todo)
    es una operación vacía. Compone bien con remap_absolute_episodes: con
    numeración TMDB continua, su salida ya cae en numeración de usuario
    (el inicio acumulado de cada temporada coincide con primer-1), así que
    también compara bien sin tocar nada más.
    """
    offsets = tmdb_season_episode_offsets(expected)
    if not any(offsets.values()):
        return (dict(expected or {}),
                dict(episode_titles or {}),
                dict(episode_air_dates or {}),
                offsets)
    new_expected = {}
    for season, eps in (expected or {}).items():
        off = offsets.get(season, 0)
        new_expected[season] = sorted(ep - off for ep in eps)

    def _renumber(mapping):
        renumbered = {}
        for season, entries in (mapping or {}).items():
            off = offsets.get(season, 0)
            renumbered[season] = {ep - off: v for ep, v in entries.items()}
        return renumbered

    return (new_expected, _renumber(episode_titles),
            _renumber(episode_air_dates), offsets)


def find_unknown_seasons(present: set, expected_seasons) -> set:
    """Temporadas que el servidor (Plex/Jellyfin) tiene para esta serie pero
    que TMDB no tiene registradas EN ABSOLUTO para el ID emparejado --
    señal fuerte de que Jellyfin/Plex tiene esta serie emparejada con el ID
    de TMDB equivocado (otra serie con menos temporadas), más que de un
    hueco real: si TMDB no conoce esa temporada, no hay con qué comparar
    sus episodios, así que el resultado para esta serie no es fiable.
    present: {(temporada, episodio), ...}. expected_seasons: números de
    temporada que sí conoce TMDB para este ID."""
    present_seasons = {season for season, _ in present}
    return present_seasons - set(expected_seasons)


def format_missing_ranges(missing: dict) -> str:
    """'T1E05, T1E08, T3E01-T3E03' -- igual que format_missing_summary pero
    sin el nombre de la serie delante (para tablas donde el nombre ya va en
    otra columna). Agrupa episodios consecutivos en rangos para no hacer
    listas eternas con series con muchos huecos."""
    if not missing:
        return ""
    parts = []
    for season in sorted(missing):
        # Una temporada con lista vacía nunca debería llegar aquí (quien
        # quita el último episodio de una temporada también debe borrar
        # la clave, ver remove_missing_episode/remove_missing_episode_from_cache)
        # -- pero esto lee un caché en disco (missing_episodes_cache.json),
        # estado externo mutable que puede quedar inconsistente por otras
        # vías (edición a mano, una versión futura con otro bug...). Sin
        # este guard, _season_ranges revienta con IndexError al pedir
        # episodes[0] de una lista vacía, y como esto se llama durante el
        # arranque (_load_missing_episodes_from_cache), tumbaba la ventana
        # entera antes de que llegara a abrirse -- sin ningún error visible
        # si se lanzó sin consola (ver lanzar.vbs).
        if not missing[season]:
            continue
        parts.extend(_season_ranges(season, missing[season]))
    return ", ".join(parts)


def format_missing_summary(show_name: str, missing: dict) -> str:
    """'Serie X: T1E05, T1E08, T3E01-T3E03' -- ver format_missing_ranges."""
    ranges = format_missing_ranges(missing)
    return f"{show_name}: {ranges}" if ranges else ""


def has_spanish_availability(providers: dict) -> bool:
    """True si la serie aparece en /tv/{id}/watch/providers para la región
    "ES" -- primer filtro (barato, una llamada por serie) del interruptor
    "Ocultar sin doblaje ES": si ni siquiera está disponible en España, no
    hace falta gastar una llamada por episodio para comprobar el doblaje."""
    return "ES" in (providers.get("results") or {})


def episode_has_spanish_text(episode_info: dict) -> bool:
    """True si /tv/{id}/season/{s}/episode/{e}?language=es-ES devuelve texto
    localizado real -- TMDB, cuando no tiene traducción, suele devolver
    'overview'/'name' vacíos en vez de dar error, así que hay que mirar el
    contenido, no solo si la llamada tuvo éxito.

    OJO: esto es una aproximación con un fallo conocido -- TMDB traduce el
    texto (título/sinopsis) de un episodio con independencia de si tiene
    audio doblado de verdad. Caso real: Bleach tiene texto en español para
    casi todos sus 366 episodios, pero el doblaje CASTELLANO (España) solo
    llegó hasta el 109 -- esta función devuelve True para episodios muy
    posteriores igualmente. Es el comportamiento por defecto del
    interruptor (gratis, automático); el veredicto de la IA (ver
    core/missing_episodes_ai.py, filter_missing_by_dub_cutoff) lo corrige
    cuando el usuario pulsa "Preguntar a la IA" para esa serie."""
    return bool((episode_info.get("overview") or "").strip()) or bool((episode_info.get("name") or "").strip())


def episode_has_spanish_translation(translations: dict) -> bool:
    """True si /tv/{id}/season/{s}/episode/{e}/translations lista el español
    ("es", cualquier región) entre los idiomas con traducción REAL.

    Primera señal del interruptor "Ocultar sin doblaje ES", ANTES de mirar
    el texto del episodio (ver episode_has_spanish_text): ese endpoint con
    language=es-ES devuelve el texto en INGLÉS cuando no hay traducción
    (fallback de TMDB), así que un episodio solo-en-inglés parecía
    "doblado" (real: El Arca 3x01 seguía visible y recomendado). Esta lista
    no tiene fallback -- si no hay entrada "es", el episodio ni siquiera
    está traducido y se marca sin doblaje sin gastar más llamadas. Si SÍ
    hay "es", se sigue con el chequeo de texto de siempre (un texto
    traducido tampoco garantiza audio doblado -- ver el caso Bleach -- pero
    eso lo refinan eldoblaje.com y la IA, no este filtro barato)."""
    if not isinstance(translations, dict):
        items = translations if isinstance(translations, list) else []
    else:
        items = translations.get("translations", [])
    for t in items if isinstance(items, list) else []:
        if isinstance(t, dict) and (t.get("iso_639_1") or "").lower() == "es":
            return True
    return False


def eps_without_confirmed_dub(season: int, eps: list, dub_episodes: dict = None,
                              dub_cutoff: dict = None) -> list:
    """Subconjunto de *eps* (temporada *season*) SIN doblaje castellano
    confirmado -- para el aviso previo al ⬇ con "Ocultar sin doblaje ES"
    activo (ver gui/app.py::_missing_ep_dub_warning_eps). Precedencia igual
    que la tabla: corte (IA o eldoblaje, {temporada: último_doblado}) si
    cubre la temporada; si no, veredictos por episodio de TMDB ({"SxEE":
    bool}). Solo cuenta lo EXPLÍCITO (False, o episodio más allá del
    corte): un episodio sin comprobar no dispara el aviso, porque sin dato
    no se puede saber -- el chequeo TMDB/eldoblaje lo irá rellenando."""
    eps = list(eps or [])
    if not eps:
        return []
    if dub_cutoff is not None:
        cut = dub_cutoff.get(season, dub_cutoff.get(str(season)))
        if cut is not None:
            return [ep for ep in eps if ep > cut]
    dub = dub_episodes or {}
    return [ep for ep in eps if dub.get(f"{season}x{ep:02d}") is False]


def filter_missing_by_spanish_dub(missing: dict, dub_by_episode: dict) -> dict:
    """Reduce *missing* ({temporada: [episodios]}) a solo los episodios con
    doblaje ES confirmado -- dub_by_episode: {"{temporada}x{episodio:02d}":
    bool}. Un episodio que TODAVÍA no se ha comprobado (no está en
    dub_by_episode) se considera visible por defecto: el worker en segundo
    plano lo irá rellenando, y ocultarlo de entrada escondería contenido
    real mientras se comprueba, en vez de solo lo ya confirmado sin doblaje."""
    result = {}
    for season, episodes in missing.items():
        kept = [ep for ep in episodes if dub_by_episode.get(f"{season}x{ep:02d}", True)]
        if kept:
            result[season] = kept
    return result


def filter_missing_by_dub_cutoff(missing: dict, dub_cutoff: dict) -> dict:
    """Igual que filter_missing_by_spanish_dub, pero a partir del veredicto
    de la IA (ver core/missing_episodes_ai.py) -- dub_cutoff:
    {temporada: último_episodio_doblado}. Se usa SOLO para series para las
    que el usuario pulsó "Preguntar a la IA" (ver
    gui/app.py::_visible_missing_ep_row): su veredicto sustituye al chequeo
    automático de TMDB para esa serie, nunca al revés. Una temporada que no
    aparece en dub_cutoff (la IA no dio veredicto para ella, o el doblaje
    está completo) se deja tal cual, visible por defecto."""
    result = {}
    for season, episodes in missing.items():
        cutoff = dub_cutoff.get(season)
        kept = [ep for ep in episodes if cutoff is None or ep <= cutoff]
        if kept:
            result[season] = kept
    return result


def apply_ignored_filter(missing: dict, ignored_seasons, ignored_episodes: dict) -> dict:
    """Recorta *missing* ({temporada: [episodios]}) quitando las temporadas
    de *ignored_seasons* enteras y los episodios sueltos de
    *ignored_episodes* ({temporada: [episodios]}) -- mismo mecanismo que
    filter_missing_by_spanish_dub/filter_missing_by_dub_cutoff (recorta, no
    borra nada persistido), para "Ignorar capítulo"/"Ignorar temporada" en
    Episodios que faltan (gui/app.py::_toggle_missing_ep_episode_ignore/
    _toggle_missing_ep_season_ignore). Una temporada ignorada entera tiene
    prioridad sobre episodios sueltos ignorados de esa misma temporada
    (quedarían huérfanos igual, la temporada ya no aparece)."""
    ignored_seasons = set(ignored_seasons or ())
    ignored_episodes = ignored_episodes or {}
    result = {}
    for season, episodes in missing.items():
        if season in ignored_seasons:
            continue
        ignored_eps = set(ignored_episodes.get(season, ()))
        kept = [ep for ep in episodes if ep not in ignored_eps]
        if kept:
            result[season] = kept
    return result


def remove_series(results: list, tmdb_id: int) -> bool:
    """Quita la fila ENTERA de *results* cuyo tmdb_id coincida -- usado
    cuando la serie completa se borra del servidor (botón de borrar en
    Episodios que faltan, o borrado desde Liberar espacio), a diferencia
    de remove_missing_episode que solo quita un episodio concreto.
    Mutación en sitio. Devuelve True si de verdad había algo que quitar."""
    for r in results:
        if r.get("tmdb_id") == tmdb_id:
            results.remove(r)
            return True
    return False


def remove_missing_episode(results: list, tmdb_id: int, season: int, episode: int) -> bool:
    """Quita (temporada, episodio) de la fila de *results* (misma lista que
    gui/app.py::App._missing_ep_results) cuyo tmdb_id coincida -- usado justo
    tras subir ese episodio, para no tener que esperar a un nuevo escaneo
    completo (que además depende de que Jellyfin/Plex ya hayan reindexado
    la biblioteca). Si la serie se queda sin ningún hueco (ni
    unknown_seasons), se quita la fila entera de *results* -- mutación en
    sitio, results se modifica directamente. Devuelve True si de verdad
    había algo que quitar."""
    for r in results:
        if r.get("tmdb_id") != tmdb_id:
            continue
        eps = r.get("missing", {}).get(season)
        if not eps or episode not in eps:
            return False
        eps.remove(episode)
        if not eps:
            del r["missing"][season]
        r["summary"] = format_missing_summary(r["name"], r["missing"])
        if not r["missing"] and not r.get("unknown_seasons"):
            results.remove(r)
        return True
    return False


def _season_ranges(season: int, episodes: list) -> list:
    """[1,2,3,7] -> ['T1E01-T1E03', 'T1E07']"""
    ranges = []
    start = prev = episodes[0]
    for ep in episodes[1:]:
        if ep == prev + 1:
            prev = ep
            continue
        ranges.append(_fmt_range(season, start, prev))
        start = prev = ep
    ranges.append(_fmt_range(season, start, prev))
    return ranges


def _fmt_range(season: int, start: int, end: int) -> str:
    if start == end:
        return f"T{season}E{start:02d}"
    return f"T{season}E{start:02d}-T{season}E{end:02d}"
