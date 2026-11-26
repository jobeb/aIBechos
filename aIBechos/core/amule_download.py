"""
Descarga automática en aMule (buscar + elegir el mejor candidato + lanzar) y
tamaño típico de episodio de una serie en el FTP -- sin interfaz. La usan el
botón ⬇ de "Episodios que faltan", el autocompletado, "Adelgazar"...
"""

from __future__ import annotations

import binascii
import re
import subprocess
import threading
import time

from core.applog import get_logger
from core.download_quality import best_result
from core.ec_client import EcClient, EcConnectionError, EcAuthError, EcProtocolError
from core.series_match import normalize_series_name, series_similarity, sibling_blocks_folder, spinoff_blocks_folder
from core.transfer import make_client

_log = get_logger("aIBechos.amule_download", "app.log")

#: aMule solo soporta UNA conexión EC a la vez: abrir otra cierra la que
#: hubiera en curso. Todo lo que hable con aMule en este proceso debe
#: serializarse con este lock.
EC_LOCK = threading.Lock()

ALREADY_COMPLETED_REASON = "ya en completados de aMule (no se vuelve a bajar)"
JUDGE_MAX_TRIES = 3


def downloads_key(r) -> str:
    """Clave estable entre sondeos: hash aMule si existe, si no name|size."""
    try:
        h = getattr(r, "hash", None) or getattr(r, "ed2k_hash", None)
        if h:
            return str(h)
    except Exception:
        pass
    try:
        return f"{getattr(r, 'name', '')}|{getattr(r, 'size_human', '')}"
    except Exception:
        return str(id(r))


def pick_with_judge(ranked: list, judge_fn, max_tries: int = JUDGE_MAX_TRIES):
    """Elige el primer candidato no vetado por la IA (ver
    ai_title_fallback.judge_download_candidate). *ranked*: mejor primero.
    *judge_fn(cand)* -> dict {"verdict","reason"} o None (IA caída).
    Devuelve (elegido|None, notas:[str]): ok/dudoso/None descargan (lo
    dudoso se anota); "malo" prueba con el siguiente, como mucho
    *max_tries*. Si todos son malos, (None, motivos): bloquear con motivo.
    Puro (sin EC) para poder probarlo."""
    vetoes = []
    for cand in list(ranked or [])[:max(1, max_tries)]:
        try:
            res = judge_fn(cand)
        except Exception:
            res = None
        if res is None:
            return cand, vetoes
        verdict = str((res or {}).get("verdict") or "")
        reason = str((res or {}).get("reason") or verdict)
        if verdict in ("ok", "dudoso"):
            if verdict == "dudoso":
                vetoes.append(f"dudoso: {reason}")
            return cand, vetoes
        vetoes.append(reason)
    return None, vetoes


