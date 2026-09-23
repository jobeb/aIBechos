import core.media_server_refresh as msr


class _FakeResponse:
    def __init__(self, json_data=None, status=200):
        self._json = json_data or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


# ── Plex ──────────────────────────────────────────────────────────────────

def test_refresh_plex_without_config_returns_false():
    ok, msg = msr.refresh_plex("", "")
    assert ok is False


def test_refresh_plex_refreshes_all_sections(monkeypatch):
    sections_payload = {"MediaContainer": {"Directory": [
        {"key": "1", "title": "Series"}, {"key": "2", "title": "Peliculas"},
    ]}}
    calls = []

    def _fake_get(url, **kw):
        calls.append(url)
        if url.endswith("/library/sections"):
            return _FakeResponse(sections_payload)
        return _FakeResponse({})

    monkeypatch.setattr(msr.requests, "get", _fake_get)
    ok, msg = msr.refresh_plex("http://plex:32400", "tok123")

    assert ok is True
    assert "2/2" in msg
    assert any("/library/sections/1/refresh" in c for c in calls)
    assert any("/library/sections/2/refresh" in c for c in calls)


def test_refresh_plex_handles_no_sections():
    import core.media_server_refresh as msr2

    def _fake_get(url, **kw):
        return _FakeResponse({"MediaContainer": {"Directory": []}})
    msr2.requests.get = _fake_get
    ok, msg = msr2.refresh_plex("http://plex:32400", "tok123")
    assert ok is False


def test_refresh_plex_handles_network_failure(monkeypatch):
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(msr.requests, "get", _raise)
    ok, msg = msr.refresh_plex("http://plex:32400", "tok123")
    assert ok is False


def test_validate_plex_true_on_200(monkeypatch):
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(status=200))
    assert msr.validate_plex("http://plex:32400", "tok123") is True


def test_validate_plex_false_on_401(monkeypatch):
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(status=401))
    assert msr.validate_plex("http://plex:32400", "tok123") is False


def test_validate_plex_false_without_config():
    assert msr.validate_plex("", "") is False


# ── Jellyfin ─────────────────────────────────────────────────────────────

def test_refresh_jellyfin_without_config_returns_false():
    ok, msg = msr.refresh_jellyfin("", "")
    assert ok is False


def test_refresh_jellyfin_success(monkeypatch):
    monkeypatch.setattr(msr.requests, "post", lambda *a, **kw: _FakeResponse(status=204))
    ok, msg = msr.refresh_jellyfin("http://jellyfin:8096", "key123")
    assert ok is True


def test_refresh_jellyfin_handles_network_failure(monkeypatch):
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(msr.requests, "post", _raise)
    ok, msg = msr.refresh_jellyfin("http://jellyfin:8096", "key123")
    assert ok is False


def test_validate_jellyfin_true_on_200(monkeypatch):
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(status=200))
    assert msr.validate_jellyfin("http://jellyfin:8096", "key123") is True


def test_validate_jellyfin_false_without_config():
    assert msr.validate_jellyfin("", "") is False


# ── Almacenamiento de Jellyfin (System/Info/Storage) ────────────────────

_STORAGE_PAYLOAD = {"Libraries": [
    {"Id": "abc", "Name": "Series", "Folders": [
        {"Path": "/home/administrador/datos2/series", "FreeSpace": 5000, "UsedSpace": 100},
    ]},
    {"Id": "def", "Name": "Peliculas", "Folders": [
        {"Path": "/home/administrador/datos/peliculas", "FreeSpace": 100, "UsedSpace": 900},
    ]},
]}


def test_get_jellyfin_storage_folders_without_config_returns_none():
    assert msr.get_jellyfin_storage_folders("", "") is None


def test_get_jellyfin_storage_folders_flattens_all_libraries(monkeypatch):
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(_STORAGE_PAYLOAD))
    folders = msr.get_jellyfin_storage_folders("http://jellyfin:8096", "key123")
    assert len(folders) == 2
    assert {f["Path"] for f in folders} == {
        "/home/administrador/datos2/series", "/home/administrador/datos/peliculas"}


def test_get_jellyfin_storage_folders_returns_none_on_old_server_without_endpoint(monkeypatch):
    """Jellyfin < 10.11 no tiene este endpoint -- debe degradar a None, no romper."""
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(status=404))
    assert msr.get_jellyfin_storage_folders("http://jellyfin:8096", "key123") is None


def test_get_jellyfin_free_space_for_root_matches_correct_library(monkeypatch):
    msr._reset_storage_cache_for_tests()
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(_STORAGE_PAYLOAD))
    free = msr.get_jellyfin_free_space_for_root("/datos2/series/", "http://jellyfin:8096", "key123")
    assert free == 5000


def test_get_jellyfin_free_space_for_root_none_when_no_match(monkeypatch):
    msr._reset_storage_cache_for_tests()
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(_STORAGE_PAYLOAD))
    free = msr.get_jellyfin_free_space_for_root("/datos2/anime/", "http://jellyfin:8096", "key123")
    assert free is None


def test_get_jellyfin_free_space_for_root_caches_between_calls(monkeypatch):
    msr._reset_storage_cache_for_tests()
    calls = []
    def _fake_get(*a, **kw):
        calls.append(1)
        return _FakeResponse(_STORAGE_PAYLOAD)
    monkeypatch.setattr(msr.requests, "get", _fake_get)

    msr.get_jellyfin_free_space_for_root("/datos2/series/", "http://jellyfin:8096", "key123")
    msr.get_jellyfin_free_space_for_root("/datos/peliculas/", "http://jellyfin:8096", "key123")

    assert len(calls) == 1, "la segunda consulta deberia usar la cache, no volver a llamar a Jellyfin"


# ── refresh_configured_servers (orquestador) ────────────────────────────

class _FakeConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_refresh_configured_servers_calls_only_enabled_ones(monkeypatch):
    calls = []
    monkeypatch.setattr(msr, "refresh_plex", lambda *a, **kw: calls.append("plex") or (True, "ok"))
    monkeypatch.setattr(msr, "refresh_jellyfin", lambda *a, **kw: calls.append("jellyfin") or (True, "ok"))

    cfg = _FakeConfig({"plex_enabled": True, "jellyfin_enabled": False,
                        "plex_host": "h", "plex_token": "t"})
    msr.refresh_configured_servers(cfg)

    assert calls == ["plex"]


