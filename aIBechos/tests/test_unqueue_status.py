"""Omitir una subida (X u Omitir) nunca deja la fila en un estado
transitorio de cola: "en_cola"/"esperando_confirmacion" la dejaban muerta
(ni el ▲ ni Subir la aceptan, y al reiniciar seguía igual -- real:
Andrómeda 2x05 tras cerrar el diálogo de sustituir con la X)."""

from types import SimpleNamespace

from core.app_files_core import FilesCoreMixin as App


def _entry(media_info=True):
    return SimpleNamespace(media_info=object() if media_info else None)


def test_transitorios_vuelven_a_listo():
    e = _entry()
    assert App._unqueue_status(e, "en_cola") == "listo"
    assert App._unqueue_status(e, "esperando_confirmacion") == "listo"
    assert App._unqueue_status(e, "subiendo") == "listo"


def test_sin_identificar_vuelve_a_pendiente():
    e = _entry(media_info=False)
    assert App._unqueue_status(e, "en_cola") == "pendiente"


def test_resto_de_estados_se_respetan():
    e = _entry()
    for st in ("listo", "renombrado", "error", "omitido", "subido", "pendiente",
               "buscando", "auto"):
        assert App._unqueue_status(e, st) == st