def _judge_or_keep(config, query: str, best, results: list, is_movie: bool,
                   expected_year, typical_size, max_size, excluded: set | None = None):
    """Pasa el mejor candidato por el juez IA (solo si está activado con
    key): devuelve (best|None, motivo_bloqueo). Sin IA, (best, "").
    "malo" prueba con los siguientes por score (máx. JUDGE_MAX_TRIES); si
    todos son malos bloquea con el motivo. Los hashes de *excluded* ni se
    juzgan (ya se descartaron al elegir). Nunca lanza."""
    try:
        ai_key = (config.get("ai_api_key", "") or "") if config.get("ai_fallback_enabled") else ""
    except Exception:
        ai_key = ""
    if not best or not ai_key or not results:
        return best, ""
    try:
        from core.ai_title_fallback import judge_download_candidate
        from core.download_quality import score_download
        from core.fmt import fmt_size
    except Exception:
        return best, ""
    size_hint = typical_size or max_size
    try:
        size_human = fmt_size(size_hint) if size_hint else ""
    except Exception:
        size_human = ""
    wanted = {"title": query, "year": expected_year or "", "is_movie": bool(is_movie),
              "expected_size": size_human}

    def judge_fn(cand):
        from core.ai_title_fallback import resolve_model
        return judge_download_candidate(
            wanted, {"name": getattr(cand, "name", ""), "size_human": getattr(cand, "size_human", "?"),
                     "sources": getattr(cand, "sources", "?")}, ai_key,
            model=resolve_model(config))

    try:
        from core.download_quality import eligible_results, _W
        # Solo lo que best_result también habría aceptado (techo de
        # Adelgazar, idiomas, porno...) y con la puntuación mínima.
        pool = [r for r in eligible_results([r for r in results if r is not None], is_movie, max_size)
                if _result_hash(r).lower() not in (excluded or set())
                and score_download(r, query, expected_year, is_movie, typical_size) >= _W("min_best_score")]
        ranked = sorted(pool,
                        # score_download no recibe max_size (el techo ya se aplicó al
                        # elegir): pasárselo lanzaba TypeError y, capturado abajo,
                        # el juez no llegó a ejecutarse NUNCA desde que existe.
                        key=lambda r: score_download(r, query, expected_year, is_movie,
                                                     typical_size),
                        reverse=True)
    except Exception:
        _log.exception("Juez IA: no se pudo ordenar candidatos de %r, se descarga sin juez", query)
        return best, ""
    if best not in ranked:
        ranked = [best] + ranked
    chosen, notes = pick_with_judge(ranked, judge_fn)
    for n in notes:
        _log.warning("Juez IA (%s): %s", query, n)
    if chosen is None:
        reason = "; ".join(notes)[:200] or "descartado por la IA"
        _log.warning("Juez IA bloquea descarga de %r: %s", query, reason)
        return None, f"IA: {reason}"
    if chosen is not best:
        _log.info("Juez IA: mejor (%s) vetado, se descarga %r", getattr(best, "name", "?"),
                  getattr(chosen, "name", "?"))
    return chosen, ""


def _excluded_hashes(exclude_hashes) -> set:
    try:
        return {str(h).lower() for h in (exclude_hashes or []) if str(h).strip()}
    except Exception:
        return set()


def _best_not_excluded(results, query, expected_year, is_movie, typical_size, max_size,
                       excluded: set):
    """best_result ignorando los hashes de *excluded* (Sustituir: no volver
    a elegir el archivo que ya está en el servidor). Sin hash conocido el
    candidato no se puede comparar y se conserva (fail-open). None si no
    queda nada elegible. Usa el best_result importado arriba."""
    pool = []
    for r in results or []:
        h = _result_hash(r).lower()
        if h and h in excluded:
            continue
        pool.append(r)
    return best_result(pool, query, expected_year, is_movie, typical_size, max_size) if pool else None


def ec_client_from_config(config, timeout: float = 10.0) -> EcClient:
    return EcClient(
        host=config.get("amule_host", "localhost"),
        port=config.get("amule_port", 4712),
        password=config.get("amule_password", ""),
        timeout=timeout,
    )


def _result_hash(best) -> str:
    try:
        raw = getattr(best, "_ec_hash", None)
        if raw and len(raw) == 16:
            return binascii.hexlify(raw).decode("ascii").lower()
    except Exception:
        pass
    return ""


def _already_completed(ec, best) -> bool:
    """aMule acepta (ok) una descarga que ya tiene en completados sin
    bajarla de nuevo -- ok engañoso. Mira si está en la cola; si no, en los
    compartidos (vía amulecmd)."""
    try:
        time.sleep(1.0)
        q = ec.get_download_queue() or []
        h = _result_hash(best)
        in_q = any(x.get("hash_hex", "").lower() == h for x in q) if h else False
        if not in_q and h:
            from core.amule_client import _find_amulecmd
            from core.ec_client import _no_console_kwargs
            exe = _find_amulecmd("")
            if exe:
                args = [exe, "-c", "show shared", "-h", ec.host, "-p", str(ec.port), "-P", ec.password]
                from core.amule_client import decode_console_output
                r = subprocess.run(args, capture_output=True, timeout=6, **_no_console_kwargs())
                if h in decode_console_output(r.stdout or b"").lower():
                    return True
    except Exception as e:
        # Sin verificación no se acusa: se sigue como no completado (la
        # descarga duplicada la acepta igual aMule, sin daño).
        _log.debug("Sin verificación de completado para %r: %s", getattr(best, "name", "?"), e)
    return False