def test_refresh_configured_servers_calls_neither_when_both_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(msr, "refresh_plex", lambda *a, **kw: calls.append("plex") or (True, "ok"))
    monkeypatch.setattr(msr, "refresh_jellyfin", lambda *a, **kw: calls.append("jellyfin") or (True, "ok"))

    cfg = _FakeConfig({"plex_enabled": False, "jellyfin_enabled": False})
    msr.refresh_configured_servers(cfg)

    assert calls == []


def test_refresh_configured_servers_never_raises_on_failure(monkeypatch):
    monkeypatch.setattr(msr, "refresh_plex", lambda *a, **kw: (False, "fallo simulado"))
    cfg = _FakeConfig({"plex_enabled": True, "plex_host": "h", "plex_token": "t"})
    msr.refresh_configured_servers(cfg)   # no debe lanzar


# ── trigger_refresh (antirrebote + hilo aparte) ─────────────────────────

def test_trigger_refresh_runs_immediately_the_first_time(monkeypatch):
    msr._reset_debounce_for_tests()
    calls = []
    monkeypatch.setattr(msr, "refresh_configured_servers", lambda cfg: calls.append(1))
    cfg = _FakeConfig({})

    msr.trigger_refresh(cfg, min_interval_seconds=60)
    import time as _time
    _time.sleep(0.05)   # dar tiempo al hilo de fondo

    assert calls == [1]


def test_trigger_refresh_debounces_rapid_calls(monkeypatch):
    msr._reset_debounce_for_tests()
    calls = []
    monkeypatch.setattr(msr, "refresh_configured_servers", lambda cfg: calls.append(1))
    cfg = _FakeConfig({})

    msr.trigger_refresh(cfg, min_interval_seconds=60)
    msr.trigger_refresh(cfg, min_interval_seconds=60)
    msr.trigger_refresh(cfg, min_interval_seconds=60)
    import time as _time
    _time.sleep(0.05)

    assert calls == [1], "las llamadas seguidas deberian colapsar en una sola"


# ── Series/episodios de Jellyfin y Plex (detector de huecos) ────────────

def test_get_jellyfin_series_without_config_returns_none():
    assert msr.get_jellyfin_series("", "") is None


def test_get_jellyfin_series_parses_tmdb_id(monkeypatch):
    payload = {"Items": [
        {"Id": "abc123", "Name": "Serie Con Tmdb", "ProviderIds": {"Tmdb": "9999", "Imdb": "tt123"}},
        {"Id": "def456", "Name": "Serie Sin Tmdb", "ProviderIds": {}},
    ]}
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    shows = msr.get_jellyfin_series("http://jellyfin:8096", "key123")
    assert shows == [
        {"id": "abc123", "name": "Serie Con Tmdb", "tmdb_id": 9999, "folder_name": None},
        {"id": "def456", "name": "Serie Sin Tmdb", "tmdb_id": None, "folder_name": None},
    ]


def test_get_jellyfin_series_extracts_real_folder_name_from_path(monkeypatch):
    """El nombre de carpeta real puede no parecerse al nombre mostrado
    (traducido) -- ver "Acusado" (Jellyfin, es-ES) vs carpeta real
    "Accused" en el servidor."""
    payload = {"Items": [
        {"Id": "abc123", "Name": "Acusado", "ProviderIds": {"Tmdb": "202097"},
         "Path": "/home/administrador/datos2/series/Accused"},
    ]}
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    shows = msr.get_jellyfin_series("http://jellyfin:8096", "key123")
    assert shows[0]["folder_name"] == "Accused"


def test_get_jellyfin_episodes_without_config_returns_none():
    assert msr.get_jellyfin_episodes("", "", "id") is None


def test_get_jellyfin_episodes_parses_season_and_episode(monkeypatch):
    payload = {"Items": [
        {"ParentIndexNumber": 1, "IndexNumber": 1},
        {"ParentIndexNumber": 1, "IndexNumber": 2},
        {"ParentIndexNumber": 2, "IndexNumber": 1},
        {"ParentIndexNumber": None, "IndexNumber": None},   # especial sin numero -- se ignora
    ]}
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    present = msr.get_jellyfin_episodes("http://jellyfin:8096", "key123", "series1")
    assert present == {(1, 1), (1, 2), (2, 1)}


def test_get_plex_series_without_config_returns_none():
    assert msr.get_plex_series("", "") is None


def test_get_plex_series_parses_tmdb_guid(monkeypatch):
    sections_payload = {"MediaContainer": {"Directory": [
        {"key": "1", "type": "show", "title": "Series"},
        {"key": "2", "type": "movie", "title": "Peliculas"},   # se ignora, no es "show"
    ]}}
    shows_payload = {"MediaContainer": {"Metadata": [
        {"ratingKey": "100", "title": "Serie X",
         "Guid": [{"id": "imdb://tt1"}, {"id": "tmdb://5555"}, {"id": "tvdb://9"}]},
        {"ratingKey": "101", "title": "Serie Sin Tmdb", "Guid": [{"id": "imdb://tt2"}]},
    ]}}

    def _fake_get(url, **kw):
        if url.endswith("/library/sections"):
            return _FakeResponse(sections_payload)
        return _FakeResponse(shows_payload)

    monkeypatch.setattr(msr.requests, "get", _fake_get)
    shows = msr.get_plex_series("http://plex:32400", "tok123")
    assert shows == [
        {"rating_key": "100", "name": "Serie X", "tmdb_id": 5555},
        {"rating_key": "101", "name": "Serie Sin Tmdb", "tmdb_id": None},
    ]


def test_get_plex_episodes_without_config_returns_none():
    assert msr.get_plex_episodes("", "", "100") is None


def test_get_plex_episodes_parses_season_and_episode(monkeypatch):
    payload = {"MediaContainer": {"Metadata": [
        {"parentIndex": 1, "index": 1},
        {"parentIndex": 1, "index": 2},
        {"parentIndex": 2, "index": 1},
    ]}}
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    present = msr.get_plex_episodes("http://plex:32400", "tok123", "100")
    assert present == {(1, 1), (1, 2), (2, 1)}


# ── Jellyfin: datos de uso (liberar espacio) ────────────────────────────────

