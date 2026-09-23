"""
Lógica Unstuck para descargas aMule colgadas.

Criterio: tiempo descargando + avance (bytes/% o sources).
Si atascado → buscar alternativa; si alternativa (o primaria) completa → borrar la otra;
si no → esperar backoff creciente y reintentar.

Estado persistido en config.py: missing_ep_auto_downloads
  {tmdb_id_str: {season_str: {ep_str: {primary_hash, primary_started_ts, primary_last_bytes, primary_last_progress_ts,
                                       alt_hash, alt_started_ts, alt_last_bytes, alt_last_progress_ts,
                                       stuck_tries, last_alt_ts}}}}
"""

import time as _time

# Umbrales por defecto si no hay queue real (fallback tiempo)
DEFAULT_STUCK_MIN_AGE_S = 2 * 3600  # 2h mínimo descargando antes de considerar atascado
DEFAULT_STALLED_NO_PROGRESS_S = 60 * 60  # 1h sin avance

RETRY_BASE_S = 30 * 60  # 30m
RETRY_FACTOR = 2
RETRY_MAX_S = 8 * 3600  # 8h (usa unstuck_backoff_max si existe)


def _retry_wait_for(tries: int, base_s: int = RETRY_BASE_S, max_s: int = RETRY_MAX_S) -> float:
    return min(base_s * (RETRY_FACTOR ** max(tries, 1) - 1) if False else base_s * (RETRY_FACTOR ** (max(tries, 1) - 1)), max_s)


def retry_wait_for(tries: int, base_s: int = RETRY_BASE_S, max_s: int = RETRY_MAX_S) -> float:
    """Backoff exponencial para reintento de alternativa: 30m, 60m, 2h, 4h, 8h..."""
    return min(base_s * (RETRY_FACTOR ** (max(tries, 1) - 1)), max_s)


def is_download_stuck(started_ts: float, last_progress_ts: float, last_bytes: int, cur_bytes: int,
                      cur_sources: int, now: float,
                      min_age_s: float = DEFAULT_STUCK_MIN_AGE_S,
                      stalled_s: float = DEFAULT_STALLED_NO_PROGRESS_S) -> bool:
    """
    True si la descarga lleva suficiente tiempo y no avanza.
    - Si cur_bytes == last_bytes y now - last_progress_ts > stalled_s → atascado
    - Si sources == 0 y now - started_ts > min_age_s → atascado
    - Si now - started_ts > min_age_s y cur_bytes == 0 → atascado
    """
    if not started_ts:
        return False
    age = now - started_ts
    if age < min_age_s:
        return False
    # Sin fuentes durante toda la edad mínima
    if cur_sources is not None and cur_sources == 0:
        return True
    # Sin progreso
    if cur_bytes is not None and last_bytes is not None:
        if cur_bytes <= last_bytes and (now - last_progress_ts) > stalled_s:
            return True
        # Nunca ha avanzado nada
        if cur_bytes == 0 and last_bytes == 0 and age > stalled_s:
            return True
    return False


def should_try_alternative(rec: dict, queue_info: dict | None, now: float,
                           unstuck_cfg: dict) -> bool:
    """
    Decide si hay que buscar una alternativa para este episodio.

    rec: entrada de missing_ep_auto_downloads para el episodio
    queue_info: {"primary": {"bytes": int, "sources": int, "percent": float} | None,
                 "alt": {...} | None}  o None si no hay EC queue
    unstuck_cfg: {"unstuck_enabled": bool, "unstuck_backoff_base_minutes": int, ...}
    """
    if not rec:
        return False
    if not unstuck_cfg.get("unstuck_enabled"):
        return False

    base_s = int(unstuck_cfg.get("unstuck_backoff_base_minutes", 30) or 30) * 60
    max_s = int(unstuck_cfg.get("unstuck_backoff_max_minutes", 480) or 480) * 60
    max_tries = int(unstuck_cfg.get("unstuck_max_retries", 5) or 5)

    stuck_tries = int(rec.get("stuck_tries", 0) or 0)
    if stuck_tries >= max_tries:
        return False

    last_alt_ts = float(rec.get("last_alt_ts", 0) or 0)
    if last_alt_ts and (now - last_alt_ts) < retry_wait_for(stuck_tries, base_s, max_s):
        return False

    # Si ya hay alternativa lanzada y sigue viva, no lanzar otra hasta que toque backoff
    # Pero si primaria está atascada, sí toca alternativa
    primary = queue_info.get("primary") if queue_info else None
    alt = queue_info.get("alt") if queue_info else None

    # Sin queue real → usar tiempo desde started
    if queue_info is None or (primary is None and alt is None):
        started = float(rec.get("primary_started_ts", 0) or rec.get("started_ts", 0) or 0)
        last_prog = float(rec.get("primary_last_progress_ts", started) or started)
        last_bytes = int(rec.get("primary_last_bytes", 0) or 0)
        # Si no tenemos métricas, usar edad como proxy: stuck si > min_age
        min_age = int(unstuck_cfg.get("unstuck_file_ttl_minutes", 120) or 120) * 60
        # Usar ttl como min_age para fallback sin queue
        return is_download_stuck(started, last_prog, last_bytes, last_bytes, 0, now,
                                 min_age_s=min_age, stalled_s=base_s)

    # Con queue: mirar primaria si existe y no tiene alternativa completa
    if primary is not None:
        started = float(rec.get("primary_started_ts", 0) or 0)
        last_prog = float(rec.get("primary_last_progress_ts", started) or started)
        last_bytes = int(rec.get("primary_last_bytes", 0) or 0)
        cur_bytes = int(primary.get("bytes", last_bytes) or last_bytes)
        sources = int(primary.get("sources", 0) or 0)
        # Actualizar last_progress si hubo avance (el llamador lo persiste)
        if cur_bytes > last_bytes:
            return False  # está avanzando, no atascado
        if is_download_stuck(started, last_prog, last_bytes, cur_bytes, sources, now):
            return True

    # Si no hay primaria pero hay alt y está atascada, también podría reintentar
    # (no necesario para spec, pero lo dejamos)
    return False


def next_alternative_query(series_name: str, season: int, episode: int, templates: dict) -> str:
    """Construye query alternativa. Usa mismo template pero si existe, varía search_type fuera."""
    from core.amule_search import build_amule_query
    # Por ahora simplemente reusa el mismo builder; el llamador puede probar otro search_type
    return build_amule_query(series_name, season, episode, templates=templates)


def handle_completion(rec: dict, completed_which: str) -> tuple[str | None, bool]:
    """
    Cuando una de las dos descargas completa, decide qué hash borrar.
    completed_which: "primary" o "alt"
    Returns: (hash_a_borrar | None, should_clear_rec)
    should_clear_rec True si el episodio ya está completo (ya no falta) y se puede limpiar todo.
    """
    if completed_which == "primary":
        alt_hash = rec.get("alt_hash")
        return alt_hash, False  # borrar alternativa, mantener rec hasta que episodio deje de faltar
    if completed_which == "alt":
        prim = rec.get("primary_hash")
        return prim, False
    return None, False


def mark_progress(rec: dict, which: str, cur_bytes: int, now: float) -> dict:
    """Actualiza last_bytes/last_progress_ts si hubo avance. Devuelve rec mutado."""
    key_bytes = f"{which}_last_bytes"
    key_ts = f"{which}_last_progress_ts"
    last = int(rec.get(key_bytes, 0) or 0)
    if cur_bytes is not None and cur_bytes > last:
        rec[key_bytes] = cur_bytes
        rec[key_ts] = now
    return rec
