"""
Escaneo de "Episodios que faltan" para la interfaz Qt: la misma lógica que la
versión Tk (core/missing_ep_scan.py::MissingEpScanMixin, compartida tal cual),
con los cuatro "enchufes" que esa mixin pide al que la hereda.
"""

from __future__ import annotations

from core.missing_ep_scan import MissingEpScanMixin
from gui_qt.bridge import ui


class MissingEpScanService(MissingEpScanMixin):
    def __init__(self, ctx, get_dub_cache):
        """*get_dub_cache*: devuelve el dict vivo de spanish_dub_cache de la
        pestaña (se actualiza con el audio real del servidor al reescanear)."""
        self.ctx = ctx
        self.config_data = ctx.config
        self.tmdb = ctx.tmdb
        self._ftp_dir_cache = ctx._ftp_dir_cache   # misma caché de sesión que el resto de la interfaz
        self._get_dub_cache = get_dub_cache

    def _new_ftp_client(self):
        return self.ctx.new_ftp_client()

    def _push_missing_episodes_to_ftp(self):
        self.ctx.push_missing_episodes()

    def _scan_notify(self, text: str, color: str):
        self.ctx.set_status(text, color)

    def _refresh_server_audio(self, source: str, server_id, tmdb_id) -> None:
        """Pistas de audio reales de la serie en Jellyfin/Plex -> veredicto
        True/False por episodio PRESENTE en spanish_dub_cache (castellano
        exigido, latino no vale). Solo con "Ocultar sin doblaje ES" activo
        (si no, es una llamada de más por serie). Nunca lanza. Misma lógica
        que App._refresh_server_audio de la versión Tk."""
        if not self.config_data.get("missing_ep_hide_no_dub", False):
            return
        if not server_id or not tmdb_id:
            return
        try:
            if source == "jellyfin":
                from core.media_server_refresh import get_jellyfin_episodes_audio
                audio = get_jellyfin_episodes_audio(
                    self.config_data.get("jellyfin_host", ""),
                    self.config_data.get("jellyfin_api_key", ""), server_id)
            else:
                from core.media_server_refresh import get_plex_episodes_audio
                audio = get_plex_episodes_audio(
                    self.config_data.get("plex_host", ""),
                    self.config_data.get("plex_token", ""), server_id)
            if not audio:
                return
            from core.server_audio import audio_map_to_episodes
            episodes = audio_map_to_episodes(audio)
        except Exception:
            return
        if not episodes:
            return

        def _apply():
            try:
                cache = self._get_dub_cache()
                entry = cache.setdefault(str(tmdb_id), {"spanish_available": None, "episodes": {}})
                entry.setdefault("episodes", {}).update(episodes)
                from core.spanish_dub_cache import save_cache
                save_cache(cache)
            except Exception:
                pass
        ui(_apply)
