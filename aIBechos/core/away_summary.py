"""Lo que pasó en Archivos mientras la ventana estaba en la bandeja o
minimizada (sobre todo el modo automático): al volver a ella se resume en
una línea ("Mientras no mirabas: 12 subidos, 1 con error (Serie 1x05)")
en vez de tener que repasar la lista entera. Puro, sin Qt."""

import threading

_LABELS = (("uploaded", "subido", "subidos"),
           ("skip", "omitido", "omitidos"),
           ("error", "con error", "con error"))


class AwaySummary:
    def __init__(self):
        self._lock = threading.Lock()
        self._counts = {}
        self._errors = []

    def record(self, kind: str, name: str = "") -> None:
        if kind not in ("uploaded", "skip", "error"):
            return
        with self._lock:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            if kind == "error" and name and len(self._errors) < 3:
                self._errors.append(name)

    def take(self) -> tuple:
        """(texto, hubo_errores) y vacía el resumen; ("", False) si no
        pasó nada."""
        with self._lock:
            counts, errors = self._counts, self._errors
            self._counts, self._errors = {}, []
        parts = []
        for kind, one, many in _LABELS:
            n = counts.get(kind, 0)
            if n:
                parts.append(f"{n} {one if n == 1 else many}")
        if not parts:
            return "", False
        text = "Mientras no mirabas: " + ", ".join(parts)
        if errors:
            text += " (" + ", ".join(errors) + (", …" if counts.get("error", 0) > len(errors) else "") + ")"
        return text, bool(counts.get("error"))
