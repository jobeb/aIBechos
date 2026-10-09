"""core/missing_ep_scan.py -- el escaneo de "Episodios que faltan" movido desde
gui/app.py a una mixin que heredan la interfaz Tk y la Qt. Se prueba de punta a
punta con un host falso (sin red, sin FTP, caché en memoria)."""

import core.media_server_refresh as msr
import core.missing_episodes_cache as mec
from core.missing_ep_scan import MissingEpScanMixin


class _Tmdb:
    def get_tv_details(self, tmdb_id):
        return {"first_air_date": "2020-01-01", "last_episode_to_air": {"id": 99},
                "seasons": [{"season_number": 0}, {"season_number": 1, "air_date": "2020-01-01"}]}

    def get_season_episodes(self, tmdb_id, season):
        return [{"episode_number": n, "name": f"Ep {n}", "air_date": f"2020-01-0{n}"} for n in range(1, 5)]


class _FtpDown:
    def connect(self, *a, **k):
        return False, "sin servidor"

    def is_connected(self):
        return False

    def disconnect(self):
        pass


class _Host(MissingEpScanMixin):
    def __init__(self):
        self.config_data = {"jellyfin_enabled": True, "jellyfin_host": "h", "jellyfin_api_key": "k",
                            "app_user_name": "yo"}
        self.tmdb = _Tmdb()
        self._ftp_dir_cache = {}
        self.pushed = 0
        self.audio_calls = []

    def _new_ftp_client(self):
        return _FtpDown()

    def _push_missing_episodes_to_ftp(self):
        self.pushed += 1

    def _refresh_server_audio(self, source, server_id, tmdb_id):
        self.audio_calls.append((source, server_id, tmdb_id))

    def _scan_notify(self, text, color):
        pass


def _patch(monkeypatch, store):
    monkeypatch.setattr(msr, "get_jellyfin_series",
                        lambda host, key: [{"id": "J1", "name": "Serie A", "tmdb_id": 10, "folder_name": "Serie A"},
                                           {"id": "J2", "name": "Serie B", "tmdb_id": 20}])
    present = {"J1": {(1, 1), (1, 2)}, "J2": {(1, 1), (1, 2), (1, 3), (1, 4)}}
    monkeypatch.setattr(msr, "get_jellyfin_episodes", lambda host, key, sid: present[sid])
    monkeypatch.setattr(msr, "get_jellyfin_usage_stats", lambda *a, **k: {})
    monkeypatch.setattr(mec, "load_cache", lambda: dict(store))
    monkeypatch.setattr(mec, "save_cache", lambda c: store.clear() or store.update(c))


def test_scan_finds_gaps_saves_and_shares(monkeypatch):
    store = {}
    _patch(monkeypatch, store)
    host = _Host()
    progress = []
    live = []
    results = host._scan_missing_episodes(progress_cb=lambda c, t, n: progress.append((c, t)),
                                          force_full=True, on_result_cb=live.append)
    assert [r["name"] for r in results] == ["Serie A"]
    assert results[0]["missing"] == {1: [3, 4]}
    assert results[0]["episode_titles"][1][3] == "Ep 3"
    assert live and live[0]["tmdb_id"] == 10
    assert progress[-1] == (2, 2)
    # La caché guarda las DOS series (B completa) y la marca del escaneo.
    assert store["10"]["missing"] == {"1": [3, 4]}
    assert store["20"]["missing"] == {}
    assert store["_meta"]["scanned_by"] == "yo"
    assert host.pushed == 1
    assert ("jellyfin", "J1", 10) in host.audio_calls


def test_incremental_scan_skips_unchanged_complete_series(monkeypatch):
    store = {}
    _patch(monkeypatch, store)
    host = _Host()
    host._scan_missing_episodes(force_full=True)
    host.audio_calls.clear()
    results = host._scan_missing_episodes(force_full=False)
    # B ya estaba completa y TMDB no tiene episodio nuevo: ni se consulta.
    assert ("jellyfin", "J2", 20) not in host.audio_calls
    assert [r["tmdb_id"] for r in results] == [10]


def test_ignored_marks_survive_rescan(monkeypatch):
    store = {}
    _patch(monkeypatch, store)
    host = _Host()
    host._scan_missing_episodes(force_full=True)
    store["10"]["ignored_episodes"] = {"1": [4]}
    store["10"]["ignored"] = True
    results = host._scan_missing_episodes(force_full=True)
    assert results[0]["ignored"] is True
    assert results[0]["ignored_episodes"] == {1: {4}}


def test_compute_dub_updates_uses_merged_cutoff(monkeypatch):
    host = _Host()
    monkeypatch.setattr(host, "_fetch_dub_cutoff",
                        lambda tmdb_id, name, pending, **kw: {"eldoblaje": {"cutoff": {"1": 3}, "checked_at": 1}})
    import core.eldoblaje as eld
    monkeypatch.setattr(eld, "cutoff_is_fresh", lambda entry, now: False)
    pending = [(10, "Serie A", 1, 3), (10, "Serie A", 1, 4), (10, "Serie A", 2, 1)]
    updates = host._compute_dub_updates(pending, {})
    eps = updates["10"]["episodes"]
    assert eps == {"1x03": True, "1x04": False}   # T2 sin corte: sin dato
    assert updates["10"]["eldoblaje"]["cutoff"] == {"1": 3}
