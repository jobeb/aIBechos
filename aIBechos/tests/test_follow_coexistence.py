"""⚡ Seguir serie (web) y el ⚡ del escritorio: latido compartido, el ⚡
se salta lo ya pedido en la web, y las subidas publican qué capítulo son
para que la web avise a quien sigue la serie."""
import time
from types import SimpleNamespace

from core import auto_series as aus
from core.app_auto_complete import AutoCompleteMixin as Auto


def test_latido_apunta_pasada_y_dueno_sin_pisar_a_otros():
    data = aus.add_auto_owner({}, "tv", 5, "Serie", "Ana", 100)
    data = aus.touch_auto_pass(data, "Ana", {"5": "Serie"}, 200)
    data = aus.touch_auto_pass(data, "Luis", {"5": "Serie", "9": "Otra"}, 300)
    assert data["tv:5"]["owners"] == {"Ana": 100, "Luis": 0}
    assert data["tv:5"]["last_pass"] == {"Ana": 200, "Luis": 300}
    assert data["tv:9"]["owners"] == {"Luis": 0}, "si no constaba se apunta como dueño"
    # Volver a activar no borra el latido de los demás; quitarse quita el suyo.
    data = aus.add_auto_owner(data, "tv", 5, "Serie", "Ana", 400)
    assert data["tv:5"]["last_pass"]["Luis"] == 300
    data = aus.remove_auto_owner(data, "tv", 5, "Luis")
    assert data["tv:5"]["last_pass"] == {"Ana": 200}


def _host(snapshot):
    h = SimpleNamespace(_download_requests_last_data=snapshot,
                        _AUTO_REQUESTS_MAX_AGE=Auto._AUTO_REQUESTS_MAX_AGE)
    return h


def test_episodios_pedidos_en_la_web():
    q = {"a": {"tmdb_id": 5, "media_type": "tv", "season": 1, "episode": 2, "status": "pending"},
         "b": {"tmdb_id": 5, "media_type": "tv", "season": 1, "episode": 3, "status": "done"},
         "c": {"tmdb_id": 5, "media_type": "tv", "season": 2, "episode": 1, "status": "downloading"},
         "d": {"tmdb_id": 5, "media_type": "tv", "season": 2, "episode": 2, "status": "claimed",
               "replace": True},
         "e": {"tmdb_id": 6, "media_type": "tv", "season": 1, "episode": 1, "status": "pending"}}
    got = Auto._auto_requested_episodes(_host((time.time(), q)), 5)
    assert got == {(1, 2), (2, 1)}, "solo en curso, sin sustituciones, de esa serie"
    viejo = (time.time() - Auto._AUTO_REQUESTS_MAX_AGE - 1, q)
    assert Auto._auto_requested_episodes(_host(viejo), 5) == set(), "cola vieja: sin dato"
    assert Auto._auto_requested_episodes(_host(None), 5) == set()


def test_subida_publica_el_capitulo(monkeypatch, tmp_path):
    from core.app_files_core import FilesCoreMixin
    from core.api_client import MediaInfo
    pushed = []
    host = SimpleNamespace(config_data={"app_user_name": "Jose"},
                           _history_lock=__import__("threading").Lock(),
                           _history_path=lambda: tmp_path / "h.json",
                           _load_history=lambda: [],
                           _push_activity_entry_to_ftp=lambda e, k: pushed.append(e),
                           _history_dirty=False)
    host.__dict__.update({k: getattr(FilesCoreMixin, k) for k in ()})
    mi = MediaInfo(tmdb_id=77, media_type="tv", title="S", original_title="S", year="2024",
                   season=2, episode=5)
    monkeypatch.setattr("core.app_files_core.FilesCoreMixin._bump_stats_after_upload",
                        lambda *a, **k: None, raising=False)
    try:
        FilesCoreMixin._save_history_entry(host, "x.mkv", "/s/x.mkv", "ok", 10, media_info=mi)
    except AttributeError:
        pass   # lo que viene después de publicar (estadísticas) no importa aquí
    assert pushed and pushed[0]["tmdb_id"] == 77
    assert (pushed[0]["season"], pushed[0]["episode"], pushed[0]["media_type"]) == (2, 5, "tv")
    pushed.clear()
    peli = MediaInfo(tmdb_id=9, media_type="movie", title="P", original_title="P", year="2020")
    try:
        FilesCoreMixin._save_history_entry(host, "p.mkv", "/p.mkv", "ok", 10, media_info=peli)
    except AttributeError:
        pass
    assert pushed and "tmdb_id" not in pushed[0], "las pelis no llevan capítulo"
