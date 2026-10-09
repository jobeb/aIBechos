"""▶ en Archivos: local si está en disco, stream directo en Jellyfin si ya
está subido (core/app_files_core.py::_play_file). Sin red ni GUI reales."""

import pytest

from core.api_client import MediaInfo
from core.app_files_core import FilesCoreMixin
from core.file_entry import FileEntry


class _Cfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _Host(FilesCoreMixin):
    def __init__(self, cfg):
        self.config_data = _Cfg(cfg)
        self.calls = []

    def after(self, ms, fn):
        fn()

    def _set_status(self, text, color=""):
        self.calls.append(("status", text))


def _entry(tmp_path, status="subido", media_type="tv", season=1, episode=2):
    p = tmp_path / "Serie 1x02.mkv"
    p.write_bytes(b"x")
    e = FileEntry(str(p))
    e.status = status
    e.media_info = MediaInfo(tmdb_id=100, media_type=media_type, title="Serie",
                             original_title="Serie", year="2024", season=season, episode=episode)
    return e


def _host_jellyfin():
    return _Host({"jellyfin_enabled": True, "jellyfin_host": "http://nas:8096/",
                  "jellyfin_api_key": "KEY"})


def test_jellyfin_play_available(tmp_path):
    assert _host_jellyfin()._jellyfin_play_available(_entry(tmp_path)) is True
    h = _host_jellyfin()
    assert h._jellyfin_play_available(_entry(tmp_path, media_type="libro")) is False
    e = _entry(tmp_path)
    e.media_info = None
    assert h._jellyfin_play_available(e) is False
    h_off = _Host({"jellyfin_enabled": False, "jellyfin_host": "http://nas:8096/",
                   "jellyfin_api_key": "KEY"})
    assert h_off._jellyfin_play_available(_entry(tmp_path)) is False
    h_nokey = _Host({"jellyfin_enabled": True, "jellyfin_host": "http://nas:8096/"})
    assert h_nokey._jellyfin_play_available(_entry(tmp_path)) is False


def test_play_subido_va_a_jellyfin(tmp_path, monkeypatch):
    h = _host_jellyfin()
    monkeypatch.setattr(h, "_play_in_jellyfin_worker", lambda e: h.calls.append(("jelly", e)))
    monkeypatch.setattr(h, "_play_local_file", lambda e: h.calls.append(("local", e)))
    h._play_file(_entry(tmp_path, status="subido"))
    assert [c[0] for c in h.calls] == ["status", "jelly"]


def test_play_no_subido_abre_local(tmp_path, monkeypatch):
    h = _host_jellyfin()
    monkeypatch.setattr(h, "_play_in_jellyfin_worker", lambda e: h.calls.append(("jelly", e)))
    monkeypatch.setattr(h, "_play_local_file", lambda e: h.calls.append(("local", e)))
    h._play_file(_entry(tmp_path, status="listo"))
    assert [c[0] for c in h.calls] == ["local"]


def test_worker_serie_abre_stream_del_capitulo(tmp_path, monkeypatch):
    import core.media_server_refresh as msr
    import webbrowser
    h = _host_jellyfin()
    monkeypatch.setattr(msr, "find_jellyfin_item_by_tmdb_id",
                        lambda host, key, tid, mt: {"id": "SERIE1", "name": "Serie"})
    seen = {}

    def fake_ep(host, key, sid, s, e):
        seen.update(sid=sid, s=s, e=e)
        return "EP9"
    monkeypatch.setattr(msr, "find_jellyfin_episode_id", fake_ep)
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    h._play_in_jellyfin_worker(_entry(tmp_path))
    assert seen == {"sid": "SERIE1", "s": 1, "e": 2}
    assert opened == ["http://nas:8096/Videos/EP9/stream?api_key=KEY"]
    assert any(c[0] == "status" and "Jellyfin" in c[1] for c in h.calls)


def test_worker_pelicula_sin_episodio(tmp_path, monkeypatch):
    import core.media_server_refresh as msr
    import webbrowser
    h = _host_jellyfin()
    calls = []
    monkeypatch.setattr(msr, "find_jellyfin_item_by_tmdb_id",
                        lambda host, key, tid, mt: calls.append(mt) or {"id": "PELI1"})
    monkeypatch.setattr(msr, "find_jellyfin_episode_id",
                        lambda *a: (_ for _ in ()).throw(AssertionError("no debe buscar episodio")))
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    h._play_in_jellyfin_worker(_entry(tmp_path, media_type="movie", season=None, episode=None))
    assert calls == ["movie"]
    assert opened == ["http://nas:8096/Videos/PELI1/stream?api_key=KEY"]


def test_worker_sin_jellyfin_cae_al_local(tmp_path, monkeypatch):
    import core.media_server_refresh as msr
    h = _host_jellyfin()
    monkeypatch.setattr(msr, "find_jellyfin_item_by_tmdb_id", lambda *a: None)
    monkeypatch.setattr(h, "_play_local_file", lambda e: h.calls.append(("local", e)))
    h._play_in_jellyfin_worker(_entry(tmp_path))
    assert "local" in [c[0] for c in h.calls]
    assert any(c[0] == "status" and "local" in c[1] for c in h.calls)


def test_worker_sin_jellyfin_ni_local_avisa(tmp_path, monkeypatch):
    import core.media_server_refresh as msr
    h = _host_jellyfin()
    monkeypatch.setattr(msr, "find_jellyfin_item_by_tmdb_id", lambda *a: None)
    e = _entry(tmp_path)
    e.path = str(tmp_path / "borrado.mkv")
    h._play_in_jellyfin_worker(e)
    assert any(c[0] == "status" and "Jellyfin" in c[1] for c in h.calls)


def test_find_episode_id(monkeypatch):
    import core.media_server_refresh as msr

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"Items": [{"Id": "A", "ParentIndexNumber": 1, "IndexNumber": 1},
                              {"Id": "B", "ParentIndexNumber": 1, "IndexNumber": 2},
                              {"Id": "C"}]}

    monkeypatch.setattr(msr.requests, "get", lambda *a, **k: _Resp())
    assert msr.find_jellyfin_episode_id("h", "k", "S", 1, 2) == "B"
    assert msr.find_jellyfin_episode_id("h", "k", "S", 3, 1) is None
    assert msr.find_jellyfin_episode_id("", "k", "S", 1, 1) is None