def test_get_jellyfin_usage_stats_without_config_returns_none():
    assert msr.get_jellyfin_usage_stats("", "") is None


def test_get_jellyfin_usage_stats_none_when_no_users(monkeypatch):
    monkeypatch.setattr(msr.requests, "get", lambda url, **kw: _FakeResponse([]))
    assert msr.get_jellyfin_usage_stats("http://jf:8096", "key") is None


def test_get_jellyfin_usage_stats_uses_specified_username(monkeypatch):
    # Servidor con varios usuarios (típico de un servidor familiar) -- por
    # defecto se usaría "administrador" (el primero), que normalmente no
    # ve nada; hay que poder pedir el usuario real explícitamente.
    users_payload = [
        {"Id": "admin_id", "Name": "administrador"},
        {"Id": "jose_id", "Name": "Jose"},
    ]
    items_payload = {"Items": [
        {"Id": "movie1", "Name": "Pelicula", "Type": "Movie", "ProviderIds": {},
         "DateCreated": "2023-01-01T00:00:00Z", "UserData": {"Played": True, "PlayCount": 3}},
    ]}
    requested_user_ids = []

    def _fake_get(url, **kw):
        if url.endswith("/Users"):
            return _FakeResponse(users_payload)
        requested_user_ids.append(url)
        return _FakeResponse(items_payload)

    monkeypatch.setattr(msr.requests, "get", _fake_get)
    stats = msr.get_jellyfin_usage_stats("http://jf:8096", "key", username="jose")
    assert any("/Users/jose_id/Items" in u for u in requested_user_ids)
    assert stats["movie1"]["fully_watched"] is True


def test_get_jellyfin_usage_stats_aggregates_watched_status_across_all_users(monkeypatch):
    # Servidor familiar con 2 usuarios: "Ana" vio la pelicula, "Bruno" no.
    # Sin especificar username, debe consultar a AMBOS y combinar: vista
    # si CUALQUIERA la vio, reproducciones sumadas, ultimo visionado el
    # mas reciente de los dos.
    users_payload = [
        {"Id": "ana_id", "Name": "Ana"},
        {"Id": "bruno_id", "Name": "Bruno"},
    ]
    metadata_payload = {"Items": [
        {"Id": "movie1", "Name": "Pelicula", "Type": "Movie", "ProviderIds": {},
         "DateCreated": "2023-01-01T00:00:00Z", "MediaSources": [{"Size": 1000}]},
    ]}
    watch_payloads = {
        "ana_id": {"Items": [
            {"Id": "movie1", "UserData": {"Played": True, "PlayCount": 2,
                                          "LastPlayedDate": "2024-06-01T00:00:00Z"}},
        ]},
        "bruno_id": {"Items": [
            {"Id": "movie1", "UserData": {"Played": False, "PlayCount": 0}},
        ]},
    }

    def _fake_get(url, **kw):
        if url.endswith("/Users"):
            return _FakeResponse(users_payload)
        if kw.get("params", {}).get("IncludeItemTypes") == "Series,Movie" and "Fields" in kw.get("params", {}) \
                and kw["params"]["Fields"] == "UserData":
            for uid, payload in watch_payloads.items():
                if f"/Users/{uid}/Items" in url:
                    return _FakeResponse(payload)
        return _FakeResponse(metadata_payload)

    monkeypatch.setattr(msr.requests, "get", _fake_get)
    stats = msr.get_jellyfin_usage_stats("http://jf:8096", "key")

    assert stats["movie1"]["fully_watched"] is True   # Ana la vio, aunque Bruno no
    assert stats["movie1"]["play_count"] == 2
    assert stats["movie1"]["last_played"] == "2024-06-01T00:00:00Z"


def test_get_jellyfin_usage_stats_falls_back_to_first_user_when_username_not_found(monkeypatch):
    users_payload = [{"Id": "admin_id", "Name": "administrador"}]
    items_payload = {"Items": []}

    def _fake_get(url, **kw):
        if url.endswith("/Users"):
            return _FakeResponse(users_payload)
        return _FakeResponse(items_payload)

    monkeypatch.setattr(msr.requests, "get", _fake_get)
    stats = msr.get_jellyfin_usage_stats("http://jf:8096", "key", username="no_existe")
    assert stats == {}   # no lanza, solo cae al primer usuario disponible


def test_get_jellyfin_usage_stats_parses_movies_and_series(monkeypatch):
    users_payload = [{"Id": "user1", "Name": "admin"}]
    items_payload = {"Items": [
        {
            "Id": "movie1", "Name": "Pelicula Vista", "Type": "Movie",
            "ProviderIds": {"Tmdb": "111"}, "DateCreated": "2023-01-01T00:00:00Z",
            "ProductionYear": 2020,
            "UserData": {"Played": True, "PlayCount": 2, "LastPlayedDate": "2024-03-01T00:00:00Z"},
        },
        {
            "Id": "movie2", "Name": "Pelicula Sin Ver", "Type": "Movie",
            "ProviderIds": {"Tmdb": "222"}, "DateCreated": "2023-06-01T00:00:00Z",
            "UserData": {"Played": False, "PlayCount": 0},
        },
        {
            "Id": "series1", "Name": "Serie Completa", "Type": "Series",
            "ProviderIds": {"Tmdb": "333"}, "DateCreated": "2022-01-01T00:00:00Z",
            "UserData": {"UnplayedItemCount": 0, "PlayCount": 0, "LastPlayedDate": "2024-01-01T00:00:00Z"},
        },
        {
            "Id": "series2", "Name": "Serie A Medias", "Type": "Series",
            "ProviderIds": {"Tmdb": "444"}, "DateCreated": "2022-01-01T00:00:00Z",
            "UserData": {"UnplayedItemCount": 5},
        },
    ]}

    def _fake_get(url, **kw):
        if url.endswith("/Users"):
            return _FakeResponse(users_payload)
        return _FakeResponse(items_payload)

    import core.media_server_refresh as msr_mod
    monkeypatch.setattr(msr_mod.requests, "get", _fake_get)

    stats = msr.get_jellyfin_usage_stats("http://jf:8096", "key")
    assert stats["movie1"]["fully_watched"] is True
    assert stats["movie1"]["play_count"] == 2
    assert stats["movie1"]["tmdb_id"] == 111
    assert stats["movie1"]["media_type"] == "movie"
    assert stats["movie1"]["year"] == "2020"
    assert stats["series1"]["year"] == ""   # payload sin ProductionYear
    assert stats["movie2"]["fully_watched"] is False
    assert stats["series1"]["fully_watched"] is True   # UnplayedItemCount == 0
    assert stats["series1"]["media_type"] == "tv"
    assert stats["series2"]["fully_watched"] is False   # le quedan episodios sin ver
    assert stats["movie1"]["date_added"] == "2023-01-01T00:00:00Z"


