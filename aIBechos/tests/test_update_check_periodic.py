"""Comprobación de actualizaciones: automática (al abrir y cada 6 h, aviso
una vez por versión, notificación si está en la bandeja) y manual (botón de
Ajustes, siempre informa)."""
from core import update_check
from core.app_settings_core import SettingsCoreMixin


class _View:
    def __init__(self):
        self.done = []

    def on_update_check_done(self, text):
        self.done.append(text)


class _Host(SettingsCoreMixin):
    def __init__(self, visible=True, skipped=""):
        self.config_data = {"skipped_update_version": skipped}
        self.settings_view = _View()
        self._update_check_running = False
        self._update_check_manual_waiting = False
        self._update_notified_tags = set()
        self._pending_update = None
        self.visible = visible
        self.dialogs, self.notes, self.statuses = [], [], []

    def _window_is_visible(self):
        return self.visible

    def _show_update_dialog(self, tag, url):
        self.dialogs.append(tag)

    def _send_notification(self, title, msg):
        self.notes.append(msg)

    def _set_status(self, msg, color=None):
        self.statuses.append(msg)


UPD = {"status": "update", "tag": "v9.0.0", "url": "https://x"}


def test_automatica_avisa_una_sola_vez_por_version():
    h = _Host()
    h._on_update_check_result(UPD, manual=False)
    h._on_update_check_result(UPD, manual=False)
    assert h.dialogs == ["v9.0.0"]


def test_automatica_respeta_saltar_version_pero_la_manual_no():
    h = _Host(skipped="v9.0.0")
    h._on_update_check_result(UPD, manual=False)
    assert h.dialogs == []
    h._on_update_check_result(UPD, manual=True)
    assert h.dialogs == ["v9.0.0"]


def test_en_la_bandeja_notifica_y_queda_pendiente():
    h = _Host(visible=False)
    h._on_update_check_result(UPD, manual=False)
    assert h.dialogs == [] and len(h.notes) == 1
    h._show_pending_update()
    assert h.dialogs == ["v9.0.0"]
    h._show_pending_update()            # solo una vez
    assert h.dialogs == ["v9.0.0"]


def test_automatica_silenciosa_si_al_dia_o_error():
    h = _Host()
    h._on_update_check_result({"status": "current", "tag": "v1"}, manual=False)
    h._on_update_check_result({"status": "error", "error": "x"}, manual=False)
    assert not (h.dialogs or h.notes or h.statuses or h.settings_view.done)


def test_manual_informa_al_dia_y_error():
    h = _Host()
    h._on_update_check_result({"status": "current", "tag": "v1"}, manual=True)
    h._on_update_check_result({"status": "error", "error": "sin red"}, manual=True)
    assert len(h.settings_view.done) == 2
    assert "última versión" in h.settings_view.done[0]
    assert "No se pudo" in h.settings_view.done[1]


def test_boton_durante_una_comprobacion_automatica_no_se_queda_colgado():
    h = _Host()
    h._update_check_running = True
    h._check_for_updates(manual=True)          # no lanza otra
    assert h._update_check_manual_waiting
    h._on_update_check_result({"status": "current", "tag": "v1"}, manual=False)
    assert h.settings_view.done and not h._update_check_manual_waiting


def test_latest_release_status(monkeypatch):
    class _R:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data
    monkeypatch.setattr(update_check.requests, "get",
                        lambda *a, **k: _R({"tag_name": "v2.0.8", "html_url": "u"}))
    assert update_check.latest_release_status("2.0.8")["status"] == "current"
    assert update_check.latest_release_status("2.0.7")["status"] == "update"

    def _boom(*a, **k): raise OSError("sin red")
    monkeypatch.setattr(update_check.requests, "get", _boom)
    assert update_check.latest_release_status("2.0.8")["status"] == "error"
