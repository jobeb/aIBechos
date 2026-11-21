"""Actualización automática (core/self_update.py y el flujo de
SettingsCoreMixin): qué paquete toca, preparación y cuándo se aplica."""
import hashlib
import io
import zipfile

import pytest

from core import self_update
from core.app_settings_core import SettingsCoreMixin

ASSETS = [
    {"name": "aIBechos_2.2.0.dmg", "url": "u-dmg", "size": 1},
    {"name": "aIBechos_2.2.0_linux.tar.gz", "url": "u-linux", "size": 1},
    {"name": "aIBechos_2.2.0_windows.zip", "url": "u-zip", "size": 1},
    {"name": "aIBechos_Setup_2.2.0.exe", "url": "u-setup", "size": 1},
]


def test_install_kind(tmp_path):
    exe = tmp_path / "aIBechos.exe"
    exe.write_bytes(b"")
    assert self_update.install_kind(frozen=False, executable=exe, platform="win32") is None
    assert self_update.install_kind(True, exe, "win32") == "portable_win"
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert self_update.install_kind(True, exe, "win32") == "installer"
    app = tmp_path / "aIBechos.app" / "Contents" / "MacOS" / "aIBechos"
    assert self_update.install_kind(True, app, "darwin") == "macos"
    assert self_update.install_kind(True, tmp_path / "aIBechos", "linux") == "linux"


@pytest.mark.parametrize("kind,url", [("installer", "u-setup"), ("portable_win", "u-zip"),
                                      ("macos", "u-dmg"), ("linux", "u-linux")])
def test_pick_asset(kind, url):
    assert self_update.pick_asset(ASSETS, kind)["url"] == url


def test_pick_asset_sin_paquete():
    assert self_update.pick_asset(ASSETS[:1], "installer") is None
    assert self_update.pick_asset(None, "linux") is None


def _zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("aIBechos/aIBechos.exe", b"nuevo")
        z.writestr("aIBechos/_internal/a.dll", b"x")
    return buf.getvalue()


class _Resp:
    def __init__(self, data):
        self.data = data
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def raise_for_status(self): pass
    def iter_content(self, n): yield self.data