def test_get_jellyfin_usage_stats_returns_none_on_network_failure(monkeypatch):
    def _fake_get(url, **kw):
        raise RuntimeError("sin red")
    monkeypatch.setattr(msr.requests, "get", _fake_get)
    assert msr.get_jellyfin_usage_stats("http://jf:8096", "key") is None


def test_get_jellyfin_usage_stats_gets_movie_size_from_mediasources(monkeypatch):
    users_payload = [{"Id": "user1", "Name": "admin"}]
    items_payload = {"Items": [
        {"Id": "movie1", "Name": "Pelicula", "Type": "Movie", "ProviderIds": {},
         "DateCreated": "2023-01-01T00:00:00Z", "UserData": {},
         "MediaSources": [{"Size": 4_500_000_000}]},
    ]}

    def _fake_get(url, **kw):
        if url.endswith("/Users"):
            return _FakeResponse(users_payload)
        return _FakeResponse(items_payload)

    monkeypatch.setattr(msr.requests, "get", _fake_get)
    stats = msr.get_jellyfin_usage_stats("http://jf:8096", "key")
    assert stats["movie1"]["size_bytes"] == 4_500_000_000


def test_get_jellyfin_usage_stats_sums_episode_sizes_for_series(monkeypatch):
    users_payload = [{"Id": "user1", "Name": "admin"}]
    items_payload = {"Items": [
        {"Id": "series1", "Name": "Serie", "Type": "Series", "ProviderIds": {},
         "DateCreated": "2022-01-01T00:00:00Z", "UserData": {"UnplayedItemCount": 0}},
    ]}
    episodes_payload = {"Items": [
        {"SeriesId": "series1", "MediaSources": [{"Size": 1_000_000_000}]},
        {"SeriesId": "series1", "MediaSources": [{"Size": 1_200_000_000}]},
        {"SeriesId": "otra_serie", "MediaSources": [{"Size": 999}]},
    ]}

    def _fake_get(url, **kw):
        if url.endswith("/Users"):
            return _FakeResponse(users_payload)
        if kw.get("params", {}).get("IncludeItemTypes") == "Episode":
            return _FakeResponse(episodes_payload)
        return _FakeResponse(items_payload)

    monkeypatch.setattr(msr.requests, "get", _fake_get)
    stats = msr.get_jellyfin_usage_stats("http://jf:8096", "key")
    assert stats["series1"]["size_bytes"] == 2_200_000_000


def test_get_jellyfin_usage_stats_aggregates_series_play_count_from_episodes(monkeypatch):
    # Caso real: Arcadia -- el panel de Jellyfin mostraba visionado
    # reciente de un usuario, pero el UserData de la SERIE traía
    # PlayCount=0/LastPlayedDate vacío (no fiable a ese nivel), dejando la
    # puntuación de tendencia en 0 pese a que sí se había visto. Debe
    # agregarse desde el UserData de cada EPISODIO en su lugar.
    users_payload = [{"Id": "alvaro_id", "Name": "Alvaro"}]
    series_payload = {"Items": [
        {"Id": "series1", "Name": "Arcadia", "Type": "Series", "ProviderIds": {},
         "DateCreated": "2022-01-01T00:00:00Z"},
    ]}
    watch_series_payload = {"Items": [
        {"Id": "series1", "UserData": {"UnplayedItemCount": 0, "PlayCount": 0}},
    ]}
    watch_episodes_payload = {"Items": [
        {"SeriesId": "series1", "UserData": {"PlayCount": 1, "LastPlayedDate": "2024-05-01T00:00:00Z"}},
        {"SeriesId": "series1", "UserData": {"PlayCount": 2, "LastPlayedDate": "2024-06-15T00:00:00Z"}},
        {"SeriesId": "otra_serie", "UserData": {"PlayCount": 5, "LastPlayedDate": "2024-07-01T00:00:00Z"}},
    ]}

    def _fake_get(url, **kw):
        if url.endswith("/Users"):
            return _FakeResponse(users_payload)
        params = kw.get("params", {})
        if params.get("IncludeItemTypes") == "Series,Movie" and params.get("Fields") == "UserData":
            return _FakeResponse(watch_series_payload)
        if params.get("IncludeItemTypes") == "Episode" and params.get("Fields") == "UserData":
            return _FakeResponse(watch_episodes_payload)
        return _FakeResponse(series_payload)

    monkeypatch.setattr(msr.requests, "get", _fake_get)
    stats = msr.get_jellyfin_usage_stats("http://jf:8096", "key")
    assert stats["series1"]["play_count"] == 3   # 1 + 2, no el 0 de UserData de la serie
    assert stats["series1"]["last_played"] == "2024-06-15T00:00:00Z"   # el más reciente de los dos episodios
    assert stats["series1"]["fully_watched"] is True   # sigue viniendo de UnplayedItemCount


# ── Plex: datos de uso (liberar espacio) ────────────────────────────────────

def test_get_plex_usage_stats_without_config_returns_none():
    assert msr.get_plex_usage_stats("", "") is None


