"""
Caché persistente del detector de episodios que faltan (ver
core/missing_episodes.py y core/missing_ep_scan.py::_scan_missing_episodes). El primer
escaneo completo es caro (una llamada por serie + una por temporada) --
esto guarda el resultado para que los siguientes escaneos solo tengan que
comprobar qué ha cambiado de verdad, en vez de repetir todo el trabajo cada
vez.

Formato: {tmdb_id_como_texto: {"name", "source", "server_id",
"last_episode_id", "expected", "missing", "ignored", "ai_verdict",
"first_air_date", "season_air_dates", "episode_air_dates"},
"_meta": {"last_scan_ts": float, "scanned_by": str}}

"first_air_date"/"season_air_dates"/"episode_air_dates" (fechas de estreno
de la serie/temporada/episodio, formato ISO "AAAA-MM-DD" tal cual las da
TMDB) ya venían de fábrica en las mismas respuestas de TMDB que este
escaneo ya pedía (get_tv_details/get_season_episodes) -- antes se
descartaban sin usar. "season_air_dates"/"episode_air_dates" siguen el
mismo patrón anidado que "episode_titles" (claves de texto por ser JSON).

"ai_verdict" ({"veredicto", "motivo", "doblaje_castellano"?}, ver
core/missing_episodes_ai.py) se guarda aparte, en gui/app.py::
_persist_ai_verdicts -- justo al recibir la respuesta de la IA, no como
parte del escaneo normal: un reescaneo completo reconstruye la entrada
entera de cada serie (sin ai_verdict, para no arrastrar un veredicto que
podría haber quedado desactualizado si el hueco real cambió), así que
solo sobrevive entre sesiones si nadie ha vuelto a escanear esa serie a
fondo desde que se preguntó.

Compartido entre clientes vía FTP (ver gui/app.py::
_push_missing_episodes_to_ftp/_sync_missing_episodes_from_ftp), igual que
"Liberar espacio" -- pero aquí SÍ hay dos campos por serie que son
personales y NUNCA viajan por la red: "ignored" (que esta serie concreta
no le interesa a ESTE cliente -- otro cliente puede tener una opinión
distinta) y "ai_verdict" (que ya tiene su propio canal de compartición
aparte, ver core/shared_dub_verdicts.py -- incluirlo aquí también
duplicaría esa sincronización). Los ignorados ya no son personales: son comunes a
todos y viajan en su propio archivo (core/missing_ep_ignored.py); aquí
solo se rellenan a partir de él al leer/guardar (_with_ignored). strip_personal_fields()/
merge_remote_into_local() son las dos funciones puras que separan esos
campos personales del resto al subir/bajar."""

import copy
import json
import threading
import time

from core.appdirs import app_data_dir
from core.atomic_write import write_json_atomic

_FILENAME = "missing_episodes_cache.json"
_cache: dict | None = None


def _path():
    return app_data_dir() / _FILENAME


def _read_from_disk() -> dict:
    path = _path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_next_expiry = 0.0   # próxima caducidad de un "ignorar N días" ya aplicada


def _with_ignored(cache: dict) -> dict:
    """Los ignorados salen SIEMPRE de core/missing_ep_ignored.py (comunes
    a todos los usuarios): quien reescriba la caché con una copia vieja
    (el escaneo, la fusión con la compartida) ya no los pierde."""
    global _next_expiry
    try:
        from core import missing_ep_ignored as _ign
        marks = _ign.load_marks()
        _ign.apply_to_cache(cache, marks)
        _next_expiry = _ign.next_expiry(marks)
    except Exception:
        pass
    return cache


def load_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = _with_ignored(_read_from_disk())
    elif _next_expiry and time.time() >= _next_expiry:
        # Un "ignorar 30 días" acaba de caducar: vuelve a contar ya.
        _with_ignored(_cache)
    return _cache


def save_cache(cache: dict) -> None:
    global _cache
    _cache = _with_ignored(cache)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, cache)


#: Un solo punto de escritura: los cambios pequeños (episodio subido, serie
#: borrada, veredicto de la IA...) pasan por update_cache() con candado y
#: quedan en un diario. Los escaneos, que tardan minutos y construyen su
#: caché a partir de una copia, guardan con save_scan_result(), que vuelve a
#: aplicar lo del diario posterior a su inicio: antes, el guardado del
#: escaneo deshacía esos cambios (p. ej. un episodio subido mientras
#: escaneaba volvía a salir como faltante).
_write_lock = threading.RLock()
_generation = 0
_journal: list = []          # [(generación, fn)], los últimos _JOURNAL_MAX
_JOURNAL_MAX = 500


