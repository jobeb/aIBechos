import json

import config as config_module
from config import DEFAULTS, Config
from core.appdirs import APP_NAME, LEGACY_APP_NAME


def test_tmdb_api_key_default_is_empty():
    """No debe haber una API key compartida hardcodeada en el repo."""
    assert DEFAULTS["tmdb_api_key"] == ""


def test_reservation_quota_gb_default_is_100():
    """Cuota de reservas configurable (ver core/reservations.py) -- 100GB
    por defecto, igual que la constante QUOTA_BYTES que sustituye."""
    assert DEFAULTS["reservation_quota_gb"] == 100


def test_missing_ep_switches_and_auto_watcher_off_by_default():
    """Interruptores de "Episodios que faltan" y el botón "⚡ Auto"
    persistentes entre reinicios (ver gui/app.py) -- todos apagados por
    defecto, igual que antes de tener esto."""
    assert DEFAULTS["missing_ep_show_ignored"] is False
    assert DEFAULTS["missing_ep_hide_ai_dismissed"] is False
    assert DEFAULTS["missing_ep_hide_no_dub"] is False
    assert DEFAULTS["auto_watcher_running"] is False


def test_missing_ep_switches_and_auto_watcher_persist_across_reload(tmp_path, monkeypatch):
    _isolated_config(tmp_path, monkeypatch)
    cfg1 = Config()
    cfg1.set("missing_ep_show_ignored", True)
    cfg1.set("missing_ep_hide_ai_dismissed", True)
    cfg1.set("missing_ep_hide_no_dub", True)
    cfg1.set("auto_watcher_running", True)
    cfg1.save()

    cfg2 = Config()   # simula reabrir la app
    assert cfg2.get("missing_ep_show_ignored") is True
    assert cfg2.get("missing_ep_hide_ai_dismissed") is True
    assert cfg2.get("missing_ep_hide_no_dub") is True
    assert cfg2.get("auto_watcher_running") is True


class _FakeKeyring:
    """Backend de keyring en memoria: evita tocar el almacén de credenciales
    real del sistema operativo durante los tests."""

    def __init__(self):
        self._store = {}

    def get_password(self, service, key):
        return self._store.get((service, key))

    def set_password(self, service, key, value):
        self._store[(service, key)] = value

    def delete_password(self, service, key):
        self._store.pop((service, key), None)


def _isolated_config(tmp_path, monkeypatch):
    """Aisla Config() del config.json y del keyring reales del sistema."""
    fake_kr = _FakeKeyring()
    monkeypatch.setattr(config_module, "keyring", fake_kr)
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "config_path", lambda: cfg_file)
    return cfg_file, fake_kr


def test_ftp_password_never_written_to_disk_in_plaintext(tmp_path, monkeypatch):
    cfg_file, _ = _isolated_config(tmp_path, monkeypatch)
    cfg = Config()
    cfg.set("ftp_password", "secreto123")
    cfg.save()

    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["ftp_password"] == ""
    # Pero sigue disponible en memoria durante la sesión actual.
    assert cfg.get("ftp_password") == "secreto123"


def test_to_dict_never_includes_plaintext_password(tmp_path, monkeypatch):
    _isolated_config(tmp_path, monkeypatch)
    cfg = Config()
    cfg.set("ftp_password", "secreto123")
    cfg.set("ftp_host", "ftp.example.com")

    exported = cfg.to_dict()

    assert exported["ftp_password"] == ""
    assert exported["ftp_host"] == "ftp.example.com"
    # No debe ser el mismo dict interno -- mutar el export no debe tocar la config viva.
    exported["ftp_host"] = "otro.example.com"
    assert cfg.get("ftp_host") == "ftp.example.com"


def test_ftp_password_persists_across_reload_via_keyring(tmp_path, monkeypatch):
    _isolated_config(tmp_path, monkeypatch)
    cfg1 = Config()
    cfg1.set("ftp_password", "hunter2")
    cfg1.save()

    cfg2 = Config()  # simula reabrir la app
    assert cfg2.get("ftp_password") == "hunter2"


