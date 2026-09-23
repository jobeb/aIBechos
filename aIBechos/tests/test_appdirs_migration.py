"""Traer los datos de cuando la aplicación se llamaba de otra forma.

Es la migración más delicada del cambio de nombre: de la carpeta de datos
cuelga TODO lo que un usuario tiene (su configuración, sus estadísticas, sus
cachés y sus logs). Si esto falla, la aplicación arranca como recién instalada
y el usuario da por perdido lo suyo.
"""

import core.appdirs as appdirs
from core.appdirs import APP_NAME, LEGACY_APP_NAME, _MIGRATION_MARKER


def _preparar(tmp_path, monkeypatch, ficheros=None):
    """Aísla la carpeta de datos en tmp_path y siembra una instalación
    antigua con *ficheros*."""
    monkeypatch.setattr(appdirs, "_base_data_dir", lambda: tmp_path)
    monkeypatch.setattr(appdirs, "_migration_checked", False)
    antigua = tmp_path / LEGACY_APP_NAME
    antigua.mkdir(parents=True, exist_ok=True)
    for nombre, contenido in (ficheros or {}).items():
        (antigua / nombre).write_text(contenido, encoding="utf-8")
    return antigua, tmp_path / APP_NAME


def test_se_trae_los_datos_de_la_instalacion_anterior(tmp_path, monkeypatch):
    antigua, nueva = _preparar(tmp_path, monkeypatch, {
        "config.json": '{"ftp_host": "midominio.net"}',
        "favorites.json": "[1, 2, 3]",
        "app.log": "una linea de log\n",
    })
    devuelta = appdirs.app_data_dir()

    assert devuelta == nueva
    assert (nueva / "config.json").read_text(encoding="utf-8") == '{"ftp_host": "midominio.net"}'
    assert (nueva / "favorites.json").read_text(encoding="utf-8") == "[1, 2, 3]"
    assert (nueva / "app.log").read_text(encoding="utf-8") == "una linea de log\n"


def test_la_carpeta_antigua_no_se_toca(tmp_path, monkeypatch):
    """Se copia, nunca se mueve: volver a la versión anterior debe seguir
    funcionando, y nada se pierde si la migración sale mal."""
    antigua, _ = _preparar(tmp_path, monkeypatch, {"config.json": "{}"})
    appdirs.app_data_dir()
    assert (antigua / "config.json").exists()


def test_no_se_repite_en_cada_arranque(tmp_path, monkeypatch):
    """El marcador tiene que cortar de verdad la copia: si no, un archivo
    borrado a propósito reaparecería en el siguiente arranque."""
    antigua, nueva = _preparar(tmp_path, monkeypatch, {"config.json": "{}"})
    appdirs.app_data_dir()
    assert (nueva / _MIGRATION_MARKER).exists()

    (nueva / "config.json").unlink()
    monkeypatch.setattr(appdirs, "_migration_checked", False)   # simula otro arranque
    appdirs.app_data_dir()
    assert not (nueva / "config.json").exists()


def test_no_pisa_lo_que_ya_hay_en_la_carpeta_nueva(tmp_path, monkeypatch):
    """Si una migración anterior se cortó a medias, la siguiente termina lo que
    faltaba SIN machacar lo ya copiado (que puede haber cambiado desde
    entonces)."""
    antigua, nueva = _preparar(tmp_path, monkeypatch, {
        "config.json": "viejo", "favorites.json": "viejo"})
    nueva.mkdir(parents=True, exist_ok=True)
    (nueva / "config.json").write_text("nuevo y bueno", encoding="utf-8")

    appdirs.app_data_dir()

    assert (nueva / "config.json").read_text(encoding="utf-8") == "nuevo y bueno"
    assert (nueva / "favorites.json").read_text(encoding="utf-8") == "viejo"


def test_una_instalacion_nueva_no_falla_por_no_haber_nada_que_traer(tmp_path, monkeypatch):
    monkeypatch.setattr(appdirs, "_base_data_dir", lambda: tmp_path)
    monkeypatch.setattr(appdirs, "_migration_checked", False)
    nueva = appdirs.app_data_dir()
    assert nueva.is_dir()
    assert (nueva / _MIGRATION_MARKER).exists()


def test_no_deja_archivos_a_medio_copiar(tmp_path, monkeypatch):
    """Cada archivo se pone en su sitio de un golpe, así que nunca debe quedar
    visible el temporal de la copia."""
    _preparar(tmp_path, monkeypatch, {"config.json": "{}", "app.log": "x"})
    nueva = appdirs.app_data_dir()
    assert [p.name for p in nueva.glob("*.migrando")] == []


def test_un_fallo_copiando_no_impide_arrancar(tmp_path, monkeypatch):
    """Que la migración reviente (permisos, disco lleno) jamás puede tumbar la
    aplicación: se reintentará en el siguiente lanzamiento."""
    _preparar(tmp_path, monkeypatch, {"config.json": "{}"})

    def _copia_que_falla(*a, **kw):
        raise PermissionError("acceso denegado")

    monkeypatch.setattr(appdirs.shutil, "copy2", _copia_que_falla)
    nueva = appdirs.app_data_dir()          # no debe lanzar

    assert nueva.is_dir()
    # Y no se marca como hecha, para poder reintentarlo.
    assert not (nueva / _MIGRATION_MARKER).exists()