NO_CASTELLANO_REASON = "sin candidato que declare castellano (doblaje no confirmado)"
CANDIDATES_LOGGED = 8


def _log_candidates(query: str, results: list, chosen, expected_year, is_movie, typical_size,
                    note: str = "") -> None:
    """Deja en app.log los candidatos de la búsqueda (los mejores por
    puntuación), para poder ver después qué opciones había y por qué se
    eligió uno -- real: Dragon Ball Daima 1x11/1x14 se bajaron en japonés y
    no había forma de saber si existía una versión en castellano."""
    try:
        from core.download_quality import score_download
        scored = sorted(((score_download(r, query, expected_year, is_movie, typical_size), r)
                         for r in (results or []) if r is not None),
                        key=lambda t: t[0], reverse=True)
        lines = [f"  {'→' if r is chosen else ' '} {sc:6.1f}  {getattr(r, 'size_human', '?'):>9}  "
                 f"{getattr(r, 'sources', '?'):>3} fuentes  {getattr(r, 'name', '?')}"
                 for sc, r in scored[:CANDIDATES_LOGGED]]
        more = f" (+{len(scored) - CANDIDATES_LOGGED} más)" if len(scored) > CANDIDATES_LOGGED else ""
        _log.info("Candidatos aMule para %r: %d resultado(s)%s%s\n%s", query, len(scored), more,
                  f" -- {note}" if note else "", "\n".join(lines) or "  (ninguno)")
    except Exception:
        _log.debug("No se pudieron registrar los candidatos de %r", query, exc_info=True)


def _effective_score_query(query: str, score_query: str | None, results) -> str:
    """Consulta con la que se puntúa: *score_query* (el nombre real de la
    serie, "Dragon Ball Daima 1x10") si algún resultado es de esa serie; si
    no, la buscada. Con una plantilla de búsqueda ("Daima") la puntuación
    exigía que el título del resultado fuera "Daima" y descartaba todos los
    "Dragon Ball Daima 1x10..." (real: 14 candidatos, ninguno elegible).
    Si los resultados se llaman como la plantilla (Tensura para Slime),
    se sigue puntuando con la consulta buscada."""
    if not score_query or score_query == query:
        return query
    try:
        from core.download_quality import _same_series_title
        if any(r is not None and _same_series_title(score_query, getattr(r, "name", "") or "")
               for r in results or []):
            return score_query
    except Exception:
        pass
    return query


def _record_origin(origin: dict | None, best) -> None:
    if not origin:
        return
    try:
        from core.download_origin import record
        record(getattr(best, "name", ""), origin.get("tmdb_id"), origin.get("media_type", ""),
               title=origin.get("title", ""), season=origin.get("season"),
               episode=origin.get("episode"), year=origin.get("year"),
               hash_hex=_result_hash(best))
    except Exception:
        _log.debug("No se pudo apuntar el origen de %r", getattr(best, "name", "?"), exc_info=True)