def test_migrates_legacy_plaintext_password_from_old_config_json(tmp_path, monkeypatch):
    cfg_file, _ = _isolated_config(tmp_path, monkeypatch)
    cfg_file.write_text(json.dumps({"ftp_password": "vieja_clave_plana"}), encoding="utf-8")

    cfg = Config()

    assert cfg.get("ftp_password") == "vieja_clave_plana"
    on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert on_disk["ftp_password"] == ""


def test_set_many_routes_ftp_password_through_keyring(tmp_path, monkeypatch):
    cfg_file, fake_kr = _isolated_config(tmp_path, monkeypatch)
    cfg = Config()
    cfg.set_many({"ftp_host": "ftp.example.com", "ftp_password": "otra_clave"})

    assert fake_kr.get_password(APP_NAME, "ftp_password") == "otra_clave"
    assert cfg.get("ftp_host") == "ftp.example.com"


def test_ai_api_key_never_written_to_disk_in_plaintext(tmp_path, monkeypatch):
    cfg_file, _ = _isolated_config(tmp_path, monkeypatch)
    cfg = Config()
    cfg.set("ai_api_key", "gsk_secreto")
    cfg.save()

    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["ai_api_key"] == ""
    assert cfg.get("ai_api_key") == "gsk_secreto"


def test_ai_api_key_persists_across_reload_via_keyring(tmp_path, monkeypatch):
    _isolated_config(tmp_path, monkeypatch)
    cfg1 = Config()
    cfg1.set("ai_api_key", "gsk_hunter2")
    cfg1.save()

    cfg2 = Config()  # simula reabrir la app
    assert cfg2.get("ai_api_key") == "gsk_hunter2"


def test_plex_token_and_jellyfin_key_never_written_to_disk_in_plaintext(tmp_path, monkeypatch):
    cfg_file, _ = _isolated_config(tmp_path, monkeypatch)
    cfg = Config()
    cfg.set("plex_token", "plex_secreto")
    cfg.set("jellyfin_api_key", "jelly_secreto")
    cfg.save()

    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["plex_token"] == ""
    assert saved["jellyfin_api_key"] == ""
    assert cfg.get("plex_token") == "plex_secreto"
    assert cfg.get("jellyfin_api_key") == "jelly_secreto"


def test_plex_and_jellyfin_credentials_persist_across_reload(tmp_path, monkeypatch):
    _isolated_config(tmp_path, monkeypatch)
    cfg1 = Config()
    cfg1.set("plex_token", "tok1")
    cfg1.set("jellyfin_api_key", "key1")
    cfg1.save()

    cfg2 = Config()
    assert cfg2.get("plex_token") == "tok1"
    assert cfg2.get("jellyfin_api_key") == "key1"


def test_media_server_refresh_disabled_by_default():
    assert DEFAULTS["plex_enabled"] is False
    assert DEFAULTS["jellyfin_enabled"] is False


def test_watch_sync_user_mappings_default_is_empty():
    assert DEFAULTS["watch_sync_user_mappings"] == []
    assert DEFAULTS["watch_sync_last_run_ts"] == 0


def test_watch_sync_schedule_disabled_by_default():
    assert DEFAULTS["watch_sync_schedule_enabled"] is False
    assert DEFAULTS["watch_sync_schedule_time"] == ""


def test_watch_sync_schedule_persists_across_reload(tmp_path, monkeypatch):
    _isolated_config(tmp_path, monkeypatch)
    cfg1 = Config()
    cfg1.set("watch_sync_schedule_enabled", True)
    cfg1.set("watch_sync_schedule_time", "03:30")
    cfg1.save()

    cfg2 = Config()
    assert cfg2.get("watch_sync_schedule_enabled") is True
    assert cfg2.get("watch_sync_schedule_time") == "03:30"


def test_watch_sync_user_mappings_persist_across_reload(tmp_path, monkeypatch):
    _isolated_config(tmp_path, monkeypatch)
    mapping = [{"plex_user_id": "1", "plex_user_name": "Jose",
                "jellyfin_user_id": "abc", "jellyfin_user_name": "Jobeb"}]
    cfg1 = Config()
    cfg1.set("watch_sync_user_mappings", mapping)
    cfg1.set("watch_sync_last_run_ts", 12345)
    cfg1.save()

    cfg2 = Config()
    assert cfg2.get("watch_sync_user_mappings") == mapping
    assert cfg2.get("watch_sync_last_run_ts") == 12345
    assert DEFAULTS["plex_token"] == ""
    assert DEFAULTS["jellyfin_api_key"] == ""


