"""Vídeos con la extensión perdida (aMule corta los nombres muy largos y
se come el ".mkv"): AutoWatcher los reconoce por sus primeros bytes y les
devuelve la extensión. Real: "La sociedad literaria y el pastel de piel
de patata (...).BDrip.1080p.x264-A" ignorado como "tipo no reconocido"."""

from pathlib import Path
from types import SimpleNamespace

import core.auto_watcher as aw
from core.auto_watcher import AutoWatcher, sniff_video_ext


def _write(path: Path, head: bytes, size=64):
    path.write_bytes(head + b"\0" * (size - len(head)))
    return path


def test_sniff(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "_SNIFF_MIN_BYTES", 16)
    assert sniff_video_ext(_write(tmp_path / "a.x264-A", bytes.fromhex("1a45dfa3"))) == ".mkv"
    assert sniff_video_ext(_write(tmp_path / "b", b"\0\0\0\x20ftypisom")) == ".mp4"
    assert sniff_video_ext(_write(tmp_path / "c", b"RIFF\0\0\0\0AVI ")) == ".avi"
    assert sniff_video_ext(_write(tmp_path / "d.nfo", b"texto normal")) is None
    assert sniff_video_ext(_write(tmp_path / "e.mkv.part", bytes.fromhex("1a45dfa3"))) is None
    monkeypatch.setattr(aw, "_SNIFF_MIN_BYTES", 1000)
    assert sniff_video_ext(tmp_path / "a.x264-A") is None, "lo pequeño no se mira"


def _watcher():
    w = SimpleNamespace(on_event=lambda *a, **k: None, _is_stable=lambda p: True)
    return w


def test_restore_renombra(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "_SNIFF_MIN_BYTES", 16)
    f = _write(tmp_path / "Peli (2018) BDrip.1080p.x264-A", bytes.fromhex("1a45dfa3"))
    assert AutoWatcher._restore_video_ext(_watcher(), f) is True
    assert (tmp_path / "Peli (2018) BDrip.1080p.x264-A.mkv").exists()
    assert not f.exists()


def test_restore_no_pisa_ni_toca_inestables(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "_SNIFF_MIN_BYTES", 16)
    f = _write(tmp_path / "x", bytes.fromhex("1a45dfa3"))
    (tmp_path / "x.mkv").write_bytes(b"otro")
    assert AutoWatcher._restore_video_ext(_watcher(), f) is False
    assert f.exists()
    g = _write(tmp_path / "y", bytes.fromhex("1a45dfa3"))
    w = _watcher()
    w._is_stable = lambda p: False
    assert AutoWatcher._restore_video_ext(w, g) is False
    assert g.exists()