def auto_download(config, ec_lock, query: str, search_type: str | None = None,
                  typical_size: int | None = None, max_size: int | None = None,
                  is_movie: bool = False, expected_year: int | None = None,
                  exclude_hashes=(), require_castellano: bool = False,
                  score_query: str | None = None, origin: dict | None = None):
    """Busca *query* en aMule y descarga el mejor candidato
    (core.download_quality.best_result). Devuelve (ok, motivo, hash_hex,
    nombre_elegido). *max_size*: techo en bytes ("Adelgazar").
    *exclude_hashes*: hashes MD4 hex que no se pueden elegir (Sustituir:
    el archivo que ya está en el servidor, para no descargarlo idéntico)
    -- si solo hay esos, falla con motivo en vez de repetirlo.
    *require_castellano*: solo candidatos cuyo nombre declara castellano
    (download_quality.declares_castellano_audio) -- para capítulos sin
    doblaje confirmado, donde lo que no dice idioma suele ser la versión
    original; si no hay ninguno, falla con NO_CASTELLANO_REASON.
    *score_query*: la consulta sin plantilla de búsqueda, para puntuar
    (ver _effective_score_query).
    *origin*: la ficha que se buscaba ({tmdb_id, media_type, title,
    season, episode, year}); si la descarga se lanza se apunta con el
    nombre elegido para que AutoWatcher no tenga que adivinarla al
    subirla (ver core/download_origin.py)."""
    ec = ec_client_from_config(config)
    try:
        with ec_lock:
            try:
                ec.connect()
            except (EcConnectionError, EcAuthError, EcProtocolError, OSError):
                return False, "aMule no disponible", "", ""
            st = search_type or config.get("amule_search_type", "Kad")
            excluded = _excluded_hashes(exclude_hashes)
            best = None
            last_key = None
            only_excluded = False
            results: list = []
            sq = query
            try:
                # Se lee en vivo (aMule va llenando la lista); si el mejor se
                # repite dos sondeos seguidos se sale antes del límite.
                all_results: list = []
                for all_results in ec.iter_search(query, search_type=st, poll_interval=2.0, max_duration=20.0):
                    results = all_results
                    if require_castellano and results:
                        from core.download_quality import declares_castellano_audio
                        results = [r for r in results
                                   if r is not None and declares_castellano_audio(getattr(r, "name", ""))]
                    sq = _effective_score_query(query, score_query, all_results)
                    candidate = _best_not_excluded(results, sq, expected_year, is_movie,
                                                   typical_size, max_size, excluded) if results else None
                    if results and candidate is None and excluded:
                        only_excluded = True
                    if candidate is not None:
                        cand_key = downloads_key(candidate)
                        if last_key is not None and cand_key == last_key:
                            best = candidate
                            break
                        best = candidate
                        last_key = cand_key
                note = "solo los que declaran castellano (doblaje no confirmado)" if require_castellano else ""
                if best is None:
                    _log_candidates(sq, all_results, None, expected_year, is_movie, typical_size,
                                    note or "ninguno elegible")
                    if only_excluded:
                        return False, "solo está el mismo archivo que ya hay en el servidor", "", ""
                    if require_castellano and all_results:
                        _log.info("auto_download: %r sin candidato que declare castellano, no se descarga", query)
                        return False, NO_CASTELLANO_REASON, "", ""
                    return False, "no encontrado", "", ""
                best, block_reason = _judge_or_keep(config, sq, best, results, is_movie,
                                                    expected_year, typical_size, max_size, excluded)
                _log_candidates(sq, all_results, best, expected_year, is_movie, typical_size,
                                note if best is not None else (block_reason or note))
                if best is None:
                    return False, block_reason, "", ""
                ok, _raw = ec.download(best)
                if ok and _already_completed(ec, best):
                    return False, ALREADY_COMPLETED_REASON, _result_hash(best), best.name
                if ok:
                    _record_origin(origin, best)
                    return True, "", _result_hash(best), best.name
                return False, "aMule rechazó la descarga", "", best.name
            except (EcProtocolError, OSError) as e:
                # Con eD2k caído iter_search lanza: se devuelve como fallo
                # normal (el autocompletado lo deja en su backoff).
                return False, f"aMule: {e}", "", ""
            except Exception as e:
                # Cualquier otra cosa (parseo, bug): fallo normal, nunca
                # excepción hacia el worker (moría en silencio).
                _log.warning("auto_download: fallo inesperado en %r: %s", query, e)
                return False, f"aMule: {e}", "", ""
    finally:
        try:
            ec.close()
        except Exception:
            pass


# ── Tamaño típico de episodio en el FTP ──

TYPICAL_CACHE_TTL = 600        # s: evita repetir listados FTP en cada búsqueda
TYPICAL_MIN_SEASON_FILES = 3   # con menos ficheros la temporada no es representativa
TYPICAL_TARGET_FILES = 6       # ficheros a acumular del fallback antes de calcular