def test_get_plex_usage_stats_parses_movies_and_shows(monkeypatch):
    sections_payload = {"MediaContainer": {"Directory": [
        {"key": "1", "type": "show"}, {"key": "2", "type": "movie"},
    ]}}
    shows_payload = {"MediaContainer": {"Metadata": [
        {"ratingKey": "s1", "title": "Serie Completa", "leafCount": 10, "viewedLeafCount": 10,
         "lastViewedAt": 1700000000, "addedAt": 1600000000, "Guid": [{"id": "tmdb://333"}],
         "year": 2015},
        {"ratingKey": "s2", "title": "Serie A Medias", "leafCount": 10, "viewedLeafCount": 4,
         "addedAt": 1600000000},
    ]}}
    movies_payload = {"MediaContainer": {"Metadata": [
        {"ratingKey": "m1", "title": "Pelicula Vista", "viewCount": 3,
         "lastViewedAt": 1700000000, "addedAt": 1600000000, "Guid": [{"id": "tmdb://111"}],
         "year": 2021},
        {"ratingKey": "m2", "title": "Pelicula Sin Ver", "viewCount": 0, "addedAt": 1600000000},
    ]}}

    def _fake_get(url, **kw):
        if url.endswith("/library/sections"):
            return _FakeResponse(sections_payload)
        if kw.get("params", {}).get("type") == "2":
            return _FakeResponse(shows_payload)
        return _FakeResponse(movies_payload)

    monkeypatch.setattr(msr.requests, "get", _fake_get)
    stats = msr.get_plex_usage_stats("http://plex:32400", "tok123")

    assert stats["s1"]["fully_watched"] is True
    assert stats["s1"]["media_type"] == "tv"
    assert stats["s1"]["tmdb_id"] == 333
    assert stats["s1"]["year"] == "2015"
    assert stats["s2"]["fully_watched"] is False
    assert stats["m1"]["fully_watched"] is True
    assert stats["m1"]["play_count"] == 3
    assert stats["m1"]["year"] == "2021"
    assert stats["m2"]["year"] == ""
    assert stats["m2"]["fully_watched"] is False


def test_get_plex_usage_stats_returns_none_on_network_failure(monkeypatch):
    def _fake_get(url, **kw):
        raise RuntimeError("sin red")
    monkeypatch.setattr(msr.requests, "get", _fake_get)
    assert msr.get_plex_usage_stats("http://plex:32400", "tok123") is None


def test_get_plex_usage_stats_gets_movie_size_directly(monkeypatch):
    sections_payload = {"MediaContainer": {"Directory": [{"key": "2", "type": "movie"}]}}
    movies_payload = {"MediaContainer": {"Metadata": [
        {"ratingKey": "m1", "title": "Pelicula", "viewCount": 1,
         "Media": [{"Part": [{"size": 4_500_000_000}]}]},
    ]}}

    def _fake_get(url, **kw):
        if url.endswith("/library/sections"):
            return _FakeResponse(sections_payload)
        return _FakeResponse(movies_payload)

    monkeypatch.setattr(msr.requests, "get", _fake_get)
    stats = msr.get_plex_usage_stats("http://plex:32400", "tok123")
    assert stats["m1"]["size_bytes"] == 4_500_000_000


def test_get_plex_usage_stats_sums_episode_sizes_per_show_via_section_listing(monkeypatch):
    sections_payload = {"MediaContainer": {"Directory": [{"key": "1", "type": "show"}]}}
    shows_payload = {"MediaContainer": {"Metadata": [
        {"ratingKey": "s1", "title": "Serie", "leafCount": 2, "viewedLeafCount": 2},
    ]}}
    episodes_payload = {"MediaContainer": {"Metadata": [
        {"grandparentRatingKey": "s1", "Media": [{"Part": [{"size": 1_000_000_000}]}]},
        {"grandparentRatingKey": "s1", "Media": [{"Part": [{"size": 1_200_000_000}]}]},
        {"grandparentRatingKey": "otra", "Media": [{"Part": [{"size": 999}]}]},
    ]}}

    def _fake_get(url, **kw):
        if url.endswith("/library/sections"):
            return _FakeResponse(sections_payload)
        if kw.get("params", {}).get("type") == "4":
            return _FakeResponse(episodes_payload)
        return _FakeResponse(shows_payload)

    monkeypatch.setattr(msr.requests, "get", _fake_get)
    stats = msr.get_plex_usage_stats("http://plex:32400", "tok123")
    assert stats["s1"]["size_bytes"] == 2_200_000_000


# ── Sincronizar visionado por usuario (core/watch_sync.py) ─────────────────

class _FakePlexUser:
    def __init__(self, id, title, token=None, token_error=None):
        self.id = id
        self.title = title
        self._token = token
        self._token_error = token_error

    def get_token(self, machine_identifier):
        if self._token_error:
            raise self._token_error
        return self._token


class _FakeMyPlexAccount:
    def __init__(self, users, token=None, title="Propietario", username="owner_user", email="owner@example.com"):
        self._users = users
        self.title = title
        self.username = username
        self.email = email

    def users(self):
        return self._users


class _FakeEpisode:
    def __init__(self, parent_index, index, watched, rating_key="ep1"):
        self.parentIndex = parent_index
        self.index = index
        self.isWatched = watched
        self.ratingKey = rating_key


class _FakeShow:
    def __init__(self, episodes):
        self._episodes = episodes

    def episodes(self):
        return self._episodes


class _FakeGuid:
    def __init__(self, id):
        self.id = id


class _FakeMovie:
    def __init__(self, guids, watched, rating_key="m1", title="Peli"):
        self.guids = guids
        self.isWatched = watched
        self.ratingKey = rating_key
        self.title = title
        self.marked_watched = False

    def markWatched(self):
        self.marked_watched = True


class _FakeSection:
    def __init__(self, type_, items):
        self.type = type_
        self._items = items

    def all(self):
        return self._items


class _FakeLibrary:
    def __init__(self, sections):
        self._sections = sections

    def sections(self):
        return self._sections


class _FakePlexServer:
    def __init__(self, baseurl, token, fetch_item=None, sections=None,
                 machine_identifier="mach1"):
        self._baseurl = baseurl
        self.token = token
        self.machineIdentifier = machine_identifier
        self._fetch_item = fetch_item
        self.library = _FakeLibrary(sections or [])

    def fetchItem(self, key):
        if self._fetch_item is None:
            raise RuntimeError("item no encontrado")
        return self._fetch_item


def test_get_plex_home_users_without_config_returns_none():
    assert msr.get_plex_home_users("", "") is None


