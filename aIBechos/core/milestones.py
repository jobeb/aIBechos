"""
Insignias por hitos de GB subidos -- puramente derivadas del total actual
de cada persona (core/upload_stats.py), sin ningún estado propio.

A propósito NO es un sistema de "acabas de cruzar tal hito, aviso único":
eso necesitaría guardar qué hitos ya se avisaron por persona y
sincronizarlo entre clientes para no repetir el aviso -- una insignia
calculada al vuelo a partir del total actual no tiene ese problema (no
hace falta recordar nada) y sirve igual de bien como motivación visible
en el ranking de subidores.
"""

_GB = 1024 ** 3

# Ascendente -- (umbral en bytes, emoji, etiqueta).
_TIERS = (
    (10 * _GB, "🥉", "10 GB"),
    (50 * _GB, "🥈", "50 GB"),
    (100 * _GB, "🥇", "100 GB"),
    (500 * _GB, "🏆", "500 GB"),
    (1024 * _GB, "💎", "1 TB"),
)


def milestone_for(total_bytes: int):
    """(emoji, etiqueta) del hito MÁS ALTO ya alcanzado, o None si no
    llega ni al primero (10 GB)."""
    for threshold, emoji, label in reversed(_TIERS):
        if total_bytes >= threshold:
            return emoji, label
    return None


def next_milestone(total_bytes: int):
    """(bytes_que_faltan, etiqueta) del siguiente hito no alcanzado
    todavía, o None si ya se superó el más alto de la lista (1 TB)."""
    for threshold, _emoji, label in _TIERS:
        if total_bytes < threshold:
            return threshold - total_bytes, label
    return None
