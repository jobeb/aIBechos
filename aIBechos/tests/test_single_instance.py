import importlib

import core.single_instance as si


def _reload_isolated(monkeypatch, tmp_path):
    """Recarga el módulo con app_data_dir() apuntando a una carpeta
    temporal, para no tocar el bloqueo real de una instancia en marcha."""
    monkeypatch.setattr(si, "app_data_dir", lambda: tmp_path)
    si._lock_file = None
    return si


def test_acquire_succeeds_when_no_other_instance(tmp_path, monkeypatch):
    mod = _reload_isolated(monkeypatch, tmp_path)
    assert mod.acquire() is True
    assert mod._lock_file is not None
    mod._lock_file.close()


def test_acquire_fails_while_lock_already_held_by_this_process(tmp_path, monkeypatch):
    mod = _reload_isolated(monkeypatch, tmp_path)
    assert mod.acquire() is True   # primera "instancia" toma el bloqueo

    # Una segunda llamada (simulando un segundo proceso intentando arrancar)
    # no debe conseguir el bloqueo mientras el primero lo tenga abierto.
    # Se simula reabriendo el fichero de bloqueo con un descriptor nuevo,
    # como haría un proceso independiente.
    import io
    first_handle = mod._lock_file
    mod._lock_file = None   # simular que "otro proceso" no conoce el handle anterior
    result = mod.acquire()
    assert result is False, "no deberia poder tomar el bloqueo si otro descriptor ya lo tiene"

    first_handle.close()   # liberar el bloqueo del "primer proceso"


def test_acquire_succeeds_again_after_lock_released(tmp_path, monkeypatch):
    mod = _reload_isolated(monkeypatch, tmp_path)
    assert mod.acquire() is True
    mod._lock_file.close()
    mod._lock_file = None

    # Tras liberar (equivalente a que el primer proceso termine, de la
    # forma que sea), una nueva instancia SI debe poder tomar el bloqueo.
    assert mod.acquire() is True
    mod._lock_file.close()
