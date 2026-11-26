"""Abrir la app ya abierta restaura su ventana en vez de avisar
(gui_qt/activation.py): un segundo proceso real le pide mostrarse."""

import os
import subprocess
import sys
import uuid

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from gui_qt.activation import request_show, server_name, start_server  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _second_instance(name):
    code = ("import sys; sys.path.insert(0, %r); from gui_qt.activation import request_show;"
            "sys.exit(0 if request_show(%r) else 3)" % (ROOT, name))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.Popen([sys.executable, "-c", code], env=env)


def test_segunda_instancia_restaura_la_ventana(qtbot):
    name = f"aIBechos-test-{uuid.uuid4().hex}"
    shown = []
    server = start_server(lambda: shown.append(1), name)
    assert server is not None
    proc = _second_instance(name)
    qtbot.waitUntil(lambda: bool(shown), timeout=10000)
    assert proc.wait(10) == 0
    server.close()


def test_sin_instancia_que_escuche_devuelve_false(qtbot):
    assert request_show(f"aIBechos-nadie-{uuid.uuid4().hex}", timeout_ms=300) is False


def test_nombre_por_usuario_y_sin_caracteres_raros():
    n = server_name()
    assert n.startswith("aIBechos-activar-")
    assert all(c.isalnum() or c in "_.-" for c in n)


def test_main_no_saca_de_la_bandeja_en_arranque_automatico():
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    i = src.index("if not acquire():")
    block = src[i:src.index("from gui_qt.app import run")]
    assert block.index('"--minimized"') < block.index("request_show"), \
        "con --minimized no se debe restaurar la ventana de la otra instancia"
    assert "warn_already_running" in block, "sin respuesta (versión anterior) se avisa como antes"