def test_get_plex_home_users_returns_id_and_title(monkeypatch):
    users = [_FakePlexUser("1", "Ana"), _FakePlexUser("2", "Bruno")]
    monkeypatch.setattr(msr, "MyPlexAccount", lambda token: _FakeMyPlexAccount(
        users, username="CineBechos", title="CineBechos", email="tecnicoefrenrr@gmail.com"))
    result = msr.get_plex_home_users("http://plex:32400", "owner_tok")
    # El propietario va PRIMERO -- MyPlexAccount.users() nunca lo incluye
    # por sí solo (caso real reportado: faltaba en la lista de emparejar
    # para "Sincronizar visionado", el administrador de la cuenta también
    # quiere sincronizar su propio visionado). La parte del email antes
    # de la @, no username/title -- probado en vivo: esos dos dan el
    # nombre de marca del servidor Plex ("CineBechos"), no la persona.
    assert result == [
        {"id": "owner", "title": "tecnicoefrenrr"},
        {"id": "1", "title": "Ana"},
        {"id": "2", "title": "Bruno"},
    ]


def test_get_plex_home_users_owner_falls_back_to_title_or_username_without_email(monkeypatch):
    users = []
    monkeypatch.setattr(msr, "MyPlexAccount", lambda token: _FakeMyPlexAccount(
        users, username="CineBechos", title="Jose Title", email=""))
    result = msr.get_plex_home_users("http://plex:32400", "owner_tok")
    assert result == [{"id": "owner", "title": "Jose Title"}]


def test_get_plex_home_users_returns_none_on_failure(monkeypatch):
    def _raise(token):
        raise RuntimeError("plex.tv caido")
    monkeypatch.setattr(msr, "MyPlexAccount", _raise)
    assert msr.get_plex_home_users("http://plex:32400", "owner_tok") is None


def test_get_plex_user_token_without_config_returns_none():
    assert msr.get_plex_user_token("", "", "1") is None


def test_get_plex_user_token_owner_returns_owner_token_directly(monkeypatch):
    # id="owner" (ver get_plex_home_users) no aparece en account.users()
    # -- no se busca ahí, se devuelve owner_token tal cual, sin ni
    # siquiera llamar a MyPlexAccount/PlexServer.
    def _fail(*a, **kw):
        raise AssertionError("no debería llamarse para id=owner")
    monkeypatch.setattr(msr, "MyPlexAccount", _fail)
    monkeypatch.setattr(msr, "PlexServer", _fail)
    assert msr.get_plex_user_token("http://plex:32400", "owner_tok", "owner") == "owner_tok"


def test_get_plex_user_token_returns_token_for_matching_user(monkeypatch):
    users = [_FakePlexUser("1", "Ana", token="tok_ana"), _FakePlexUser("2", "Bruno", token="tok_bruno")]
    monkeypatch.setattr(msr, "MyPlexAccount", lambda token: _FakeMyPlexAccount(users))
    monkeypatch.setattr(msr, "PlexServer", lambda baseurl, token: _FakePlexServer(baseurl, token))
    assert msr.get_plex_user_token("http://plex:32400", "owner_tok", "2") == "tok_bruno"


def test_get_plex_user_token_returns_none_when_user_not_found(monkeypatch):
    users = [_FakePlexUser("1", "Ana", token="tok_ana")]
    monkeypatch.setattr(msr, "MyPlexAccount", lambda token: _FakeMyPlexAccount(users))
    monkeypatch.setattr(msr, "PlexServer", lambda baseurl, token: _FakePlexServer(baseurl, token))
    assert msr.get_plex_user_token("http://plex:32400", "owner_tok", "no_existe") is None


def test_get_plex_user_token_returns_none_when_switch_fails(monkeypatch):
    """Caso PIN-protegido u otro fallo del cambio de usuario -- se trata
    igual que "no disponible", nunca lanza."""
    users = [_FakePlexUser("1", "Ana", token_error=RuntimeError("necesita PIN"))]
    monkeypatch.setattr(msr, "MyPlexAccount", lambda token: _FakeMyPlexAccount(users))
    monkeypatch.setattr(msr, "PlexServer", lambda baseurl, token: _FakePlexServer(baseurl, token))
    assert msr.get_plex_user_token("http://plex:32400", "owner_tok", "1") is None


def test_get_plex_episode_watched_without_config_returns_none():
    assert msr.get_plex_episode_watched("", "", "100") is None


def test_get_plex_episode_watched_parses_season_episode_and_watched(monkeypatch):
    episodes = [
        _FakeEpisode(1, 1, True, rating_key="e11"),
        _FakeEpisode(1, 2, False, rating_key="e12"),
        _FakeEpisode(None, None, False),   # especial sin numero -- se ignora
    ]
    show = _FakeShow(episodes)
    monkeypatch.setattr(msr, "PlexServer",
                         lambda baseurl, token: _FakePlexServer(baseurl, token, fetch_item=show))
    result = msr.get_plex_episode_watched("http://plex:32400", "user_tok", "100")
    assert result == {
        (1, 1): {"watched": True, "ref": "e11"},
        (1, 2): {"watched": False, "ref": "e12"},
    }


def test_get_plex_movie_watched_without_config_returns_none():
    assert msr.get_plex_movie_watched("", "") is None


def test_get_plex_movie_watched_parses_tmdb_id_and_watched(monkeypatch):
    movies = [
        _FakeMovie([_FakeGuid("tmdb://111"), _FakeGuid("imdb://tt1")], True, rating_key="m111", title="Peli Uno"),
        _FakeMovie([_FakeGuid("tmdb://222")], False, rating_key="m222", title="Peli Dos"),
        _FakeMovie([_FakeGuid("imdb://tt3")], True),   # sin tmdb_id -- se excluye
    ]
    sections = [_FakeSection("movie", movies), _FakeSection("show", [])]
    monkeypatch.setattr(msr, "PlexServer",
                         lambda baseurl, token: _FakePlexServer(baseurl, token, sections=sections))
    result = msr.get_plex_movie_watched("http://plex:32400", "user_tok")
    assert result == {
        111: {"watched": True, "ref": "m111", "name": "Peli Uno"},
        222: {"watched": False, "ref": "m222", "name": "Peli Dos"},
    }


def test_mark_plex_watched_without_config_returns_false():
    assert msr.mark_plex_watched("", "", "100") is False


def test_mark_plex_watched_calls_markwatched_on_correct_item(monkeypatch):
    movie = _FakeMovie([], False)
    monkeypatch.setattr(msr, "PlexServer",
                         lambda baseurl, token: _FakePlexServer(baseurl, token, fetch_item=movie))
    assert msr.mark_plex_watched("http://plex:32400", "user_tok", "100") is True
    assert movie.marked_watched is True