def generation() -> int:
    """Marca de "ahora" para save_scan_result (llamar al empezar)."""
    with _write_lock:
        return _generation


def update_cache(fn, journal: bool = True) -> bool:
    """Aplica *fn(cache)* (muta, devuelve True si cambió algo) sobre la
    caché actual, con candado, y la guarda. *journal*: apuntarlo para
    reaplicarlo sobre el resultado de un escaneo en curso (False para lo
    que ya trae datos completos, como la fusión con la compartida)."""
    global _generation
    with _write_lock:
        cache = copy.deepcopy(load_cache())
        try:
            changed = bool(fn(cache))
        except Exception:
            return False
        if not changed:
            return False
        save_cache(cache)
        _generation += 1
        if journal:
            _journal.append((_generation, fn))
            del _journal[:-_JOURNAL_MAX]
        return True


def save_scan_result(cache: dict, since: int) -> None:
    """Guarda la caché construida por un escaneo que empezó en *since*
    (generation()), con los cambios pequeños hechos mientras tanto."""
    global _generation
    with _write_lock:
        for gen, fn in list(_journal):
            if gen > since:
                try:
                    fn(cache)
                except Exception:
                    pass
        save_cache(cache)
        _generation += 1


def remove_missing_episode_from_cache(cache: dict, tmdb_id: int, season: int, episode: int) -> bool:
    """Igual que core.missing_episodes.remove_missing_episode, pero sobre el
    dict crudo tal cual se persiste aquí (claves de texto, listas de
    episodios) -- para que la marca sobreviva a un reinicio sin depender de
    otro escaneo completo. Mutación en sitio. Devuelve True si de verdad
    había algo que quitar; el llamador es responsable de save_cache()
    después."""
    entry = cache.get(str(tmdb_id))
    if entry is None:
        return False
    missing = entry.get("missing", {})
    season_key = str(season)
    if season_key not in missing or episode not in missing[season_key]:
        return False
    missing[season_key].remove(episode)
    if not missing[season_key]:
        del missing[season_key]
    return True


def remove_series_from_cache(cache: dict, tmdb_id: int) -> bool:
    """Quita la entrada ENTERA de *cache* para tmdb_id -- usado cuando la
    serie completa se borra del servidor (ver
    core.missing_episodes.remove_series, la versión para la lista en
    memoria). Mutación en sitio. El llamador es responsable de
    save_cache() después."""
    key = str(tmdb_id)
    if key not in cache:
        return False
    del cache[key]
    return True


_PERSONAL_FIELDS = ("ignored", "ignored_seasons", "ignored_episodes", "ai_verdict")


def strip_personal_fields(cache: dict) -> dict:
    """Copia de *cache* sin los campos personales de cada serie (ver el
    docstring del módulo) -- lo que de verdad se sube al FTP compartido.
    "_meta" se conserva tal cual (no es una serie, no tiene campos
    personales)."""
    result = {}
    for key, entry in cache.items():
        if key == "_meta":
            result[key] = entry
            continue
        result[key] = {k: v for k, v in entry.items() if k not in _PERSONAL_FIELDS}
    return result


def merge_remote_into_local(local_cache: dict, remote_cache: dict) -> dict:
    """Aplica *remote_cache* (ya sin campos personales, ver
    strip_personal_fields) sobre *local_cache* -- cada serie se reemplaza
    con los datos remotos, pero conservando "ignored"/"ai_verdict" de la
    entrada LOCAL si ya existían (esos dos campos nunca viajan por la
    red). Las series que ya no aparecen en remote_cache se quitan: lo
    remoto es la fuente de verdad de "qué series se están vigilando" --
    si ya no está ahí es porque se borró en otro cliente (ver
    remove_series_from_cache). "_meta" se toma tal cual de remote_cache."""
    result = {}
    for key, remote_entry in remote_cache.items():
        if key == "_meta":
            result[key] = remote_entry
            continue
        merged = dict(remote_entry)
        local_entry = local_cache.get(key)
        if local_entry:
            for field in _PERSONAL_FIELDS:
                if field in local_entry:
                    merged[field] = local_entry[field]
        merged.setdefault("ignored", False)
        merged.setdefault("ai_verdict", None)
        result[key] = merged
    return result


def _reset_cache_for_tests() -> None:
    global _cache, _next_expiry, _generation
    _cache = None
    _next_expiry = 0.0
    with _write_lock:
        _generation = 0
        _journal.clear()