def test_prepare_portable(tmp_path, monkeypatch):
    data = _zip_bytes()
    monkeypatch.setattr(self_update, "app_data_dir", lambda: tmp_path / "datos")
    monkeypatch.setattr(self_update.requests, "get", lambda *a, **k: _Resp(data))
    app = tmp_path / "app"
    app.mkdir()
    exe = app / "aIBechos.exe"
    exe.write_bytes(b"viejo")
    asset = {"name": "aIBechos_2.2.0_windows.zip", "url": "u", "size": len(data),
             "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
    prep = self_update.prepare("v2.2.0", asset, "portable_win", executable=exe)
    assert (prep.payload / "aIBechos.exe").read_bytes() == b"nuevo"
    assert prep.target == app.resolve() and exe.read_bytes() == b"viejo"


def test_prepare_rechaza_descarga_corrupta(tmp_path, monkeypatch):
    monkeypatch.setattr(self_update, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(self_update.requests, "get", lambda *a, **k: _Resp(b"abc"))
    with pytest.raises(IOError):
        self_update.prepare("v2", {"name": "s.exe", "url": "u", "size": 99}, "installer",
                            executable=tmp_path / "aIBechos.exe")
    with pytest.raises(IOError):
        self_update.prepare("v2", {"name": "s.exe", "url": "u", "size": 3,
                                   "digest": "sha256:00"}, "installer",
                            executable=tmp_path / "aIBechos.exe")


# ── Flujo en el mixin ──

class _View:
    def __init__(self): self.done = []
    def on_update_check_done(self, t): self.done.append(t)


class _Cfg(dict):
    def set(self, k, v): self[k] = v
    def save(self): pass


class _Host(SettingsCoreMixin):
    def __init__(self, auto=True, visible=False):
        self.config_data = _Cfg(auto_update=auto)
        self.settings_view = _View()
        self._update_check_running = False
        self._update_check_manual_waiting = False
        self._update_notified_tags = set()
        self._pending_update = None
        self._auto_update_tag = None
        self._auto_update_ready = None
        self._auto_update_countdown = False
        self._auto_update_failed_tags = set()
        self._upload_running = False
        self.busy = False
        self.visible = visible
        self.dialogs, self.notes, self.later, self.quit = [], [], [], 0

    def _window_is_visible(self): return self.visible
    def _ui_busy(self): return self.busy
    def _show_update_dialog(self, tag, url): self.dialogs.append(tag)
    def _send_notification(self, t, m): self.notes.append(m)
    def _set_status(self, m, c=None): pass
    def after(self, ms, fn): self.later.append((ms, fn))
    def _quit_for_update(self): self.quit += 1


UPD = {"status": "update", "tag": "v9.0.0", "url": "https://x", "assets": ASSETS}


def _run_now(h):
    """Ejecuta lo programado con after(0, ...) (lo del hilo de fondo)."""
    while any(ms == 0 for ms, _ in h.later):
        i = next(i for i, (ms, _) in enumerate(h.later) if ms == 0)
        h.later.pop(i)[1]()


@pytest.fixture
def fake_update(monkeypatch):
    launched = []
    monkeypatch.setattr(self_update, "install_kind", lambda: "installer")
    monkeypatch.setattr(self_update, "prepare",
                        lambda tag, a, k: self_update.PreparedUpdate(tag, k, None, None, None))
    monkeypatch.setattr(self_update, "launch_helper",
                        lambda prep, pid, args: launched.append(args))
    # el hilo de fondo, en línea
    import core.app_settings_core as m

    class _T:
        def __init__(self, target, daemon): self.t = target
        def start(self): self.t()
    monkeypatch.setattr(m.threading, "Thread", _T)
    return launched


def test_en_la_bandeja_instala_sin_preguntar(fake_update):
    h = _Host()
    h._on_update_check_result(UPD, manual=False)
    _run_now(h)
    assert fake_update == [["--minimized"]] and h.quit == 1 and not h.dialogs


def test_espera_a_que_terminen_las_subidas(fake_update):
    h = _Host()
    h._upload_running = True
    h._on_update_check_result(UPD, manual=False)
    _run_now(h)
    assert h.quit == 0 and h.later and h.later[-1][0] == 60_000
    h._upload_running = False
    h.later.pop()[1]()
    assert h.quit == 1


def test_con_la_ventana_abierta_avisa_y_reinicia_a_los_30_s(fake_update):
    h = _Host(visible=True)
    h._on_update_check_result(UPD, manual=False)
    _run_now(h)
    assert h.quit == 0 and len(h.notes) == 1 and h.later[-1][0] == 30_000
    h.later.pop()[1]()
    assert h.quit == 1 and fake_update == [[]]


def test_desactivada_solo_avisa(fake_update):
    h = _Host(auto=False, visible=True)
    h._on_update_check_result(UPD, manual=False)
    assert h.dialogs == ["v9.0.0"] and h.quit == 0


def test_si_falla_la_descarga_avisa_como_antes(fake_update, monkeypatch):
    def _boom(*a): raise IOError("sin red")
    monkeypatch.setattr(self_update, "prepare", _boom)
    h = _Host(visible=True)
    h._on_update_check_result(UPD, manual=False)
    _run_now(h)
    assert h.dialogs == ["v9.0.0"] and h.quit == 0
    h._on_update_check_result(UPD, manual=False)   # no reintenta en bucle
    assert h.dialogs == ["v9.0.0"]


def test_desde_codigo_fuente_solo_avisa(monkeypatch):
    monkeypatch.setattr(self_update, "install_kind", lambda: None)
    h = _Host(visible=True)
    h._on_update_check_result(UPD, manual=False)
    assert h.dialogs == ["v9.0.0"]


def test_no_reintenta_sola_una_version_que_ya_fallo(fake_update):
    """Instalación para todos los usuarios con el UAC rechazado: al volver
    a abrir sigue la versión vieja. Sin esto se reiniciaba en bucle."""
    h = _Host()
    h._on_update_check_result(UPD, manual=False)
    _run_now(h)
    assert h.quit == 1 and h.config_data["auto_update_attempted_tag"] == "v9.0.0"
    h2 = _Host(visible=True)                       # "la app relanzada"
    h2.config_data["auto_update_attempted_tag"] = "v9.0.0"
    h2._on_update_check_result(UPD, manual=False)
    assert h2.quit == 0 and h2.dialogs == ["v9.0.0"]
    h2._on_update_check_result(UPD, manual=True)   # a mano sí reintenta
    _run_now(h2)
    assert h2.later and h2.later[-1][0] == 30_000


def test_cleanup_leftovers(tmp_path, monkeypatch):
    monkeypatch.setattr(self_update, "app_data_dir", lambda: tmp_path)
    (tmp_path / "update" / "nuevo").mkdir(parents=True)
    (tmp_path / "update" / "aIBechos_Setup.exe").write_bytes(b"x" * 10)
    self_update.cleanup_leftovers()
    assert not (tmp_path / "update").exists()
    self_update.cleanup_leftovers()                # sin carpeta: no lanza