def test_mark_plex_watched_returns_false_on_failure(monkeypatch):
    monkeypatch.setattr(msr, "PlexServer",
                         lambda baseurl, token: _FakePlexServer(baseurl, token, fetch_item=None))
    assert msr.mark_plex_watched("http://plex:32400", "user_tok", "100") is False


def test_get_jellyfin_users_without_config_returns_none():
    assert msr.get_jellyfin_users("", "") is None


def test_get_jellyfin_users_returns_id_and_name(monkeypatch):
    payload = [{"Id": "a1", "Name": "Ana"}, {"Id": "b2", "Name": "Bruno"}]
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    assert msr.get_jellyfin_users("http://jf:8096", "key") == [
        {"id": "a1", "name": "Ana"}, {"id": "b2", "name": "Bruno"}]


def test_get_jellyfin_episode_watched_without_config_returns_none():
    assert msr.get_jellyfin_episode_watched("", "", "u1", "s1") is None


def test_get_jellyfin_episode_watched_parses_userdata_played(monkeypatch):
    payload = {"Items": [
        {"Id": "ep11", "ParentIndexNumber": 1, "IndexNumber": 1, "UserData": {"Played": True}},
        {"Id": "ep12", "ParentIndexNumber": 1, "IndexNumber": 2, "UserData": {"Played": False}},
        {"Id": "ep_x", "ParentIndexNumber": None, "IndexNumber": None, "UserData": {"Played": True}},
    ]}
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    result = msr.get_jellyfin_episode_watched("http://jf:8096", "key", "u1", "s1")
    assert result == {
        (1, 1): {"watched": True, "ref": "ep11"},
        (1, 2): {"watched": False, "ref": "ep12"},
    }


def test_get_jellyfin_movie_watched_without_config_returns_none():
    assert msr.get_jellyfin_movie_watched("", "", "u1") is None


def test_get_jellyfin_movie_watched_parses_tmdb_and_played(monkeypatch):
    payload = {"Items": [
        {"Id": "mv111", "Name": "Peli Uno", "ProviderIds": {"Tmdb": "111"}, "UserData": {"Played": True}},
        {"Id": "mv222", "Name": "Peli Dos", "ProviderIds": {"Tmdb": "222"}, "UserData": {"Played": False}},
        {"Id": "mv_x", "Name": "Sin Tmdb", "ProviderIds": {}, "UserData": {"Played": True}},   # se excluye
    ]}
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    result = msr.get_jellyfin_movie_watched("http://jf:8096", "key", "u1")
    assert result == {
        111: {"watched": True, "ref": "mv111", "name": "Peli Uno"},
        222: {"watched": False, "ref": "mv222", "name": "Peli Dos"},
    }


def test_mark_jellyfin_watched_without_config_returns_false():
    assert msr.mark_jellyfin_watched("", "", "u1", "item1") is False


def test_mark_jellyfin_watched_success(monkeypatch):
    monkeypatch.setattr(msr.requests, "post", lambda *a, **kw: _FakeResponse(status=200))
    assert msr.mark_jellyfin_watched("http://jf:8096", "key", "u1", "item1") is True


def test_mark_jellyfin_watched_returns_false_on_failure(monkeypatch):
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(msr.requests, "post", _raise)
    assert msr.mark_jellyfin_watched("http://jf:8096", "key", "u1", "item1") is False


def test_verify_jellyfin_password_without_config_returns_false():
    assert msr.verify_jellyfin_password("", "", "") is False


def test_verify_jellyfin_password_true_on_200(monkeypatch):
    monkeypatch.setattr(msr.requests, "post", lambda *a, **kw: _FakeResponse(status=200))
    assert msr.verify_jellyfin_password("http://jf:8096", "Jose", "correcta") is True


def test_verify_jellyfin_password_false_on_401(monkeypatch):
    monkeypatch.setattr(msr.requests, "post", lambda *a, **kw: _FakeResponse(status=401))
    assert msr.verify_jellyfin_password("http://jf:8096", "Jose", "incorrecta") is False


def test_verify_jellyfin_password_false_on_network_failure(monkeypatch):
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(msr.requests, "post", _raise)
    assert msr.verify_jellyfin_password("http://jf:8096", "Jose", "correcta") is False


# ── get_jellyfin_series_item / get_plex_series_item (reescaneo individual) ──
# Caso real: al reescanear UNA serie que ya se borró del todo del servidor
# de medios, hay que distinguir "no encontrada de verdad" (ITEM_NOT_FOUND)
# de "fallo de red" (None) -- solo la primera debe hacer que se quite de la
# lista de huecos.

def test_get_jellyfin_series_item_returns_data_when_found(monkeypatch):
    payload = {"Items": [{"Id": "abc123", "Name": "El consultor",
                          "ProviderIds": {"Tmdb": "12345"}, "Path": "/datos2/series/El consultor"}]}
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    item = msr.get_jellyfin_series_item("http://jf:8096", "key", "abc123")
    assert item == {"id": "abc123", "name": "El consultor", "tmdb_id": 12345, "folder_name": "El consultor"}


def test_get_jellyfin_series_item_returns_not_found_on_empty_items(monkeypatch):
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse({"Items": []}))
    assert msr.get_jellyfin_series_item("http://jf:8096", "key", "abc123") is msr.ITEM_NOT_FOUND


def test_get_jellyfin_series_item_returns_not_found_on_404(monkeypatch):
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(status=404))
    assert msr.get_jellyfin_series_item("http://jf:8096", "key", "abc123") is msr.ITEM_NOT_FOUND


def test_get_jellyfin_series_item_returns_none_on_network_failure(monkeypatch):
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(msr.requests, "get", _raise)
    assert msr.get_jellyfin_series_item("http://jf:8096", "key", "abc123") is None


def test_get_jellyfin_series_item_without_config_returns_none():
    assert msr.get_jellyfin_series_item("", "", "abc123") is None


def test_get_plex_series_item_returns_data_when_found(monkeypatch):
    payload = {"MediaContainer": {"Metadata": [
        {"ratingKey": "999", "title": "El consultor",
         "Guid": [{"id": "tmdb://12345"}]},
    ]}}
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    item = msr.get_plex_series_item("http://plex:32400", "tok", "999")
    assert item == {"rating_key": "999", "name": "El consultor", "tmdb_id": 12345}


