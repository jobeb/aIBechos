"""Escritura atómica de JSON (core/atomic_write.py).

Garantía: el destino o queda con el contenido nuevo entero o conserva el
anterior entero, nunca a medias -- lo que evita que una muerte a mitad de
guardado "borre" upload_history.json / deletion_history.json.
"""

import json

from core.atomic_write import write_json_atomic


def test_crea_el_archivo_con_contenido_valido(tmp_path):
    p = tmp_path / "upload_history.json"
    write_json_atomic(p, [{"a": 1}])
    assert json.loads(p.read_text(encoding="utf-8")) == [{"a": 1}]


def test_sobrescribe_manteniendo_validez(tmp_path):
    p = tmp_path / "h.json"
    p.write_text(json.dumps([{"viejo": True}]), encoding="utf-8")
    write_json_atomic(p, [{"nuevo": 2}])
    assert json.loads(p.read_text(encoding="utf-8")) == [{"nuevo": 2}]


def test_no_deja_temporales_sueltos(tmp_path):
    p = tmp_path / "h.json"
    write_json_atomic(p, [1])
    write_json_atomic(p, [1, 2])
    assert [f.name for f in tmp_path.iterdir()] == ["h.json"]


def test_crea_directorios_padre(tmp_path):
    p = tmp_path / "sub" / "dir" / "h.json"
    write_json_atomic(p, {"x": 1})
    assert json.loads(p.read_text(encoding="utf-8")) == {"x": 1}


def test_un_cuelgue_a_medias_no_toca_el_original(tmp_path):
    """Simula la muerte entre truncar y escribir: con write_text directo el
    original se perdería; con temporal+replace sigue intacto."""
    p = tmp_path / "h.json"
    original = [{"ts": 1}]
    p.write_text(json.dumps(original), encoding="utf-8")
    # El temporal a medio escribir no afecta al destino hasta el replace
    tmp = p.with_name(p.name + ".tmp-99999")
    tmp.write_text('[{"ts":', encoding="utf-8")
    assert json.loads(p.read_text(encoding="utf-8")) == original


def test_dos_hilos_guardando_a_la_vez_no_se_pisan(tmp_path):
    """Antes el temporal era .tmp-PID: dos hilos del mismo proceso escribían
    en el mismo temporal y el resultado podía quedar mezclado o fallar."""
    import threading
    p = tmp_path / "config.json"
    errores = []

    def guardar(n):
        try:
            for i in range(30):
                write_json_atomic(p, {"hilo": n, "i": i, "relleno": "x" * 5000})
        except Exception as e:      # pragma: no cover - es lo que se comprueba
            errores.append(e)

    hilos = [threading.Thread(target=guardar, args=(n,)) for n in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert errores == []
    assert json.loads(p.read_text(encoding="utf-8"))["relleno"] == "x" * 5000
    assert [f.name for f in tmp_path.iterdir()] == ["config.json"]


def test_reintenta_si_el_destino_esta_ocupado_un_instante(tmp_path, monkeypatch):
    """Windows: os.replace falla si el antivirus tiene el archivo abierto."""
    import core.atomic_write as aw
    real = aw.os.replace
    fallos = {"n": 2}

    def replace(src, dst):
        if fallos["n"]:
            fallos["n"] -= 1
            raise PermissionError("ocupado")
        return real(src, dst)

    monkeypatch.setattr(aw.os, "replace", replace)
    monkeypatch.setattr(aw, "_REPLACE_WAIT_S", 0)
    p = tmp_path / "h.json"
    write_json_atomic(p, [1])
    assert json.loads(p.read_text(encoding="utf-8")) == [1]


def test_config_save_es_atomico(tmp_path, monkeypatch):
    """Si escribir falla a medias, config.json conserva lo anterior entero."""
    import config as config_module
    import core.atomic_write as aw
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "config_path", lambda: cfg_file)
    monkeypatch.setattr(config_module, "keyring", type("K", (), {
        "get_password": staticmethod(lambda s, k: None),
        "set_password": staticmethod(lambda s, k, v: None),
        "delete_password": staticmethod(lambda s, k: None)})())
    cfg = config_module.Config()
    cfg.set("language", "es-ES")
    cfg.save()
    antes = cfg_file.read_text(encoding="utf-8")

    def dump_roto(data, f, **kw):
        f.write('{"a medias": ')
        raise OSError("disco lleno")

    monkeypatch.setattr(aw.json, "dump", dump_roto)
    cfg.set("language", "en-US")
    try:
        cfg.save()
    except OSError:
        pass
    assert cfg_file.read_text(encoding="utf-8") == antes
    assert [f.name for f in tmp_path.iterdir()] == ["config.json"]
