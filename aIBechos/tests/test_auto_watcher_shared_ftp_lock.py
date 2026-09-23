"""AutoWatcher debe poder compartir su candado de conexión FTP con quien lo
construya (gui/app.py::App._ftp_cmd_lock), no solo usar uno propio -- self.ftp
es una única conexión de control compartida también con otras partes de la
GUI (refresco de espacio libre, "Probar conexión", cruce FTP del detector de
huecos), y ftplib no es seguro entre hilos: sin un candado en común entre
AutoWatcher y esas otras llamadas, sus respuestas se cruzan (visto de
verdad: "550 Failed to change directory" / "200 Switching to Binary mode"
tratados como error)."""

import threading
from unittest.mock import MagicMock

from core.auto_watcher import AutoWatcher


def _make_watcher(tmp_path, ftp_lock=None):
    config = MagicMock()
    config.get.side_effect = lambda k, d=None: {"poll_interval": 10}.get(k, d)
    return AutoWatcher(str(tmp_path), config, MagicMock(), MagicMock(),
                        on_event=lambda *a, **k: None, ftp_lock=ftp_lock)


def test_without_ftp_lock_creates_its_own(tmp_path):
    watcher = _make_watcher(tmp_path)
    assert isinstance(watcher._ftp_lock, type(threading.Lock()))


def test_uses_the_shared_lock_when_given(tmp_path):
    shared_lock = threading.Lock()
    watcher = _make_watcher(tmp_path, ftp_lock=shared_lock)
    assert watcher._ftp_lock is shared_lock
