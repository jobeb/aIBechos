"""
Lógica pura de la pestaña "Episodios que faltan": reconstruir filas desde la
caché, filtrarlas (búsqueda, ignoradas, IA, doblaje, completas), ordenarlas y
derivar los textos que se pintan (resumen, cabecera de temporada, líneas de
episodio). Sin dependencias de interfaz (la usa gui_qt/missing_episodes/), así
se puede probar sin levantar ventanas.

Formato de fila (dict): ver rows_from_cache.
"""

from __future__ import annotations

import datetime
import time as _time
from dataclasses import dataclass


# ── Utilidades de presentación ──

def fmt_air_date(iso_date) -> str:
    """"2024-03-15" (formato de TMDB) -> "15/03/2024"; "" si no hay fecha o
    no tiene el formato esperado."""
    if not iso_date:
        return ""
    try:
        return datetime.datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return ""


def season_header_text(season: int, n_eps: int, air_date: str, arrow: str = "") -> str:
    """"Temporada N (M episodios) -- estrenada DD/MM/AAAA". *arrow* (">"/"v")
    antepone un triángulo de texto (la vista Qt lo dibuja ella y no lo pasa)."""
    text = f"{arrow} " if arrow else ""
    text += f"Temporada {season} ({n_eps} episodios)"
    formatted = fmt_air_date(air_date)
    if formatted:
        text += f" -- estrenada {formatted}"
    return text


def dedupe_rows(rows: list) -> list:
    """Quita filas repetidas (misma serie dos veces, real: Dragon Ball
    Daima): una por tmdb_id (acepta int y str mezclados) y, para filas sin
    tmdb_id, una por nombre normalizado. Se queda con la primera aparición."""
    seen = set()
    out = []
    for r in rows or ():
        if not isinstance(r, dict):
            continue
        tid = r.get("tmdb_id")
        key = None
        if tid:
            try:
                key = ("id", int(tid))
            except (TypeError, ValueError):
                key = ("id", str(tid))
        else:
            try:
                from core.series_match import normalize_series_name
                norm = normalize_series_name(r.get("name", ""))
            except Exception:
                norm = str(r.get("name", "") or "").lower()
            if norm:
                key = ("name", norm)
        if key is None:
            out.append(r)   # sin identidad aprovechable: no se puede dedupicar
        elif key not in seen:
            seen.add(key)
            out.append(r)
    return out


# ── Reconstruir filas desde missing_episodes_cache.json ──

def ai_verdict_from_cache_entry(entry: dict):
    """"ai_verdict" persistido de una entrada cruda de la caché -- None si
    nunca se preguntó. "doblaje_castellano" se guarda con claves de texto
    (JSON no permite claves int), aquí se reconvierten a int."""
    ai_verdict = entry.get("ai_verdict")
    if not ai_verdict:
        return None
    if "doblaje_castellano" in ai_verdict:
        ai_verdict = dict(ai_verdict)
        ai_verdict["doblaje_castellano"] = {int(s): ep for s, ep in ai_verdict["doblaje_castellano"].items()}
    return ai_verdict