_typical_cache: dict = {}


def known_series_names_from_cache() -> set:
    """Nombres de todas las series de la caché de "Episodios que faltan" --
    universo para sibling_blocks_folder."""
    try:
        from core.missing_episodes_cache import load_cache
        cache = load_cache() or {}
    except Exception:
        return set()
    return {e.get("name") for e in cache.values() if isinstance(e, dict) and e.get("name")}


def connect_ftp_from_config(config):
    """Conexión FTP/SFTP NUEVA y propia (nunca compartida entre hilos) con
    los datos de Ajustes. Quien la recibe hace disconnect()."""
    ftp = make_client(config.get("ftp_protocol", "ftp"))
    ftp.connect(config.get("ftp_host", ""), int(config.get("ftp_port", 21)),
                config.get("ftp_user", ""), config.get("ftp_password", ""),
                config.get("ftp_use_tls", False))
    return ftp


def _typical_fingerprint(config) -> tuple:
    """Huella del servidor+categorías para la caché de típicos: si cambia
    de servidor o reorganiza raíces, los tamaños guardados no valen."""
    try:
        cats = config.get("ftp_categories", {"tv": []}) or {}
        roots = tuple(sorted(str(c.get("root", "")) for c in cats.get("tv", []) or []))
    except Exception:
        roots = ()
    try:
        return (config.get("ftp_host", ""), config.get("ftp_port", 21), roots)
    except Exception:
        return ("", 21, roots)


