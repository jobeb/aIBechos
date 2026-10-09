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
from core.series_match import normalize_series_name, series_similarity, sibling_blocks_folder
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
        return judge_download_candidate(
            wanted, {"name": getattr(cand, "name", ""), "size_human": getattr(cand, "size_human", "?"),
                     "sources": getattr(cand, "sources", "?")}, ai_key)

    try:
        ranked = sorted((r for r in results if r is not None
                         and _result_hash(r).lower() not in (excluded or set())),
                        key=lambda r: score_download(r, query, expected_year, is_movie,
                                                     typical_size, max_size),
                        reverse=True)
    except Exception:
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
    queda nada elegible."""
    from core.download_quality import best_result
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
        q = ec.get_download_queue()
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
    except Exception:
        pass
    return False


def auto_download(config, ec_lock, query: str, search_type: str | None = None,
                  typical_size: int | None = None, max_size: int | None = None,
                  is_movie: bool = False, expected_year: int | None = None,
                  exclude_hashes=()):
    """Busca *query* en aMule y descarga el mejor candidato
    (core.download_quality.best_result). Devuelve (ok, motivo, hash_hex,
    nombre_elegido). *max_size*: techo en bytes ("Adelgazar").
    *exclude_hashes*: hashes MD4 hex que no se pueden elegir (Sustituir:
    el archivo que ya está en el servidor, para no descargarlo idéntico)
    -- si solo hay esos, falla con motivo en vez de repetirlo."""
    ec = ec_client_from_config(config)
    try:
        with ec_lock:
            try:
                ec.connect()
            except (EcConnectionError, EcAuthError, OSError):
                return False, "aMule no disponible", "", ""
            st = search_type or config.get("amule_search_type", "Kad")
            excluded = _excluded_hashes(exclude_hashes)
            best = None
            last_key = None
            only_excluded = False
            try:
                # Se lee en vivo (aMule va llenando la lista); si el mejor se
                # repite dos sondeos seguidos se sale antes del límite.
                for results in ec.iter_search(query, search_type=st, poll_interval=2.0, max_duration=20.0):
                    candidate = _best_not_excluded(results, query, expected_year, is_movie,
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
                if best is None:
                    if only_excluded:
                        return False, "solo está el mismo archivo que ya hay en el servidor", "", ""
                    return False, "sin candidato que cumpla el umbral", "", ""
                best, block_reason = _judge_or_keep(config, query, best, results, is_movie,
                                                    expected_year, typical_size, max_size, excluded)
                if best is None:
                    return False, block_reason, "", ""
                ok, _raw = ec.download(best)
                if ok and _already_completed(ec, best):
                    return False, ALREADY_COMPLETED_REASON, _result_hash(best), best.name
                if ok:
                    return True, "", _result_hash(best), best.name
                return False, "aMule rechazó la descarga", "", best.name
            except (EcProtocolError, OSError) as e:
                # Con eD2k caído iter_search lanza: se devuelve como fallo
                # normal (el autocompletado lo deja en su backoff).
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
    if ckey in cache:
        ts, val = cache[ckey]
        if now - ts < TYPICAL_CACHE_TTL:
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
        # de otra serie conocida (Dragon Ball Daima / Dragon Ball).
        sibling_names = known_series_names_from_cache() | {series_name}
        cat, folder_name = None, None
        for c in config.get("ftp_categories", {"tv": []}).get("tv", []):
            root_try = c.get("root", "")
            if not root_try:
                continue
            try:
                for d in own_ftp.list_dirs(root_try) or []:
                    nd = normalize_series_name(d)
                    if ((series_similarity(norm_target, nd, strict=False) >= 0.85
                         or norm_target in nd or nd in norm_target)
                            and not sibling_blocks_folder(series_name, d, sibling_names)):
                        cat, folder_name = c, d
                        break
                if cat:
                    break
            except Exception:
                continue
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
            cache[ckey] = (now, res)
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