def rows_from_cache(cache: dict, complete: bool) -> list:
    """Filas de tabla desde la caché -- con complete=False las series con
    hueco (o temporadas que TMDB no conoce), con complete=True justo las
    contrarias. Mismo formato en ambos casos."""
    from core.missing_episodes import format_missing_summary, apply_season_split_filter
    results = []
    for key, entry in cache.items():
        if key == "_meta":
            continue
        missing = {int(k): v for k, v in (entry.get("missing") or {}).items()}
        unknown_seasons = set(entry.get("unknown_seasons", []))
        if bool(missing or unknown_seasons) == complete:
            continue
        name = entry.get("name", "")
        episode_titles = {int(s): {int(e): t for e, t in eps.items()}
                          for s, eps in (entry.get("episode_titles") or {}).items()}
        season_air_dates = {int(s): d for s, d in (entry.get("season_air_dates") or {}).items()}
        episode_air_dates = {int(s): {int(e): d for e, d in eps.items()}
                             for s, eps in (entry.get("episode_air_dates") or {}).items()}
        ignored_seasons = set(entry.get("ignored_seasons") or [])
        ignored_episodes = {int(s): set(eps) for s, eps in (entry.get("ignored_episodes") or {}).items()}
        expected = {int(k): v for k, v in (entry.get("expected") or {}).items()}
        present_season_counts = {int(k): v for k, v in (entry.get("present_season_counts") or {}).items()}
        # La caché guarda el hueco SIN filtrar -- el filtro de temporadas
        # partidas se aplica aquí, al reconstruir la fila para mostrar.
        missing, split_seasons = apply_season_split_filter(missing, expected)
        results.append({
            "tmdb_id": int(key), "name": name, "source": entry.get("source", ""),
            "server_id": entry.get("server_id"),
            "missing": missing, "summary": format_missing_summary(name, missing),
            "ignored": entry.get("ignored", False), "episode_titles": episode_titles,
            "split_seasons": split_seasons, "unknown_seasons": unknown_seasons,
            "expected_episodes": expected,
            "tmdb_season_counts": {s: len(eps) for s, eps in expected.items()},
            "server_season_counts": present_season_counts,
            "ai_verdict": ai_verdict_from_cache_entry(entry),
            "absolute_numbering": entry.get("absolute_numbering", False),
            "play_count": entry.get("play_count", 0),
            "last_played_ts": entry.get("last_played_ts"),
            "folder_name": entry.get("folder_name"),
            "first_air_date": entry.get("first_air_date", ""),
            "season_air_dates": season_air_dates,
            "episode_air_dates": episode_air_dates,
            "ignored_seasons": ignored_seasons,
            "ignored_episodes": ignored_episodes,
        })
    return results


def complete_count_in_cache(cache: dict) -> int:
    """Series sin ningún hueco ni temporadas desconocidas en la caché."""
    return sum(1 for key, entry in cache.items()
               if key != "_meta" and not (entry.get("missing") or entry.get("unknown_seasons")))


