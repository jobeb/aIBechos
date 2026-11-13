"""Historial de actividad compartido: se guarda por antigüedad (90 días),
no "últimos 500" (con ~150 subidas en un día el gráfico de 30 días empezaba
a mitad de mes), y la copia local repone lo que recorte un cliente viejo."""
import pytest

from core import activity_log as al

NOW = 1_800_000_000.0
DAY = 86400


@pytest.fixture(autouse=True)
def _tmp_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(al, "app_data_dir", lambda: tmp_path)


def _up(ts, name="a.mkv", person="Jose"):
    return {"kind": "subida", "ts": ts, "person": person, "filename": name, "size": 1}


def test_mas_de_500_registros_recientes_no_se_recortan():
    entries = [_up(NOW - i * 60, f"{i}.mkv") for i in range(2000)]
    assert len(al.prune(al.merge(entries), NOW)) == 2000


def test_caduca_por_antiguedad_y_respeta_el_tope():
    old, fresh = _up(NOW - 91 * DAY, "viejo"), _up(NOW - 89 * DAY, "nuevo")
    assert [e["filename"] for e in al.prune([old, fresh], NOW)] == ["nuevo"]
    many = [_up(NOW - i, f"{i}") for i in range(al.MAX_ENTRIES + 50)]
    assert len(al.prune(al.merge(many), NOW)) == al.MAX_ENTRIES


def test_merge_sin_duplicados_y_ordenado():
    a, b, c = _up(NOW - 30), _up(NOW - 20, "b"), _up(NOW - 10, "c")
    out = al.merge([c, a], [a, b], [None, "x"])
    assert out == [a, b, c]


def test_copia_local_repone_lo_que_recorta_un_cliente_viejo():
    full = [_up(NOW - i * 60, f"{i}.mkv") for i in range(800)]
    al.reconcile(full, NOW)                        # cliente nuevo ve las 800
    trimmed = al.merge(full)[-500:]                # un cliente viejo recorta a 500
    assert len(al.reconcile(trimmed, NOW)) == 800  # el nuevo las repone