def test_ai_fallback_disabled_by_default():
    assert DEFAULTS["ai_fallback_enabled"] is False
    assert DEFAULTS["ai_api_key"] == ""


def test_migrates_legacy_ftp_templates_into_wildcard_categories(tmp_path, monkeypatch):
    cfg_file, _ = _isolated_config(tmp_path, monkeypatch)
    cfg_file.write_text(json.dumps({
        "ftp_path_template": "/datos2/series/{serie}/Temporada {temporada:02d}/",
        "ftp_movie_path_template": "/datos2/peliculas/{serie} ({año})/",
    }), encoding="utf-8")

    cfg = Config()
    cats = cfg.get("ftp_categories")

    assert len(cats["tv"]) == 1
    assert cats["tv"][0]["name"] == "Series"
    assert cats["tv"][0]["genre_ids"] == []
    assert cats["tv"][0]["root"] == "/datos2/series"
    assert cats["tv"][0]["template"] == "{serie}/Temporada {temporada:02d}/"

    assert len(cats["movie"]) == 1
    assert cats["movie"][0]["root"] == "/datos2/peliculas"


def test_does_not_remigrate_when_ftp_categories_already_present(tmp_path, monkeypatch):
    cfg_file, _ = _isolated_config(tmp_path, monkeypatch)
    cfg_file.write_text(json.dumps({
        "ftp_path_template": "/datos2/series/{serie}/",
        "ftp_categories": {"tv": [], "movie": []},   # limpiado a proposito por el usuario
    }), encoding="utf-8")

    cfg = Config()

    assert cfg.get("ftp_categories") == {"tv": [], "movie": []}


def test_fresh_install_migrates_from_defaults(tmp_path, monkeypatch):
    _isolated_config(tmp_path, monkeypatch)   # sin config.json previo

    cfg = Config()
    cats = cfg.get("ftp_categories")

    assert len(cats["tv"]) == 1
    assert cats["tv"][0]["root"] == "/datos2/series"


def test_blank_movie_template_migrates_to_wildcard_without_root(tmp_path, monkeypatch):
    cfg_file, _ = _isolated_config(tmp_path, monkeypatch)
    cfg_file.write_text(json.dumps({"ftp_movie_path_template": ""}), encoding="utf-8")

    cfg = Config()
    cats = cfg.get("ftp_categories")

    assert cats["movie"] == []


def test_migrate_does_not_create_libro_key(tmp_path, monkeypatch):
    # A diferencia de tv/movie, "libro" no tiene ninguna plantilla legacy
    # de la que migrar -- se queda ausente hasta que el usuario cree una
    # categoría desde Ajustes → Servidor → Categorías, igual que "anime".
    _isolated_config(tmp_path, monkeypatch)
    cfg = Config()
    assert "libro" not in cfg.get("ftp_categories")


def test_libro_and_comic_template_defaults():
    assert DEFAULTS["libro_template"] == "{serie}{ext}"
    assert DEFAULTS["comic_template"] == "{serie} ({año}) #{episodio:02d}{ext}"


def test_comicvine_api_key_default_is_empty_and_not_in_keyring_keys():
    """comicvine_api_key es credencial compartida por el grupo (como
    tmdb_api_key), no de keyring por máquina -- ver core/server_config.py."""
    assert DEFAULTS["comicvine_api_key"] == ""
    assert "comicvine_api_key" not in config_module._KEYRING_KEYS


def test_google_books_api_key_default_is_empty_and_not_in_keyring_keys():
    """google_books_api_key es opcional (Google Books funciona sin key) pero
    mismo tratamiento que comicvine_api_key si se configura -- credencial de
    grupo compartida vía servidor, no de keyring por máquina."""
    assert DEFAULTS["google_books_api_key"] == ""
    assert "google_books_api_key" not in config_module._KEYRING_KEYS


