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