def test_get_plex_series_item_returns_not_found_on_404(monkeypatch):
    # Caso real: un ratingKey borrado responde 404 directamente (a
    # diferencia de la búsqueda de Jellyfin, que responde 200 con lista
    # vacía) -- ver el comentario en get_plex_series_item.
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(status=404))
    assert msr.get_plex_series_item("http://plex:32400", "tok", "999") is msr.ITEM_NOT_FOUND


def test_get_plex_series_item_returns_not_found_on_empty_metadata(monkeypatch):
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse({"MediaContainer": {}}))
    assert msr.get_plex_series_item("http://plex:32400", "tok", "999") is msr.ITEM_NOT_FOUND


def test_get_plex_series_item_returns_none_on_network_failure(monkeypatch):
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(msr.requests, "get", _raise)
    assert msr.get_plex_series_item("http://plex:32400", "tok", "999") is None


def test_get_plex_series_item_without_config_returns_none():
    assert msr.get_plex_series_item("", "", "999") is None


# ── Buscar por tmdb_id (Archivos -- "Abrir en Jellyfin/Plex") ──────────────

def test_find_jellyfin_item_by_tmdb_id_finds_series(monkeypatch):
    payload = {"Items": [
        {"Id": "abc123", "Name": "Serie X", "ProviderIds": {"Tmdb": "9999"}},
        {"Id": "def456", "Name": "Serie Y", "ProviderIds": {"Tmdb": "1111"}},
    ]}
    captured = {}
    def _fake_get(url, **kw):
        captured["params"] = kw.get("params")
        return _FakeResponse(payload)
    monkeypatch.setattr(msr.requests, "get", _fake_get)
    item = msr.find_jellyfin_item_by_tmdb_id("http://jf:8096", "key", 9999, "tv")
    assert item == {"id": "abc123", "name": "Serie X"}
    assert captured["params"]["IncludeItemTypes"] == "Series"


def test_find_jellyfin_item_by_tmdb_id_uses_movie_type_for_movies(monkeypatch):
    captured = {}
    def _fake_get(url, **kw):
        captured["params"] = kw.get("params")
        return _FakeResponse({"Items": []})
    monkeypatch.setattr(msr.requests, "get", _fake_get)
    msr.find_jellyfin_item_by_tmdb_id("http://jf:8096", "key", 9999, "movie")
    assert captured["params"]["IncludeItemTypes"] == "Movie"


def test_find_jellyfin_item_by_tmdb_id_returns_none_when_not_found(monkeypatch):
    payload = {"Items": [{"Id": "abc123", "Name": "Serie Y", "ProviderIds": {"Tmdb": "1111"}}]}
    monkeypatch.setattr(msr.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    assert msr.find_jellyfin_item_by_tmdb_id("http://jf:8096", "key", 9999, "tv") is None


def test_find_jellyfin_item_by_tmdb_id_without_config_returns_none():
    assert msr.find_jellyfin_item_by_tmdb_id("", "", 9999, "tv") is None


def test_find_jellyfin_item_by_tmdb_id_returns_none_on_network_failure(monkeypatch):
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(msr.requests, "get", _raise)
    assert msr.find_jellyfin_item_by_tmdb_id("http://jf:8096", "key", 9999, "tv") is None


def test_find_plex_item_by_tmdb_id_finds_movie(monkeypatch):
    sections_payload = {"MediaContainer": {"Directory": [
        {"key": "1", "type": "show", "title": "Series"},
        {"key": "2", "type": "movie", "title": "Peliculas"},
    ]}}
    movies_payload = {"MediaContainer": {"Metadata": [
        {"ratingKey": "500", "title": "Peli X", "Guid": [{"id": "tmdb://7777"}]},
    ]}}
    captured = {}
    def _fake_get(url, **kw):
        if url.endswith("/library/sections"):
            return _FakeResponse(sections_payload)
        captured["params"] = kw.get("params")
        return _FakeResponse(movies_payload)
    monkeypatch.setattr(msr.requests, "get", _fake_get)
    item = msr.find_plex_item_by_tmdb_id("http://plex:32400", "tok", 7777, "movie")
    assert item == {"rating_key": "500", "name": "Peli X"}
    assert captured["params"]["type"] == "1"


def test_find_plex_item_by_tmdb_id_only_searches_matching_section_type(monkeypatch):
    sections_payload = {"MediaContainer": {"Directory": [
        {"key": "1", "type": "show", "title": "Series"},
    ]}}
    calls = []
    def _fake_get(url, **kw):
        calls.append(url)
        if url.endswith("/library/sections"):
            return _FakeResponse(sections_payload)
        return _FakeResponse({"MediaContainer": {"Metadata": []}})
    monkeypatch.setattr(msr.requests, "get", _fake_get)
    msr.find_plex_item_by_tmdb_id("http://plex:32400", "tok", 7777, "movie")
    # Sin sección "movie" en la biblioteca -- solo se llama a /library/sections, nunca a /all
    assert len(calls) == 1


def test_find_plex_item_by_tmdb_id_returns_none_when_not_found(monkeypatch):
    sections_payload = {"MediaContainer": {"Directory": [{"key": "1", "type": "show", "title": "Series"}]}}
    shows_payload = {"MediaContainer": {"Metadata": [
        {"ratingKey": "100", "title": "Serie Y", "Guid": [{"id": "tmdb://1111"}]}]}}
    def _fake_get(url, **kw):
        if url.endswith("/library/sections"):
            return _FakeResponse(sections_payload)
        return _FakeResponse(shows_payload)
    monkeypatch.setattr(msr.requests, "get", _fake_get)
    assert msr.find_plex_item_by_tmdb_id("http://plex:32400", "tok", 9999, "tv") is None


def test_find_plex_item_by_tmdb_id_without_config_returns_none():
    assert msr.find_plex_item_by_tmdb_id("", "", 9999, "tv") is None


def test_find_plex_item_by_tmdb_id_returns_none_on_network_failure(monkeypatch):
    def _raise(*a, **kw):
        raise ConnectionError("sin red")
    monkeypatch.setattr(msr.requests, "get", _raise)
    assert msr.find_plex_item_by_tmdb_id("http://plex:32400", "tok", 9999, "tv") is None
