"""Una sustitución pedida desde la web la coge UNA sola subida a la vez, y
nunca se borra como "viejo" un archivo que este mismo watcher está subiendo.
Real (La Odisea, 11/10): la misma petición se aplicó al .mkv y al .avi a la
vez; el .avi terminó antes y borró en el servidor el .mkv que aún se subía,
que después dio "OK" sin existir y se borró también en local."""

import threading
import time
from unittest.mock import MagicMock

import core.auto_watcher as autowatcher_mod
import core.slim_pending as slim_pending
from core.auto_watcher import AutoWatcher
from core.api_client import MediaInfo


class _FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def _make_watcher(folder, ftp, events):
    config = _FakeConfig({
        "poll_interval": 10,
        "movie_template": "{serie} ({año}){ext}",
        "min_confidence": 0,
        "rename_local": False,
        "ftp_host": "servidor",
        "ftp_parallel": 2,
        "ftp_categories": {
            "movie": [{"id": "c1", "name": "Peliculas", "genre_ids": [],
                       "root": "/peliculas", "template": "{serie}/"}],
            "tv": [],
        },
    })
    tmdb = MagicMock()
    tmdb.search_multi.side_effect = lambda query, *a, **kw: [
        {"id": 7, "title": "Pelicula", "media_type": "movie",
         "release_date": "2024-01-01", "genre_ids": []}]
    tmdb.build_media_info.side_effect = lambda result, **kw: MediaInfo(
        tmdb_id=7, media_type="movie", title="Pelicula",
        original_title="Pelicula", year="2024", genre_ids=[])
    w = AutoWatcher(str(folder), config, tmdb, ftp,
                    on_event=lambda *a, **k: None,
                    on_file_event=lambda p, t, **k: events.append((p, t, k)),
                    ftp_factory=lambda: ftp)
    w._is_stable = lambda path: True
    return w


def test_one_request_is_not_applied_to_two_files_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(autowatcher_mod, "_processed_db_path", lambda: tmp_path / "db.json")
    monkeypatch.setattr(slim_pending, "_path", lambda: tmp_path / "slim_pending.json")
    slim_pending.record({"kind": "request", "media_type": "movie",
                         "key_norm": slim_pending.norm_key("Pelicula"), "tmdb_id": 7,
                         "year": "2024", "season": None, "episode": None,
                         "heavy_remote_file": "", "added_ts": time.time()})

    src = tmp_path / "w"
    src.mkdir()
    (src / "Pelicula 1080p.mkv").write_bytes(b"m" * 50)

    remote = set()
    mkv_started, release_mkv = threading.Event(), threading.Event()

    def upload(local_path, remote_path, **kw):
        name = kw["remote_filename"]
        remote.add(name)
        if name.endswith(".mkv"):
            mkv_started.set()
            release_mkv.wait(5)
        return True, "ok"

    ftp = MagicMock()
    ftp.is_connected.return_value = True
    ftp.connect.return_value = (True, "ok")
    ftp.build_remote_path.return_value = "/peliculas/Pelicula/"
    ftp.get_free_space.return_value = None
    ftp.get_remote_size.return_value = None
    ftp.list_files.side_effect = ftp.list_files_checked.side_effect = lambda path: sorted(remote)
    ftp.delete_file.side_effect = lambda p: (remote.discard(p.rsplit("/", 1)[-1]) or True, "")
    ftp.upload_file.side_effect = upload

    events = []
    w = _make_watcher(src, ftp, events)
    w._scan()
    assert mkv_started.wait(5)

    # Llega la segunda copia mientras la primera sigue subiendo.
    (src / "Pelicula dvdrip.avi").write_bytes(b"a" * 30)
    w._scan()
    deadline = time.monotonic() + 5
    while not any(p.endswith(".avi") and t in ("skip", "uploaded") for p, t, _ in events) \
            and time.monotonic() < deadline:
        time.sleep(0.02)
    release_mkv.set()
    deadline = time.monotonic() + 5
    while not any(p.endswith(".mkv") and t == "uploaded" for p, t, _ in events) \
            and time.monotonic() < deadline:
        time.sleep(0.02)

    deleted = [c.args[0] for c in ftp.delete_file.call_args_list]
    assert not any(d.endswith(".mkv") for d in deleted), \
        f"se borró el .mkv que aún se subía: {deleted}"
    assert "Pelicula (2024).mkv" in remote


def test_finish_replacement_never_deletes_an_in_flight_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(slim_pending, "_path", lambda: tmp_path / "slim_pending.json")
    ftp = MagicMock()
    w = _make_watcher(tmp_path, ftp, [])
    w._uploading_remote.add("/peliculas/X/Viejo.mkv")
    w._finish_request_replacement({"kind": "request"}, "/peliculas/X/", "Nuevo.avi",
                                  "Viejo.mkv", "Nuevo.avi")
    ftp.delete_file.assert_not_called()


def test_claim_request_is_exclusive_until_released(tmp_path):
    w = _make_watcher(tmp_path, MagicMock(), [])
    req = {"kind": "request", "media_type": "movie", "key_norm": "x"}
    a, b = threading.Event(), threading.Event()
    assert w._claim_request(req, a)
    assert w._claim_request(req, a), "la misma subida puede volver a reclamarla"
    assert not w._claim_request(req, b)