def test_migrates_legacy_custom_episode_links_into_episode_level(tmp_path, monkeypatch):
    cfg_file, _ = _isolated_config(tmp_path, monkeypatch)
    old_links = [{"name": "Mi enlace", "url_template": "https://ejemplo.com/{serie}"}]
    cfg_file.write_text(json.dumps({"custom_episode_links": old_links}), encoding="utf-8")

    cfg = Config()

    assert cfg.get("custom_links_episode") == old_links
    assert "custom_episode_links" not in cfg._data
    # Serie y temporada no existían antes -- arrancan con los nuevos valores por defecto
    assert cfg.get("custom_links_show")
    assert cfg.get("custom_links_season")


def test_fresh_install_has_separate_defaults_per_level(tmp_path, monkeypatch):
    _isolated_config(tmp_path, monkeypatch)   # sin config.json previo

    cfg = Config()

    assert cfg.get("custom_links_show")[0]["url_template"] == "https://www.themoviedb.org/tv/{tmdb_id}"
    assert "{temporada}" in cfg.get("custom_links_season")[0]["url_template"]
    assert "{episodio}" in cfg.get("custom_links_episode")[0]["url_template"]


# ---------------------------------------------- cambio de nombre de la app --
# Al pasar de "aRenombrar" a "aIBechos", el llavero del sistema sigue teniendo
# las credenciales guardadas bajo el nombre ANTERIOR. Sin traerlas, el usuario
# se queda sin contraseña del servidor nada más actualizar.

def test_trae_las_credenciales_guardadas_con_el_nombre_anterior(tmp_path, monkeypatch):
    cfg_file, fake_kr = _isolated_config(tmp_path, monkeypatch)
    fake_kr.set_password(LEGACY_APP_NAME, "ftp_password", "la_de_siempre")
    fake_kr.set_password(LEGACY_APP_NAME, "plex_token", "token_plex")

    cfg = Config()

    assert cfg.get("ftp_password") == "la_de_siempre"
    assert fake_kr.get_password(APP_NAME, "ftp_password") == "la_de_siempre"
    assert fake_kr.get_password(APP_NAME, "plex_token") == "token_plex"
    # Y no se deja el rastro en el nombre antiguo.
    assert fake_kr.get_password(LEGACY_APP_NAME, "ftp_password") is None


def test_las_credenciales_solo_se_traen_una_vez(tmp_path, monkeypatch):
    """La marca es imprescindible en macOS: el Llavero pide permiso al leer lo
    guardado por la aplicación anterior, y sin marca volvería a preguntar en
    CADA arranque a quien dijera que no."""
    cfg_file, fake_kr = _isolated_config(tmp_path, monkeypatch)
    fake_kr.set_password(LEGACY_APP_NAME, "ftp_password", "la_de_siempre")
    Config()

    consultas = []
    original = fake_kr.get_password
    fake_kr.get_password = lambda s, k: (consultas.append(s), original(s, k))[1]

    Config()        # simula reabrir la app

    assert LEGACY_APP_NAME not in consultas


def test_la_marca_de_migracion_no_viaja_al_exportar(tmp_path, monkeypatch):
    """Llevarla a otro equipo le haría saltarse una migración que allí no se ha
    hecho nunca, y ese equipo se quedaría sin contraseña sin motivo aparente."""
    from config import INTERNAL_FLAGS
    cfg_file, fake_kr = _isolated_config(tmp_path, monkeypatch)
    fake_kr.set_password(LEGACY_APP_NAME, "ftp_password", "la_de_siempre")
    cfg = Config()

    # Se guarda en disco (si no, se repetiría en cada arranque)...
    guardado = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert guardado["_keyring_service_migrated"] is True
    # ...pero está declarada como interna, que es lo que la excluye al exportar.
    assert "_keyring_service_migrated" in INTERNAL_FLAGS


def test_sin_credenciales_antiguas_no_pasa_nada(tmp_path, monkeypatch):
    cfg_file, fake_kr = _isolated_config(tmp_path, monkeypatch)
    cfg = Config()
    assert cfg.get("ftp_password") == ""
    assert fake_kr.get_password(APP_NAME, "ftp_password") is None
