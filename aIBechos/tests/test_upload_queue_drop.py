"""Cola de subida manual: diálogos sin plazo, quitar archivos de una tanda
en marcha sin pararla ni desordenarla, y "Saltar" por archivo.

Bugs reales:
- Diálogos con plazo de 30 s: sin contestar se daban por "Omitir" y el
  siguiente worker abría otra ventana encima (varias "El archivo ya
  existe" apiladas).
- "Limpiar seleccionados" activaba _upload_cancel y paraba toda la tanda.
- Quitar una fila hacía _upload_queue.remove() mientras el worker la
  recorría por índice: el archivo siguiente no se subía nunca.
"""

import threading
import time
from types import SimpleNamespace

from core.app_files_upload import FilesUploadMixin


class _Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


class _Conn:
    def connect(self, *a):
        return True, ""

    def is_connected(self):
        return True

    def disconnect(self):
        pass


class _Host(FilesUploadMixin):
    def __init__(self, entries=(), parallel=1):
        self.config_data = _Cfg(ftp_parallel=parallel, ftp_retries=0, ftp_host="h")
        self.files = list(entries)
        self._upload_queue = list(entries)
        self._upload_running = True
        self._upload_cancel = threading.Event()
        self._upload_skip_of = {}
        self._upload_batch_size = len(entries)
        self.uploaded = []
        self.on_upload = None

    # ganchos de interfaz: aquí no hay GUI
    def after(self, _ms, fn):
        fn()

    def _set_status(self, *a):
        pass

    def _ftp_row_set(self, *a):
        pass

    def _update_row(self, *a):
        pass

    def _restore_ftp_row_buttons(self):
        pass

    def _refresh_ftp_columns(self):
        pass

    def _reserve_manual_entries(self, entries):
        pass

    def _release_manual_entries(self, entries):
        pass

    def _new_ftp_client(self):
        return _Conn()

    def _upload_entry_with(self, entry, ftp_conn, speed_kbs, skip_ev):
        if self.on_upload:
            self.on_upload(entry)
        self.uploaded.append(entry.name)
        entry.status = "subido"
        return True, ""


def _entry(name):
    return SimpleNamespace(name=name, status="en_cola", media_info=object(),
                           ftp_progress=0.0, ftp_speed=0.0, ftp_status="")


# ── _ask_gui ──

def _gui_host():
    h = _Host()
    h.after = lambda _ms, fn: threading.Thread(target=fn, daemon=True).start()
    return h


def test_ask_gui_devuelve_el_resultado():
    h = _gui_host()
    assert h._ask_gui(lambda: SimpleNamespace(result="all"), "x") == "all"


def test_ask_gui_fabrica_que_falla_no_cuelga():
    h = _gui_host()

    def boom():
        raise RuntimeError("sin ventana")
    assert h._ask_gui(boom, "x") is None


def test_ask_gui_espera_sin_plazo_hasta_que_se_conteste():
    h = _gui_host()
    contestar = threading.Event()

    def dialogo():
        contestar.wait()
        return SimpleNamespace(result="overwrite")
    out = []
    t = threading.Thread(target=lambda: out.append(h._ask_gui(dialogo, "x")), daemon=True)
    t.start()
    t.join(1.5)
    assert t.is_alive() and not out, "no debe darse por contestado sin respuesta"
    contestar.set()
    t.join(3)
    assert out == ["overwrite"]


def test_ask_gui_cancelar_suelta_la_espera():
    h = _gui_host()
    nunca = threading.Event()

    def dialogo():
        nunca.wait(10)
        return SimpleNamespace(result="overwrite")
    threading.Timer(0.2, h._upload_cancel.set).start()
    t0 = time.monotonic()
    assert h._ask_gui(dialogo, "x", h._upload_cancel) is None
    assert time.monotonic() - t0 < 3
    nunca.set()


# ── quitar de la tanda ──

def test_quitar_uno_en_espera_lo_salta_y_sigue_con_los_demas():
    a, b, c = _entry("a"), _entry("b"), _entry("c")
    h = _Host([a, b, c])
    h.on_upload = lambda e: h._drop_from_upload([b]) if e is a else None
    h._queue_worker()
    assert h.uploaded == ["a", "c"]
    assert not h._upload_cancel.is_set()
    assert b.status != "en_cola"   # no se queda como fila muerta


def test_quitar_uno_ya_pasado_no_salta_el_siguiente():
    a, b, c = _entry("a"), _entry("b"), _entry("c")
    h = _Host([a, b, c])
    h.on_upload = lambda e: h._drop_from_upload([a]) if e is b else None
    h._queue_worker()
    assert h.uploaded == ["a", "b", "c"]


def test_saltar_un_archivo_no_toca_el_evento_de_otro():
    a, b = _entry("a"), _entry("b")
    h = _Host([a, b])
    ev_a, ev_b = threading.Event(), threading.Event()
    h._upload_skip_of = {id(a): ev_a, id(b): ev_b}
    h._drop_from_upload([a])
    assert ev_a.is_set() and not ev_b.is_set()
    assert a._upload_dropped and h._upload_queue == [a, b]


def test_pendientes_solo_los_de_la_tanda_sin_terminar():
    a, b, c, d = _entry("a"), _entry("b"), _entry("c"), _entry("d")
    b.status = "subido"
    h = _Host([a, b, c])
    h._drop_from_upload([c])
    assert h._upload_pending_among([a, b, c, d]) == [a]
    h._upload_running = False
    assert h._upload_pending_among([a]) == []


def test_volver_a_subir_un_quitado_le_quita_la_marca():
    a = _entry("a")
    h = _Host([a])
    h._drop_from_upload([a])
    a.status = "listo"
    h._enqueue_or_start([a])
    assert a._upload_dropped is False and a.status == "en_cola"
