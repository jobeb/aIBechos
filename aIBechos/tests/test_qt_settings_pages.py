"""Configuración en Qt (gui_qt/settings/): las páginas recogen exactamente las
mismas claves que guardaba la versión Tk (App._collect_settings), y recién abiertas no cuentan
como "cambios sin guardar" (si no, salir de Ajustes preguntaría siempre)."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")


class _Cfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _Host:
    def __init__(self, cfg):
        self.config_data = _Cfg(cfg)
        self.window = None
        self._genres_cache = {"tv": [{"id": 16, "name": "Animación"}], "movie": [{"id": 28, "name": "Acción"}]}

    def _export_config(self):
        pass

    def _import_config(self):
        pass


# Las claves que guardaba "Guardar configuración" en la versión Tk
# (App._collect_settings); la de Qt debe guardar exactamente las mismas.
SETTINGS_KEYS = {
    "ai_api_key", "ai_fallback_enabled", "amule_host", "amule_password", "amule_port",
    "anime_template", "app_user_name", "auto_action", "auto_extract_archives", "comic_template",
    "comicvine_api_key", "custom_links_episode", "custom_links_movie", "custom_links_season",
    "custom_links_show", "desktop_notifications", "download_requests_enabled",
    "download_requests_max_active", "ftp_categories", "ftp_host", "ftp_parallel", "ftp_password",
    "ftp_port", "ftp_protocol", "ftp_retries", "ftp_speed_limit", "ftp_upload_streams", "ftp_use_tls",
    "ftp_user", "google_books_api_key", "jellyfin_api_key", "jellyfin_enabled", "jellyfin_host",
    "jellyfin_username", "language", "libro_template", "manual_action", "min_confidence",
    "movie_template", "p2p_blocked_adult", "p2p_blocked_exts", "p2p_blocked_groups",
    "p2p_blocked_sample", "p2p_blocked_scr", "p2p_lang_ca", "p2p_lang_de", "p2p_lang_fr",
    "p2p_lang_it", "p2p_lang_pt", "p2p_lang_vos", "p2p_score_weights", "p2p_trusted_groups",
    "plex_enabled", "plex_host", "plex_token", "poll_interval", "rename_local", "rename_remote",
    "reservation_quota_gb", "shared_data_ftp_path", "solicitudes_web_url", "start_with_windows",
    "streaming_availability_key", "tmdb_api_key", "tv_template", "unstuck_backoff_base_minutes",
    "unstuck_backoff_max_minutes", "unstuck_enabled", "unstuck_file_ttl_minutes",
    "unstuck_max_retries", "watch_folder",
}


def _pages(host):
    from gui_qt.settings.tab import CLIENT_PAGES, SERVER_PAGES
    return [cls(host) for _t, _k, cls in CLIENT_PAGES + SERVER_PAGES]


SAMPLE = {
    "watch_folder": "C:/vigilada", "poll_interval": 10, "ftp_port": 21, "ftp_protocol": "sftp",
    "ftp_use_tls": False, "ftp_parallel": 2, "ftp_upload_streams": 4, "ftp_speed_limit": 0,
    "tv_template": "{serie} {temporada}x{episodio:02d}{ext}",
    "custom_links_movie": [{"name": "TMDB", "url_template": "https://x/{tmdb_id}"}],
    "ftp_categories": {"tv": [{"id": "a", "name": "Series", "genre_ids": [16], "root": "/s",
                               "template": "{serie}/"}],
                       "movie": [], "libro": [{"id": "b", "name": "Libros", "genre_ids": ["ebook"],
                                               "root": "/l", "template": "{serie}/"}]},
    "p2p_trusted_groups": ["grupots"], "p2p_score_weights": {},
}


def test_mismas_claves_que_tk(qtbot):
    pages = _pages(_Host(SAMPLE))
    for p in pages:
        qtbot.addWidget(p)
    keys = set()
    for p in pages:
        keys |= set(p.collect())
    assert keys == SETTINGS_KEYS


def test_recien_abierto_no_hay_cambios(qtbot):
    from gui_qt.settings.tab import _normalized
    from config import DEFAULTS
    cfg = {**DEFAULTS, **SAMPLE}
    host = _Host(cfg)
    for p in _pages(host):
        qtbot.addWidget(p)
        changed = {k: v for k, v in p.collect().items()
                   if _normalized(k, host.config_data.get(k)) != _normalized(k, v)}
        assert not changed, f"{type(p).__name__}: {changed}"


def test_protocolo_y_limites(qtbot):
    from gui_qt.settings.client_pages import FtpPage, GeneralPage
    host = _Host({**SAMPLE, "poll_interval": 1, "unstuck_max_retries": 99})
    ftp = FtpPage(host)
    gen = GeneralPage(host)
    qtbot.addWidget(ftp)
    qtbot.addWidget(gen)
    data = {**ftp.collect(), **gen.collect()}
    assert (data["ftp_protocol"], data["ftp_use_tls"]) == ("sftp", False)
    assert data["poll_interval"] == 5 and data["unstuck_max_retries"] == 20
    # Cambiar a FTP propone su puerto estándar (estaba el de por defecto).
    from core.transfer import PROTOCOL_LABELS
    ftp.protocol.setCurrentText(PROTOCOL_LABELS["sftp"])
    ftp.protocol.setCurrentText(PROTOCOL_LABELS["ftp"])
    assert ftp.collect()["ftp_port"] == 21
    ftp.protocol.setCurrentText(PROTOCOL_LABELS["sftp"])
    assert ftp.collect()["ftp_port"] == 22