def merge_all_rows(results: list, complete_rows, hide_complete: bool) -> list:
    """Todas las filas candidatas ANTES de filtros: las de hueco y, si
    "Ocultar completas" está apagado, también las completas (sin repetir
    las que ya estén entre las de hueco)."""
    if hide_complete:
        return dedupe_rows(results)

    def _norm_id(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return v
    known = {_norm_id(r.get("tmdb_id")) for r in results}
    rows = list(results) + [r for r in (complete_rows or [])
                            if _norm_id(r.get("tmdb_id")) not in known]
    return dedupe_rows(rows)


# ── Doblaje castellano ──

def merged_dub_cut(cutoff_a: dict, cutoff_b: dict, season: int,
                   cutoff_c: dict | None = None, cutoff_d: dict | None = None):
    """Corte aplicable a *season* fusionando hasta cuatro fuentes
    (eldoblaje/wiki, Streaming Availability, Crunchyroll y RTVE): el MÍNIMO
    si varias la cubren, el de la única que la cubra, o None."""
    cuts = [cutoff_a, cutoff_b]
    if cutoff_c is not None:
        cuts.append(cutoff_c)
    if cutoff_d is not None:
        cuts.append(cutoff_d)
    vals = []
    for cut in cuts:
        v = (cut or {}).get(season, (cut or {}).get(str(season)))
        if v is None:
            continue
        try:
            vals.append(int(v))
        except (TypeError, ValueError):
            return v
    if not vals:
        return None
    return min(vals)


def dub_cutoff_for_series(dub_cache: dict, tmdb_id, ai_verdict=None) -> dict | None:
    """Corte {temporada: último_doblado}: veredicto de la IA si lo hay
    (sustituye a todo lo demás), si no fusión de las fuentes en caché, si no
    None (sin dato)."""
    if ai_verdict and "doblaje_castellano" in ai_verdict:
        return ai_verdict["doblaje_castellano"]
    try:
        cache_entry = (dub_cache or {}).get(str(tmdb_id), {})
        eld = (cache_entry.get("eldoblaje") or {}).get("cutoff") or {}
        st = (cache_entry.get("streaming") or {}).get("cutoff") or {}
        cr = (cache_entry.get("crunchyroll") or {}).get("cutoff") or {}
        rt = (cache_entry.get("rtve") or {}).get("cutoff") or {}
        merged = {}
        for s in set(list(eld.keys()) + list(st.keys()) + list(cr.keys()) + list(rt.keys())):
            try:
                season = int(s)
            except (TypeError, ValueError):
                continue
            cut = merged_dub_cut(eld, st, season, cr, rt)
            if cut is not None:
                merged[season] = cut
        return merged or None
    except Exception:
        return None


def _dub_episodes(dub_cache: dict, tmdb_id) -> dict:
    return ((dub_cache or {}).get(str(tmdb_id)) or {}).get("episodes") or {}


def dub_filtered(r: dict, dub_cache: dict) -> dict:
    """El "missing" de *r* recortado a solo los episodios con doblaje
    castellano CONFIRMADO (lo sin verificar también se recorta)."""
    from core.missing_episodes import filter_missing_by_dub_cutoff, filter_missing_by_spanish_dub
    ai_verdict = r.get("ai_verdict")
    if ai_verdict and "doblaje_castellano" in ai_verdict:
        return filter_missing_by_dub_cutoff(r["missing"], ai_verdict["doblaje_castellano"],
                                           hide_unverified=True)
    dub_episodes = _dub_episodes(dub_cache, r["tmdb_id"])
    cutoff = dub_cutoff_for_series(dub_cache, r.get("tmdb_id"))
    if cutoff:
        # Corte parcial: solo recorta sus temporadas; el resto cae al
        # veredicto por episodio.
        by_cutoff = filter_missing_by_dub_cutoff(r["missing"], cutoff, hide_unverified=True)
        uncovered = {s: eps for s, eps in r["missing"].items()
                     if cutoff.get(s, cutoff.get(str(s))) is None}
        by_ep = filter_missing_by_spanish_dub(uncovered, dub_episodes, hide_unverified=True)
        merged = dict(by_cutoff)
        for s, eps in by_ep.items():
            if s in merged:
                merged[s] = sorted(set(merged[s]) | set(eps))
            else:
                merged[s] = eps
        return merged
    return filter_missing_by_spanish_dub(r["missing"], dub_episodes, hide_unverified=True)


def unverified_eps(r: dict, dub_cache: dict) -> list:
    """[(temporada, episodio), ...] sin ningún veredicto de doblaje."""
    try:
        from core.missing_episodes import unverified_dub_episodes
        r = r or {}
        cutoff = dub_cutoff_for_series(dub_cache, r.get("tmdb_id"), r.get("ai_verdict") or {})
        return unverified_dub_episodes(r.get("missing") or {},
                                       _dub_episodes(dub_cache, r.get("tmdb_id")) or None, cutoff)
    except Exception:
        return []


def dub_absent_eps(r: dict, dub_cache: dict) -> list:
    """[(temporada, episodio), ...] con doblaje confirmado AUSENTE."""
    try:
        from core.missing_episodes import dub_status_for_episode
        r = r or {}
        cutoff = dub_cutoff_for_series(dub_cache, r.get("tmdb_id"), r.get("ai_verdict") or {})
        dub = _dub_episodes(dub_cache, r.get("tmdb_id"))
        out = []
        for season, eps in (r.get("missing") or {}).items():
            for ep in eps or []:
                if dub_status_for_episode(season, ep, dub, cutoff) == "absent":
                    out.append((season, ep))
        return out
    except Exception:
        return []


def dub_warning_eps(r: dict, season: int, eps: list, dub_cache: dict) -> list:
    """Episodios de *eps* sin doblaje ES confirmado -- para avisar antes de
    descargar con "Ocultar sin doblaje ES" activo (quien llama comprueba el
    interruptor)."""
    from core.missing_episodes import eps_without_confirmed_dub
    r = r or {}
    cutoff = dub_cutoff_for_series(dub_cache, r.get("tmdb_id"), r.get("ai_verdict") or {})
    cache_entry = (dub_cache or {}).get(str(r.get("tmdb_id")), {})
    return eps_without_confirmed_dub(season, eps, cache_entry.get("episodes"), cutoff)


def episode_dub_status(r: dict, season: int, ep: int, dub_cache: dict) -> str:
    """"absent"/"unverified"/"ok" de un episodio concreto (color de la línea)."""
    try:
        from core.missing_episodes import dub_status_for_episode
        cutoff = dub_cutoff_for_series(dub_cache, r.get("tmdb_id"), r.get("ai_verdict") or {})
        return dub_status_for_episode(season, ep, _dub_episodes(dub_cache, r.get("tmdb_id")), cutoff)
    except Exception:
        return "ok"


# ── Filtros ──

@dataclass
class MissingEpFilters:
    query: str = ""
    show_ignored: bool = False
    hide_ai_dismissed: bool = False
    hide_no_dub: bool = False
    hide_complete: bool = True


def is_ai_dismissed(r: dict) -> bool:
    v = r.get("ai_verdict")
    return bool(v) and v.get("veredicto") == "numeracion_distinta"


def visible_row(r: dict, filters: MissingEpFilters, dub_cache: dict):
    """Aplica los filtros activos -- devuelve la fila a pintar (o una COPIA
    con "missing" recortado por ignorados/doblaje), o None si no debe verse.
    Si tras recortar no queda ningún hueco (ni unknown_seasons), se oculta."""
    from core.missing_episodes import format_missing_summary, apply_ignored_filter

    query = (filters.query or "").strip().lower()
    if not (filters.show_ignored or not r.get("ignored")):
        return None
    if query and query not in (r.get("name") or "").lower():
        return None
    if filters.hide_ai_dismissed and is_ai_dismissed(r):
        return None

    # Serie completa: solo con "Ocultar completas" apagado, y los filtros
    # que recortan "missing" no le aplican (no hay nada que recortar).
    if not r["missing"] and not r.get("unknown_seasons"):
        return None if filters.hide_complete else r

    missing = r["missing"]
    if not filters.show_ignored and (r.get("ignored_seasons") or r.get("ignored_episodes")):
        orig_missing = missing
        missing = apply_ignored_filter(missing, r.get("ignored_seasons"), r.get("ignored_episodes"))
        if not missing:
            # Todos los huecos ignorados: ocultar aunque tenga unknown_seasons.
            if orig_missing:
                return None
            if not r.get("unknown_seasons"):
                return None

    if filters.hide_no_dub:
        r_for_dub = r if missing is r["missing"] else dict(r, missing=missing)
        missing = dub_filtered(r_for_dub, dub_cache)
        if not missing and not r.get("unknown_seasons"):
            return None

    if missing is r["missing"]:
        return r
    display = dict(r)
    display["missing"] = missing
    display["summary"] = format_missing_summary(r["name"], missing) if missing else r["summary"]
    return display


def dub_hidden_rows(results: list, dub_cache: dict) -> list:
    """Series que "Ocultar sin doblaje ES" deja sin nada visible (quien
    llama comprueba que el interruptor esté activo)."""
    hidden = [r for r in results
              if not dub_filtered(r, dub_cache) and not r.get("unknown_seasons")]
    return sorted(hidden, key=lambda r: r["name"].lower())


def ai_dismissed_rows(results: list) -> list:
    return sorted((r for r in results if is_ai_dismissed(r)), key=lambda r: r["name"].lower())


# ── Orden ──

def sort_key_fn(key: str):
    """Clave de orden por columna: "premiere" (ISO tal cual), "summary"
    (total de episodios que faltan), "trending" (core.trending) o nombre."""
    if key == "premiere":
        return lambda r: r.get("first_air_date") or ""
    if key == "summary":
        return lambda r: sum(len(eps) for eps in r["missing"].values())
    if key == "trending":
        from core.trending import trending_score
        return lambda r: trending_score(r.get("play_count", 0), r.get("last_played_ts"), _time.time())
    return lambda r: r["name"].lower()


# ── Textos derivados ──

def episode_lines(r: dict, tv_template: str) -> list:
    """(temporada, episodio, título, nombre_archivo, fecha) por episodio que
    falta -- el nombre sigue la plantilla de TV del usuario."""
    from core.api_client import MediaInfo
    from core.renamer import build_new_name
    episode_titles = r.get("episode_titles") or {}
    episode_air_dates = r.get("episode_air_dates") or {}
    lines = []
    for season in sorted(r["missing"]):
        for ep in r["missing"][season]:
            title = episode_titles.get(season, {}).get(ep, "")
            air_date = episode_air_dates.get(season, {}).get(ep, "")
            info = MediaInfo(tmdb_id=r["tmdb_id"], media_type="tv", title=r["name"],
                             original_title=r["name"], year="", season=season,
                             episode=ep, episode_title=title)
            try:
                name = build_new_name(info, tv_template, ext=".mkv")
            except ValueError:
                name = f"T{season}E{ep:02d}"
            else:
                name = name.rsplit(".", 1)[0]   # quitar la extensión ficticia
            lines.append((season, ep, title, name, air_date))
    return lines


def row_summary(r: dict, dub_cache: dict) -> tuple:
    """(texto, tono, tooltip) de la columna "Episodios que faltan". tono:
    "pending"/"error"/"warning"/"success" -- cada interfaz lo traduce a su
    color."""
    n_missing = sum(len(eps) for eps in r["missing"].values())
    n_seasons = len(r["missing"])
    ai_verdict = r.get("ai_verdict")
    has_warning = (bool(r.get("split_seasons")) or bool(r.get("unknown_seasons"))
                   or bool(r.get("absolute_numbering")))
    if n_missing:
        summary = f"{n_missing} episodio{'s' if n_missing != 1 else ''}"
        if n_seasons > 1:
            summary += f" ({n_seasons} temporadas)"
    elif r.get("unknown_seasons"):
        summary = "Posible ID equivocado"
    else:
        n_have = sum((r.get("server_season_counts") or {}).values())
        summary = "Completa" + (f" ({n_have} episodios)" if n_have else "")
    if has_warning:
        summary += " ⚠"
    if ai_verdict:
        summary += " 🤖"
    n_unverified = len(unverified_eps(r, dub_cache))
    n_absent = len(dub_absent_eps(r, dub_cache))
    if n_unverified:
        summary += f" ?{n_unverified}"
    if is_ai_dismissed(r):
        tone = "pending"
    elif n_absent:
        tone = "error"
    elif has_warning or n_unverified:
        tone = "warning"
    elif not n_missing:
        tone = "success"
    else:
        tone = "pending"
    tooltip = ""
    if n_unverified or n_absent:
        parts = []
        if n_absent:
            parts.append(f"{n_absent} sin doblaje castellano (confirmado ausente)")
        if n_unverified:
            parts.append(f"{n_unverified} sin verificar (ninguna fuente confirma ni desmiente)")
        tooltip = (f"Doblaje: {'; '.join(parts)}. El autocompletado no descarga episodios "
                   "sin castellano confirmado.")
    return summary, tone, tooltip


def row_warnings(r: dict) -> list:
    """Avisos que se muestran al desplegar la serie (texto plano)."""
    out = []
    if r.get("split_seasons"):
        seasons_txt = ", ".join(f"T{s}" for s in sorted(r["split_seasons"]))
        out.append(f"⚠ {seasons_txt}: podría no faltar de verdad -- TMDB cuenta como una sola "
                   "temporada algo que Netflix publicó en dos partes")
    if r.get("unknown_seasons"):
        seasons_txt = ", ".join(f"T{s}" for s in sorted(r["unknown_seasons"]))
        out.append(f"⚠ Tu servidor tiene la {seasons_txt} pero TMDB no la tiene registrada para "
                   "esta serie -- es muy probable que el ID de TMDB emparejado en Jellyfin/Plex "
                   "sea el equivocado. Revisa la identificación de esta serie en el propio servidor.")
    if r.get("absolute_numbering"):
        out.append("⚠ Tu servidor parece numerar los episodios de corrido (numeración absoluta, "
                   "típico de anime largo como Naruto Shippuden) en vez de reiniciar en cada "
                   "temporada -- se convirtió automáticamente para comparar, pero conviene "
                   "revisar el resultado.")
    if not r["missing"] and not r.get("unknown_seasons"):
        n_have = sum((r.get("server_season_counts") or {}).values())
        n_seasons_have = len(r.get("server_season_counts") or {})
        detalle = f" -- {n_have} episodios en {n_seasons_have} temporada(s)" if n_have else ""
        out.append(f"✓ No falta ningún episodio según TMDB{detalle}")
    return out


def ai_verdict_text(r: dict) -> str:
    """Texto del veredicto de la IA para el panel lateral ("" si no hay)."""
    verdict = r.get("ai_verdict")
    if not verdict:
        return ""
    dismissed = verdict.get("veredicto") == "numeracion_distinta"
    veredicto_txt = "probablemente NO falta nada real" if dismissed else "probablemente sí falta de verdad"
    motivo = verdict.get("motivo", "")
    text = f"🤖 Según la IA, {veredicto_txt}" + (f": {motivo}" if motivo else "")
    if not dismissed:
        affected = sorted(r.get("missing", {}).keys())
        if affected:
            etiqueta = "temporada" if len(affected) == 1 else "temporadas"
            text += f"\n📋 Huecos reales en la lista: {etiqueta} {', '.join(str(s) for s in affected)}"
    dub_cutoff = verdict.get("doblaje_castellano")
    if dub_cutoff is not None:
        if dub_cutoff:
            partes = ", ".join(f"T{s} hasta el {s}x{ep:02d}" for s, ep in sorted(dub_cutoff.items()))
            text += f"\n🎙 Doblaje castellano: {partes}"
        else:
            text += "\n🎙 Doblaje castellano: sin recorte encontrado (parece completo, o sin datos fiables)"
    return text


# ── Ignorados (persistencia en la caché) ──

def set_series_ignored(cache: dict, tmdb_id, ignored: bool) -> bool:
    """Marca/desmarca la serie como ignorada en *cache* (in place).
    Devuelve True si cambió algo (quien llama guarda)."""
    key = str(tmdb_id)
    if key not in cache:
        return False
    cache[key]["ignored"] = ignored
    return True


def set_season_ignored(cache: dict, tmdb_id, season: int, ignored: bool) -> bool:
    key = str(tmdb_id)
    if key not in cache:
        return False
    seasons = set(cache[key].get("ignored_seasons", []))
    if ignored:
        seasons.add(season)
    else:
        seasons.discard(season)
    cache[key]["ignored_seasons"] = sorted(seasons)
    return True


def set_episode_ignored(cache: dict, tmdb_id, season: int, episode: int, ignored: bool) -> bool:
    key = str(tmdb_id)
    if key not in cache:
        return False
    ignored_eps = {int(s): set(v) for s, v in (cache[key].get("ignored_episodes") or {}).items()}
    eps = ignored_eps.setdefault(season, set())
    if ignored:
        eps.add(episode)
    else:
        eps.discard(episode)
    if not eps:
        ignored_eps.pop(season, None)
    cache[key]["ignored_episodes"] = {str(s): sorted(v) for s, v in ignored_eps.items()}
    return True


def apply_season_ignored_to_row(r: dict, season: int, ignored: bool) -> None:
    seasons = set(r.get("ignored_seasons") or [])
    seasons.add(season) if ignored else seasons.discard(season)
    r["ignored_seasons"] = seasons


def apply_episode_ignored_to_row(r: dict, season: int, episode: int, ignored: bool) -> None:
    ignored_eps = {s: set(eps) for s, eps in (r.get("ignored_episodes") or {}).items()}
    eps = ignored_eps.setdefault(season, set())
    eps.add(episode) if ignored else eps.discard(episode)
    if not eps:
        ignored_eps.pop(season, None)
    r["ignored_episodes"] = ignored_eps


# ── Comprobación de doblaje: qué falta por comprobar y cómo se fusiona ──

def dub_pending_checks(results: list, dub_cache: dict, now: float | None = None) -> list:
    """Episodios (tmdb_id, name, season, ep) de series no ignoradas sin
    veredicto de doblaje en *dub_cache*, o con la entrada caducada."""
    from core.spanish_dub_cache import is_stale
    now = _time.time() if now is None else now
    pending = []
    for r in results:
        if r.get("ignored"):
            continue
        series_entry = (dub_cache or {}).get(str(r["tmdb_id"]), {})
        dub_episodes = {} if is_stale(series_entry, now) else series_entry.get("episodes", {})
        for season, eps in r["missing"].items():
            for ep in eps:
                if f"{season}x{ep:02d}" not in dub_episodes:
                    pending.append((r["tmdb_id"], r["name"], season, ep))
    return pending


def merge_dub_updates(dub_cache: dict, updates: dict) -> None:
    """Aplica (in place) el resultado de una comprobación de doblaje sobre
    la caché -- quien llama la guarda."""
    for key, entry in updates.items():
        existing = dub_cache.setdefault(key, {"spanish_available": None, "episodes": {}})
        if entry.get("spanish_available") is not None:
            existing["spanish_available"] = entry["spanish_available"]
        existing.setdefault("episodes", {}).update(entry["episodes"])
        for src in ("eldoblaje", "streaming", "crunchyroll", "rtve"):
            if entry.get(src):
                existing[src] = entry[src]


# ── Veredictos de la IA (core/missing_episodes_ai.py hace la llamada) ──

def batch_ai_payload(results: list) -> list:
    """Lo que se manda a Groq en la consulta por lotes tras un escaneo: solo
    recuentos por temporada (coste bajo con muchas series a la vez)."""
    return [
        {"tmdb_id": r["tmdb_id"], "name": r["name"],
         "tmdb_seasons": {str(k): v for k, v in r.get("tmdb_season_counts", {}).items()},
         "server_seasons": {str(k): v for k, v in r.get("server_season_counts", {}).items()}}
        for r in results
    ]


def single_show_ai_payload(r: dict, details: dict, info_doblaje_eldoblaje: str = "",
                           ftp_filenames: list | None = None) -> list:
    """Consulta manual de UNA serie: además de los recuentos, año, título
    original, países, géneros, números concretos de episodios que faltan y
    que hay (deja ver patrones que un recuento no distingue), el texto real
    de eldoblaje.com y los nombres de archivo reales del FTP."""
    present_episodes = {}
    for season, expected_eps in r.get("expected_episodes", {}).items():
        missing_eps = set(r.get("missing", {}).get(season, []))
        present = [ep for ep in expected_eps if ep not in missing_eps]
        if present:
            present_episodes[season] = present
    return [{
        "tmdb_id": r["tmdb_id"], "name": r["name"],
        "original_name": details.get("original_name") or "",
        "first_air_date": details.get("first_air_date") or "",
        "origin_country": details.get("origin_country") or [],
        "genres": [g.get("name") for g in details.get("genres", []) if g.get("name")],
        "tmdb_seasons": {str(k): v for k, v in r.get("tmdb_season_counts", {}).items()},
        "server_seasons": {str(k): v for k, v in r.get("server_season_counts", {}).items()},
        "missing_episodes": {str(k): v for k, v in r.get("missing", {}).items()},
        "present_episodes": {str(k): v for k, v in present_episodes.items()},
        "info_doblaje_eldoblaje": info_doblaje_eldoblaje,
        "ftp_filenames": ftp_filenames or [],
    }]


def persist_ai_verdicts(cache: dict, verdicts: dict) -> bool:
    """Guarda (in place) cada veredicto en la entrada de su serie, solo si
    ya existe. True si cambió algo (quien llama guarda)."""
    changed = False
    for tmdb_id, verdict in (verdicts or {}).items():
        key = str(tmdb_id)
        if key in cache:
            cache[key]["ai_verdict"] = verdict
            changed = True
    return changed


def shared_verdicts_to_apply(results: list, merged: dict, previous: dict) -> dict:
    """De los veredictos de doblaje compartidos (core/shared_dub_verdicts.py),
    los MÁS RECIENTES que la última sincronización para las series cargadas
    -- {tmdb_id: veredicto}. También los fija en cada fila de *results*."""
    to_apply = {}
    for r in results:
        key = str(r["tmdb_id"])
        shared = merged.get(key)
        if not shared:
            continue
        prev_checked_at = ((previous or {}).get(key) or {}).get("checked_at", 0)
        if shared.get("checked_at", 0) <= prev_checked_at:
            continue
        verdict = {k: v for k, v in shared.items() if k not in ("checked_at", "checked_by")}
        r["ai_verdict"] = verdict
        to_apply[r["tmdb_id"]] = verdict
    return to_apply
