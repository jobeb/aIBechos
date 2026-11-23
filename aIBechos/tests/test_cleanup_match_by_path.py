"""Liberar espacio: emparejar elementos del servidor con Jellyfin por la
RUTA del archivo antes que por nombre. Casos reales: "Robots (2023)" se
llevaba "Robots" (2005), "Enola Holmes (2020)" se llevaba "Enola Holmes 2"
y "Como entrenar a tu dragon (2025)" se llevaba "Cómo entrenar a tu
dragón 2" (2014)."""
from types import SimpleNamespace

import core.media_server_refresh as msr
from core.app_cleanup_core import CleanupCoreMixin
from core.cleanup_candidates import match_usage_by_path

JF = "/home/administrador"


def test_suelto_casa_por_ruta_sin_extension():
    entries = {"a": {"path": f"{JF}/datos/peliculas/Robots (2023).mkv"},
               "b": {"path": f"{JF}/datos/pelispeques/Robots (2005).mkv"}}
    cands = [("/datos/peliculas/Robots (2023)", True), ("/datos/pelispeques/Robots (2005)", True)]
    assert match_usage_by_path(cands, entries) == {
        "/datos/peliculas/Robots (2023)": ["a"], "/datos/pelispeques/Robots (2005)": ["b"]}


def test_carpeta_casa_con_archivo_dentro_o_la_propia_carpeta():
    entries = {"m": {"path": f"{JF}/datos/peliculas/Peli (2020)/Peli (2020).mkv"},
               "s": {"path": f"{JF}/datos2/series/Silo"}}
    cands = [("/datos/peliculas/Peli (2020)", False), ("/datos2/series/Silo", False),
             ("/datos2/series/Sil", False)]
    assert match_usage_by_path(cands, entries) == {
        "/datos/peliculas/Peli (2020)": ["m"], "/datos2/series/Silo": ["s"]}


def test_sin_ruta_o_prefijo_parecido_no_casa():
    entries = {"x": {"path": ""}, "y": {"path": f"{JF}/datos/peliculas/Robots (2023) extendida.mkv"}}
    assert match_usage_by_path([("/datos/peliculas/Robots (2023)", True)], entries) == {}


def test_duplicado_en_jellyfin_devuelve_las_dos():
    entries = {"a": {"path": f"{JF}/datos/peliculas/X (2020).mkv"},
               "b": {"path": f"{JF}/datos/peliculas/X (2020).avi"}}
    assert match_usage_by_path([("/datos/peliculas/X (2020)", True)], entries) == {
        "/datos/peliculas/X (2020)": ["a", "b"]}


class _FakeFTP:
    def __init__(self, roots):
        self.roots = roots

    def connect(self, *a):
        return True, ""

    def disconnect(self):
        pass

    def list_tree_recursive(self, root):
        return None

    def list_files_with_sizes(self, root):
        return [(n, 1000) for n in self.roots.get(root, [])]

    def list_dirs(self, root):
        return []


def _jf(name, year, tmdb, path):
    return {"name": name, "tmdb_id": tmdb, "media_type": "movie", "year": year,
            "fully_watched": False, "play_count": 0, "last_played": None,
            "date_added": None, "size_bytes": 1000, "path": path}


def test_analisis_empareja_por_ruta_no_cruza_titulos(monkeypatch):
    stats = {
        "jf1": _jf("Cómo entrenar a tu dragón", "2025", 1087192, f"{JF}/datos/peliculas/Como entrenar a tu dragon (2025).mkv"),
        "jf2": _jf("Cómo entrenar a tu dragón 2", "2014", 82702, f"{JF}/datos/pelispeques/Como entrenar a tu dragon 2 (2014).avi"),
        "jf3": _jf("Enola Holmes", "2020", 497582, f"{JF}/datos/peliculas/Enola Holmes (2020).mkv"),
        "jf4": _jf("Enola Holmes 2", "2022", 829280, f"{JF}/datos/peliculas/Enola Holmes 2 (2022).avi"),
        "jf5": _jf("Robots", "2005", 9928, f"{JF}/datos/pelispeques/Robots (2005).mkv"),
        "jf6": _jf("Robots", "2023", 723347, f"{JF}/datos/peliculas/Robots (2023).mkv"),
        "jf7": _jf("Sin Ruta", "2001", 555, ""),
    }
    monkeypatch.setattr(msr, "get_jellyfin_usage_stats", lambda *a, **k: stats)
    ftp = _FakeFTP({
        "/datos/peliculas": ["Como entrenar a tu dragon (2025).mkv", "Enola Holmes (2020).mkv",
                             "Enola Holmes 2 (2022).avi", "Robots (2023).mkv", "Sin Ruta (2001).mkv"],
        "/datos/pelispeques": ["Como entrenar a tu dragon 2 (2014).avi", "Robots (2005).mkv"],
    })
    host = SimpleNamespace(
        config_data={"jellyfin_enabled": True, "plex_enabled": False,
                     "ftp_categories": {"movie": [{"root": "/datos/peliculas", "name": "Películas"},
                                                  {"root": "/datos/pelispeques", "name": "Pelis peques"}]}},
        _new_ftp_client=lambda: ftp, _cleanup_trees={})
    items = CleanupCoreMixin._scan_cleanup_candidates(host)
    got = {it.ftp_path: (it.tmdb_id, it.year) for it in items}
    assert got == {
        "/datos/peliculas/Como entrenar a tu dragon (2025)": (1087192, "2025"),
        "/datos/pelispeques/Como entrenar a tu dragon 2 (2014)": (82702, "2014"),
        "/datos/peliculas/Enola Holmes (2020)": (497582, "2020"),
        "/datos/peliculas/Enola Holmes 2 (2022)": (829280, "2022"),
        "/datos/peliculas/Robots (2023)": (723347, "2023"),
        "/datos/pelispeques/Robots (2005)": (9928, "2005"),
        # Sin ruta en Jellyfin: sigue casando por nombre, como antes.
        "/datos/peliculas/Sin Ruta (2001)": (555, "2001"),
    }
