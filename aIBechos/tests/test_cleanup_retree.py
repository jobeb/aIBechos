"""La expansión de una serie en Liberar espacio debe ver los capítulos
tras pulsar ↻, venga el árbol con claves absolutas o relativas (LIST -R
devuelve "./x" en algunos servidores y eso nunca casa por prefijo con
ftp_path: ver core/app_cleanup_core.py::_store_cleanup_retree)."""

from core.app_cleanup_core import _store_cleanup_retree


def test_claves_absolutas_pueblan_arbol_y_cache():
    trees, cache = {}, {}
    _store_cleanup_retree(trees, cache, "/datos2/series/Fargo",
                          {"/datos2/series/Fargo": [("Fargo 1x01.mkv", 100)],
                           "/datos2/series/Fargo/Temporada 01": [("Fargo 1x02.mkv", 200)]},
                          now=1000.0)
    assert trees["/datos2/series/Fargo/Temporada 01"] == [("Fargo 1x02.mkv", 200)]
    assert cache["/datos2/series/Fargo"] == (
        1000.0, [("Fargo 1x01.mkv", 100, "/datos2/series/Fargo"),
                 ("Fargo 1x02.mkv", 200, "/datos2/series/Fargo/Temporada 01")])


def test_claves_relativas_igual_rellenan_la_cache():
    """El caso real del fallo: con claves "./x" el lookup por prefijo no
    casa, pero la caché plana por ftp_path exacto sí salva la expansión."""
    trees, cache = {}, {}
    _store_cleanup_retree(trees, cache, "/datos2/series/Fargo",
                          {".": [("Fargo 1x01.mkv", 100)],
                           "./Temporada 01": [("Fargo 1x02.mkv", 200)]},
                          now=1000.0)
    assert cache["/datos2/series/Fargo"][1] == [
        ("Fargo 1x01.mkv", 100, "."),
        ("Fargo 1x02.mkv", 200, "./Temporada 01")]


def test_tamanos_rotos_no_tumban_nada():
    # None -> 0 (convención existente, ver _fetch_cleanup_ep_files);
    # lo que no es número ni vacío se salta sin tumbar la carpeta.
    trees, cache = {}, {}
    _store_cleanup_retree(trees, cache, "/s/X",
                          {"/s/X": [("a.mkv", "no-num"), ("b.mkv", None), ("c.mkv", 10)]},
                          now=5.0)
    assert trees["/s/X"] == [("b.mkv", 0), ("c.mkv", 10)]
    assert cache["/s/X"] == (5.0, [("b.mkv", 0, "/s/X"), ("c.mkv", 10, "/s/X")])


def test_arbol_vacio_o_none_no_rompe():
    trees, cache = {"/s/X": [("a.mkv", 1)]}, {}
    _store_cleanup_retree(trees, cache, "/s/X", {}, now=5.0)
    _store_cleanup_retree(trees, cache, "/s/X", None, now=5.0)
    assert trees == {"/s/X": [("a.mkv", 1)]}
    assert cache["/s/X"] == (5.0, [])
