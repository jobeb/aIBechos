"""Ignorados de "Episodios que faltan" (serie entera, temporada o
episodio), comunes a todos los usuarios.

Antes vivían como campos personales dentro de missing_episodes_cache.json,
que se reescribe entera desde varios sitios, y se perdían:
- el escaneo copiaba la caché al empezar y la guardaba entera al acabar,
  pisando lo ignorado mientras tanto (minutos, o el de fondo cada 12 h);
- si Jellyfin/Plex no respondían, el escaneo quitaba de la caché todas las
  series "que ya no estaban", con sus ignorados;
- al fusionar la caché compartida se quitaban las series que no venían en
  la remota, y sus ignorados con ellas.

Ahora son un archivo propio (local + compartido en el FTP,
shared_data "episodios_ignorados") con una marca por cosa ignorada:

    {"<tmdb>": {...}, "<tmdb>/<temporada>": {...}, "<tmdb>/<temp>/<ep>": {...}}
    cada una {"on": bool, "ts": float, "by": str}

Quitar un ignorado no borra la marca: la deja "on": False con su hora, para
que al fusionar con otro equipo que aún la tenía puesta gane el cambio más
reciente (merge()). La caché del escaneo se sigue rellenando con los campos
"ignored"/"ignored_seasons"/"ignored_episodes" de siempre, pero siempre a
partir de aquí (apply_to_cache), así que nadie que la reescriba los pierde.
"""

import json
import threading
import time

from core.appdirs import app_data_dir
from core.atomic_write import write_json_atomic

_FILENAME = "missing_episodes_ignored.json"
_lock = threading.RLock()
_marks: dict | None = None


def mark_key(tmdb_id, season=None, episode=None) -> str:
    parts = [str(tmdb_id)]
    if season is not None:
        parts.append(str(int(season)))
        if episode is not None:
            parts.append(str(int(episode)))
    return "/".join(parts)


def _valid(m) -> bool:
    return isinstance(m, dict) and "on" in m