def typical_size_for_series(config, series_name: str, season: int | None = None,
                             cache: dict | None = None) -> int | None:
    """Tamaño típico de episodio ya en el servidor para *series_name* (solo
    FTP). Usa la temporada pedida si tiene >=3 ficheros (>10MB); si no,
    acumula las más recientes hasta >=6 -- así no se mezclan calidades
    distintas (T01 SD con T03 HD). Caché de 10 min por (serie, temporada)."""
    cache = _typical_cache if cache is None else cache
    series_name = (series_name or "").strip()
    _log.info("Típico %r T%s: inicio (solo FTP)", series_name, season if season is not None else "?")
    if not series_name:
        return None
    try:
        ckey = (normalize_series_name(series_name), season)
    except Exception:
        ckey = (series_name.lower(), season)
    now = time.time()
    fingerprint = _typical_fingerprint(config)
    if ckey in cache:
        stored = cache[ckey]
        try:
            ts, val = stored[0], stored[1]
            same_server = len(stored) < 3 or tuple(stored[2]) == tuple(fingerprint)
        except Exception:
            ts, val, same_server = 0, None, False
        if same_server and now - ts < TYPICAL_CACHE_TTL:
            return val
    own_ftp = make_client(config.get("ftp_protocol", "ftp"))
    try:
        try:
            own_ftp.connect(config.get("ftp_host", ""), int(config.get("ftp_port", 21)),
                            config.get("ftp_user", ""), config.get("ftp_password", ""),
                            config.get("ftp_use_tls", False))
        except Exception as e:
            _log.info("Típico %r: no se pudo conectar al FTP (%s)", series_name, e)
            return None
        if not own_ftp.is_connected():
            _log.info("Típico %r: FTP no conectado", series_name)
            return None
        norm_target = normalize_series_name(series_name)
        # sibling_blocks_folder: no reclamar por parecido la carpeta exacta
        # de otra serie conocida; spinoff_blocks_folder: ni la de otra
        # entrega de la franquicia aunque no sea conocida. La carpeta exacta
        # gana a cualquier parecida. Real: el típico de "Dragon Ball Daima"
        # salía de "Dragon Ball." (193 MB contra ~500 MB de los suyos) y el
        # 1x10 en castellano de 675 MB quedaba por debajo del umbral.
        sibling_names = known_series_names_from_cache() | {series_name}
        cat, folder_name = None, None
        loose = None
        for c in config.get("ftp_categories", {"tv": []}).get("tv", []):
            root_try = c.get("root", "")
            if not root_try:
                continue
            try:
                for d in own_ftp.list_dirs(root_try) or []:
                    nd = normalize_series_name(d)
                    if nd == norm_target:
                        cat, folder_name = c, d
                        break
                    if (loose is None
                            and (series_similarity(norm_target, nd, strict=False) >= 0.85
                                 or norm_target in nd or nd in norm_target)
                            and not sibling_blocks_folder(series_name, d, sibling_names)
                            and not spinoff_blocks_folder(series_name, d)):
                        loose = (c, d)
                if cat:
                    break
            except Exception:
                continue
        if not cat and loose:
            cat, folder_name = loose
        if not cat or not folder_name:
            _log.info("Típico %r (norm %r): no se encontró carpeta en FTP", series_name, norm_target)
            return None
        root = cat.get("root", "")
        template = cat.get("template", "{serie}/")
        try:
            season_path = own_ftp.build_remote_path(root.rstrip("/") + "/" + template, folder_name, 1, "", "tv")
            from pathlib import PurePosixPath
            target_path = str(PurePosixPath(season_path).parent).rstrip("/")
        except Exception as e:
            _log.info("Típico %r: no se pudo construir ruta (%s)", series_name, e)
            return None
        try:
            subdirs = own_ftp.list_dirs(target_path) or []
        except Exception as e:
            _log.info("Típico %r: no se pudo listar %s (%s)", series_name, target_path, e)
            return None
        per_season = {}
        for sd in subdirs:
            m = re.search(r"(\d{1,2})", sd)
            if m and "temporada" in sd.lower():
                try:
                    per_season[int(m.group(1))] = sd
                except ValueError:
                    continue
        if not per_season:
            _log.info("Típico %r: sin carpetas de temporada en %s", series_name, target_path)
            return None

        def _sizes_of(snum):
            p = f"{target_path.rstrip('/')}/{per_season[snum]}"
            try:
                files = own_ftp.list_files_with_sizes(p) or []
            except Exception as e:
                _log.info("Típico %r: no se pudo listar %s (%s)", series_name, p, e)
                return []
            out = []
            for _name, sz in files:
                try:
                    if sz and int(sz) > 10 * 1024 * 1024:   # >10MB, sin samples
                        out.append(int(sz))
                except Exception:
                    continue
            return out

        sizes, used = [], []
        if season is not None and season in per_season:
            sizes = _sizes_of(season)
            if len(sizes) >= TYPICAL_MIN_SEASON_FILES:
                used = [season]
            else:
                sizes = []
        if not sizes:
            for snum in sorted(per_season.keys(), reverse=True):
                if len(used) >= 4:
                    break
                if snum in used:
                    continue
                got = _sizes_of(snum)
                if got:
                    used.append(snum)
                    sizes.extend(got)
                if len(sizes) >= TYPICAL_TARGET_FILES:
                    break
        if not sizes:
            _log.info("Típico %r T%s: sin tamaños", series_name, season if season is not None else "?")
            return None
        try:
            from core.download_quality import _typical_from_sizes
            res = _typical_from_sizes(sizes)
            _log.info("Típico %r T%s: %d ficheros de T%s -> %s MB", series_name,
                      season if season is not None else "?", len(sizes), used,
                      f"{res/1024/1024:.0f}" if res else "None")
            cache[ckey] = (now, res, _typical_fingerprint(config))
            return res
        except Exception as e:
            _log.info("Típico %r error calculando típico (%s)", series_name, e)
            return None
    except Exception as e:
        _log.info("Típico %r: fallo inesperado (%s)", series_name, e)
        return None
    finally:
        try:
            own_ftp.disconnect()
        except Exception:
            pass


def typical_size_for_query(config, query: str) -> int | None:
    """Típico a partir de una consulta "Serie 1x05" (botón ⬇ de un episodio)."""
    try:
        from core.download_quality import _series_title_before_episode, _parse_season_episode
        sname = _series_title_before_episode(query)
        se = _parse_season_episode(query)
        if sname:
            return typical_size_for_series(config, sname, se[0] if se else None)
    except Exception:
        pass
    return None
