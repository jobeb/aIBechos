"""Pestaña Info (gui_qt/info/): contenedor perezoso con Historial,
Solicitudes web, Protegidos, Sincronizar visionado y Estadísticas;
Solicitudes separada del Historial, con Cancelar por fila activa."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QObject, Signal  # noqa: E402

from gui_qt.actions import ActionsRole  # noqa: E402
from gui_qt.info.tab import SPECS, InfoTab  # noqa: E402


class _Ctx(QObject):
    status = Signal(object, object)
    reservations_changed = Signal()

    def set_status(self, *a):
        pass

    def sync_reservations(self):
        pass


class _Cfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        pass


class _Host:
    def __init__(self):
        self.ctx = _Ctx()
        self.config_data = _Cfg({"app_user_name": "Jose"})
        self.tmdb = object()
        self._reservations = {}
        self._shared_activity_history = []
        self._web_requests_rows = []
        self._web_requests_error = ""
        self._history_all = []
        self._history_visible = False
        self._requests_visible = False
        self._stats_visible = False
        self._last_web_requests_sync_ts = 0.0
        self._shared_category_stats = {}
        self._shared_upload_stats = {}
        self._shared_deletion_stats = {}
        self._shared_slim_stats = {}
        self._shared_category_upload_stats = {}
        self._shared_free_space_by_disk = {}
        self.history_view = None
        self.requests_view = None
        self.info_view = None
        self.cancelled = []

    def after(self, ms, fn):
        fn()

    def _load_history(self):
        return []

    def _history_entry_matches(self, entry, query):
        return True

    def _sync_activity_history_from_ftp(self):
        pass

    def _sync_web_requests_history(self):
        pass

    def _quota_status(self, user):
        return 0.0, 10.0, "#fff", ""

    def _load_watch_sync_history(self):
        return []

    def _sync_upload_stats_from_ftp(self):
        pass

    def _sync_category_stats_from_ftp(self):
        pass

    def _sync_deletion_stats_from_ftp(self):
        pass

    def _sync_slim_stats_from_ftp(self):
        pass

    def _sync_category_upload_stats_from_ftp(self):
        pass

    def _cancel_web_request(self, req_id):
        self.cancelled.append(req_id)
        return True


def _req(rid, status):
    return {"kind": "solicitud", "id": rid, "ts": 1.0, "name": f"Peli {rid}",
            "person": "Jose", "status": status, "status_es": status, "detail": ""}


def _info(qtbot):
    from gui_qt.bridge import install
    install()
    info = InfoTab(_Host())
    qtbot.addWidget(info)
    return info


def test_subpestanas_y_claves(qtbot):
    info = _info(qtbot)
    assert [t for t, _k, _c in SPECS] == ["📋 Historial", "🌐 Solicitudes web", "🔒 Protegidos",
                                          "🔄 Sincronizar visionado", "📊 Estadísticas"]
    assert info.current_key() == "history"
    assert set(info.pages) == {"history"}  # perezoso: solo la visible
    for i, expected in enumerate(["history", "requests", "protected", "watch_sync", "stats"]):
        info.tabs.setCurrentIndex(i)
        assert info.current_key() == expected
    assert set(info.pages) == {"history", "requests", "protected", "watch_sync", "stats"}
    assert isinstance(info.host.history_view, object)
    assert isinstance(info.host.requests_view, object)


def test_historial_sin_solicitudes(qtbot):
    info = _info(qtbot)
    hist = info.pages["history"]
    assert not hasattr(hist, "requests")
    hist.refresh()
    assert "Historial de subidas" in hist.title.text()


def test_solicitudes_con_cancel(qtbot, monkeypatch):
    info = _info(qtbot)
    info.tabs.setCurrentIndex(1)
    req = info.pages["requests"]
    req.host._web_requests_rows = [_req("a", "pending"), _req("b", "done")]
    req.refresh()
    assert len(req.model.rows) == 2
    assert "2" in req.title.text()
    acts = req.model.data(req.model.index(0, 7), ActionsRole)
    assert [a.id for a in acts] == ["cancel"] and acts[0].enabled
    acts_done = req.model.data(req.model.index(1, 7), ActionsRole)
    assert not acts_done[0].enabled
    import gui_qt.info.requests as rq
    monkeypatch.setattr(rq, "confirm", lambda *a, **k: True)
    req._on_action(req.model.index(0, 7), "cancel")
    qtbot.wait(500)
    assert req.host.cancelled == ["a"]


def test_reenvios_shown_hidden(qtbot):
    info = _info(qtbot)
    info.on_shown()
    assert info.host._history_visible is True
    info.tabs.setCurrentIndex(1)
    assert info.host._requests_visible is True
    info.on_hidden()
    assert info.host._history_visible is False
    assert info.host._requests_visible is False