def _ts(m) -> float:
    try:
        return float(m.get("ts") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def merge(a: dict, b: dict) -> dict:
    """Dict NUEVO con, por cada marca, la más reciente de *a* y *b* (a
    igualdad, la de *a*). Ignora basura."""
    out = {k: v for k, v in (a or {}).items() if _valid(v)}
    for k, v in (b or {}).items():
        if not _valid(v):
            continue
        cur = out.get(k)
        if cur is None or _ts(v) > _ts(cur):
            out[k] = v
    return out


def set_mark(marks: dict, tmdb_id, season=None, episode=None, on: bool = True,
             by: str = "", now: float | None = None, until: float = 0) -> dict:
    """*until* (epoch, 0 = para siempre): el ignorado deja de contar
    solo a partir de esa hora (p. ej. "ignorar 30 días")."""
    out = dict(marks or {})
    mark = {"on": bool(on), "ts": now if now is not None else time.time(), "by": by or ""}
    if on and until:
        mark["until"] = float(until)
    out[mark_key(tmdb_id, season, episode)] = mark
    return out


def is_active(m, now: float | None = None) -> bool:
    """¿Esta marca ignora ahora mismo? (puesta y sin caducar)."""
    if not _valid(m) or not m.get("on"):
        return False
    try:
        until = float(m.get("until") or 0)
    except (TypeError, ValueError):
        until = 0.0
    return not until or (now if now is not None else time.time()) < until


def next_expiry(marks: dict, now: float | None = None) -> float:
    """Hora de la próxima caducidad pendiente (0 si no hay ninguna)."""
    ts = now if now is not None else time.time()
    pending = [float(m.get("until") or 0) for m in (marks or {}).values()
               if is_active(m, ts) and m.get("until")]
    return min(pending) if pending else 0.0


def get_mark(marks: dict, tmdb_id, season=None, episode=None) -> dict:
    m = (marks or {}).get(mark_key(tmdb_id, season, episode))
    return m if _valid(m) else {}


def _index(marks: dict, now: float | None = None) -> dict:
    """{tmdb: (serie, {temporadas}, {temporada: {episodios}})} de las
    marcas activas, en una sola pasada."""
    ts = now if now is not None else time.time()
    out = {}
    for k, m in (marks or {}).items():
        if not is_active(m, ts):
            continue
        parts = str(k).split("/")
        series, seasons, episodes = out.setdefault(parts[0], [False, set(), {}])
        try:
            nums = [int(x) for x in parts[1:]]
        except ValueError:
            continue
        if not nums:
            out[parts[0]][0] = True
        elif len(nums) == 1:
            seasons.add(nums[0])
        elif len(nums) == 2:
            episodes.setdefault(nums[0], set()).add(nums[1])
    return out


def fields_for(marks: dict, tmdb_id, now: float | None = None) -> tuple:
    """(serie_ignorada, {temporadas}, {temporada: {episodios}}) de *tmdb_id*."""
    series, seasons, episodes = _index(
        {k: v for k, v in (marks or {}).items()
         if k == str(tmdb_id) or str(k).startswith(f"{tmdb_id}/")}, now).get(
        str(tmdb_id), (False, set(), {}))
    return bool(series), set(seasons), {s: set(e) for s, e in episodes.items()}


def apply_to_cache(cache: dict, marks: dict, now: float | None = None) -> dict:
    """Pone en cada serie de *cache* (in place) los campos de ignorados
    según *marks*. Devuelve *cache*."""
    idx = _index(marks, now)
    for key, entry in (cache or {}).items():
        if key == "_meta" or not isinstance(entry, dict):
            continue
        series, seasons, episodes = idx.get(str(key), (False, set(), {}))
        entry["ignored"] = bool(series)
        entry["ignored_seasons"] = sorted(seasons)
        entry["ignored_episodes"] = {str(s): sorted(e) for s, e in sorted(episodes.items())}
    return cache


def apply_to_rows(rows: list, marks: dict, now: float | None = None) -> None:
    """Igual que apply_to_cache pero sobre las filas de la pestaña
    (temporadas/episodios como sets e ints)."""
    idx = _index(marks, now)
    for r in rows or []:
        if not isinstance(r, dict) or r.get("tmdb_id") is None:
            continue
        series, seasons, episodes = idx.get(str(r["tmdb_id"]), (False, set(), {}))
        r["ignored"] = bool(series)
        r["ignored_seasons"] = set(seasons)
        r["ignored_episodes"] = {s: set(e) for s, e in episodes.items()}


def describe(mark: dict) -> str:
    """" Ignorado por X el 15/11/2026, hasta el 15/12/2026." (o "")."""
    if not is_active(mark):
        return ""
    parts = []
    by = (mark.get("by") or "").strip()
    try:
        ts = float(mark.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    if by:
        parts.append(f"por {by}")
    if ts > 86400:   # las traídas de la caché antigua no tienen hora real
        parts.append("el " + time.strftime("%d/%m/%Y", time.localtime(ts)))
    text = (" Ignorado " + " ".join(parts) + ".") if parts else ""
    if mark.get("until"):
        text += " Vuelve a contar el " + time.strftime(
            "%d/%m/%Y", time.localtime(float(mark["until"]))) + "."
    return text


def marks_from_cache(cache: dict, now: float = 1.0) -> dict:
    """Marcas sacadas de los campos de ignorados que ya tenga *cache* (la
    primera vez, para no perder lo que se había ignorado antes). Hora
    mínima: cualquier cambio real posterior, de cualquier equipo, gana."""
    marks = {}
    for key, entry in (cache or {}).items():
        if key == "_meta" or not isinstance(entry, dict):
            continue
        on = {"on": True, "ts": now, "by": ""}
        if entry.get("ignored"):
            marks[mark_key(key)] = dict(on)
        for s in entry.get("ignored_seasons") or []:
            try:
                marks[mark_key(key, s)] = dict(on)
            except (TypeError, ValueError):
                pass
        for s, eps in (entry.get("ignored_episodes") or {}).items():
            for e in eps or []:
                try:
                    marks[mark_key(key, s, e)] = dict(on)
                except (TypeError, ValueError):
                    pass
    return marks


# ── Archivo local ──

def _path():
    return app_data_dir() / _FILENAME


def load_marks() -> dict:
    """Marcas locales (copia). La primera vez, si no hay archivo, se crean
    a partir de los ignorados que ya hubiera en la caché del escaneo."""
    global _marks
    with _lock:
        if _marks is None:
            path = _path()
            data = None
            if path.exists():
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    data = None
            if isinstance(data, dict):
                _marks = merge({}, data)
            else:
                from core.missing_episodes_cache import _read_from_disk
                _marks = marks_from_cache(_read_from_disk())
                if _marks and not path.exists():
                    _write(_marks)
        return dict(_marks)


def _write(marks: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, marks)


def save_marks(marks: dict) -> None:
    global _marks
    with _lock:
        _marks = dict(marks)
        _write(_marks)


def _reset_for_tests() -> None:
    global _marks
    with _lock:
        _marks = None
