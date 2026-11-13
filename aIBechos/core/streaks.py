"""
Racha de días consecutivos subiendo, por persona -- calculada AL VUELO a
partir del historial de actividad compartido (app._shared_activity_history,
mismo origen que ya usa el gráfico "Subidas en los últimos 30 días"). No es
un dato persistente aparte: no hay nada que sembrar/sincronizar/subir al
FTP para esto, solo esta función pura sobre datos que la app ya tiene en
memoria.

Nota: el historial de actividad compartido caduca a los 90 días (ver
core/activity_log.py; antes eran solo los últimos 500 registros), así que
una racha más larga que eso no se refleja entera.
"""

import datetime

from core.upload_stats import _normalize_key


def compute_streaks(entries: list, today: "datetime.date | None" = None) -> dict:
    """entries: historial de actividad (con "kind"/"status"/"person"/"ts").
    Devuelve {person_key: {"display_name", "streak_days"}} -- solo
    personas con una racha ACTIVA ahora mismo (streak_days >= 1).

    Una racha está "activa" si la persona subió algo HOY o AYER -- si ya
    subió hoy, se cuenta hacia atrás desde hoy; si hoy todavía no ha
    subido nada pero sí ayer, la racha sigue "viva" (no se rompe hasta
    que pase un día ENTERO sin ninguna subida) y se cuenta desde ayer.
    Si el último día con subida fue hace 2 días o más, la racha está
    rota y esa persona no aparece en el resultado."""
    if today is None:
        today = datetime.date.today()

    days_by_person: dict = {}
    for e in entries:
        if e.get("kind") != "subida" or e.get("status", "ok") != "ok":
            continue
        person = (e.get("person") or "").strip()
        if not person:
            continue
        key = _normalize_key(person)
        day = datetime.datetime.fromtimestamp(e.get("ts", 0)).date()
        bucket = days_by_person.setdefault(key, {"display_name": person, "days": set()})
        bucket["display_name"] = person
        bucket["days"].add(day)

    result = {}
    for key, bucket in days_by_person.items():
        days = bucket["days"]
        cursor = today if today in days else today - datetime.timedelta(days=1)
        if cursor not in days:
            continue   # ni hoy ni ayer -- racha rota
        streak = 0
        while cursor in days:
            streak += 1
            cursor -= datetime.timedelta(days=1)
        result[key] = {"display_name": bucket["display_name"], "streak_days": streak}
    return result


def top_streaks(streaks: dict, limit: int = 10) -> list:
    return sorted(streaks.values(), key=lambda e: e.get("streak_days", 0), reverse=True)[:limit]
