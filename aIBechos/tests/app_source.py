"""Código fuente de "la App" para los tests estáticos: las mixins de core/
con su lógica sin interfaz y la interfaz Qt (gui_qt/) que las aloja."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

APP_FILES = [
    _ROOT / "core" / "app_files_core.py",
    _ROOT / "core" / "app_shared_sync.py",
    _ROOT / "core" / "missing_ep_scan.py",
    _ROOT / "core" / "file_entry.py",
    _ROOT / "core" / "app_auto_complete.py",
    _ROOT / "core" / "app_download_requests.py",
    _ROOT / "core" / "app_downloads_core.py",
    _ROOT / "core" / "app_movies_core.py",
    _ROOT / "core" / "app_history_core.py",
    _ROOT / "core" / "app_cleanup_core.py",
    _ROOT / "core" / "app_watch_sync_core.py",
    _ROOT / "core" / "app_settings_core.py",
] + sorted((_ROOT / "gui_qt").rglob("*.py"))


class _CombinedSource:
    """Imita Path.read_text() devolviendo todos los archivos unidos (sin los
    `from __future__`, que solo pueden ir al principio de un módulo)."""

    def read_text(self, encoding="utf-8"):
        parts = []
        for p in APP_FILES:
            text = p.read_text(encoding=encoding)
            parts.append("\n".join(l for l in text.split("\n")
                                   if not l.startswith("from __future__ import")))
        return "\n\n".join(parts)


APP_SOURCE = _CombinedSource()
