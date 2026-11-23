"""Adelgazar películas en Liberar espacio, también las sueltas (sin carpeta
propia: "Peli (2020).mkv" con su .nfo y carteles en la raíz de la
categoría), y el emparejamiento de la ligera con el gordo."""
from types import SimpleNamespace

from core import slim_pending
from core.app_cleanup_core import CleanupCoreMixin as Cleanup
from core.cleanup_candidates import CleanupItem, group_loose_files_by_name

GB = 1024 ** 3


def _loose_item(**kw):
    files = [("La Odisea (2026).mkv", 9 * GB), ("La Odisea (2026).nfo", 500),
             ("La Odisea (2026)-poster.jpg", 100_000)]
    g = group_loose_files_by_name(files)["La Odisea (2026)"]
    base = dict(tmdb_id=1234, name="La Odisea (2026)", media_type="movie",
                ftp_path="/datos/peliculas/La Odisea (2026)", category_name="Películas",
                size_bytes=g["size_bytes"],
                loose_file_paths=[f"/datos/peliculas/{n}" for n in g["file_names"]],
                loose_files=g["files"])
    base.update(kw)
    return CleanupItem(**base)


def _host():
    return SimpleNamespace(
        config_data={"slim_desired_sizes": {}},
        _cleanup_trees={}, _cleanup_ep_cache={}, _cleanup_ep_cache_ttl=600,
        _cleanup_slim_ratio=lambda: 2.0,
        _cleanup_desired_bytes=lambda name: None)


def test_grupo_suelto_trae_tamano_por_archivo():
    g = group_loose_files_by_name([("A (2020).mkv", 5), ("A (2020).nfo", 1)])["A (2020)"]
    assert sorted(map(tuple, g["files"])) == [("A (2020).mkv", 5), ("A (2020).nfo", 1)]


def test_pelicula_suelta_solo_su_video_y_en_la_raiz():
    files = Cleanup._cleanup_ep_files(_host(), _loose_item())
    assert files == [("La Odisea (2026).mkv", 9 * GB, "/datos/peliculas")]
    # Análisis antiguo (sin tamaños por archivo): no se puede adelgazar todavía.
    assert Cleanup._cleanup_ep_files(_host(), _loose_item(loose_files=None)) is None


def test_pelicula_suelta_gorda_es_adelgazable_con_su_carpeta_real():
    h = _host()
    it = _loose_item()
    cands, target = Cleanup._slim_candidates_for_item(h, it, Cleanup._cleanup_ep_files(h, it))
    assert [d["name"] for d in cands] == ["La Odisea (2026).mkv"]
    assert cands[0]["folder"] == "/datos/peliculas", "no la ruta del grupo, que no es carpeta"
    assert target == 2 * GB   # sin resolución en el nombre: techo de 2 GB
    # Una de 3 GB no llega a 2× el objetivo.
    ligera = _loose_item(loose_files=[["La Odisea (2026).mkv", 3 * GB]])
    cands, _t = Cleanup._slim_candidates_for_item(h, ligera, Cleanup._cleanup_ep_files(h, ligera))
    assert cands == []


def test_solo_adelgazables_y_vista_plana_incluyen_peliculas_sueltas():
    h = _host()
    h._cleanup_trees = {"/datos/series/X": [("X 1x01.mkv", GB)]}   # hay análisis
    h._cleanup_files_by_owner = lambda items: Cleanup._cleanup_files_by_owner(h, items)
    h._cleanup_ep_files = lambda it: Cleanup._cleanup_ep_files(h, it)
    h._slim_candidates_for_item = lambda it, f: Cleanup._slim_candidates_for_item(h, it, f)
    gorda, ligera = _loose_item(), _loose_item(loose_files=[["La Odisea (2026).mkv", GB]])
    assert Cleanup._filter_slim_only(h, [gorda, ligera]) == [gorda]
    rows = Cleanup._build_cleanup_flat_rows(h, [gorda])
    assert [r["name"] for r in rows] == ["La Odisea (2026).mkv"]
    assert rows[0]["folder"] == "/datos/peliculas"


def test_registro_del_gordo_sin_anio_en_la_clave_y_con_tmdb(monkeypatch):
    got = []
    monkeypatch.setattr(slim_pending, "record", lambda e: got.append(e) or e)
    h = SimpleNamespace(config_data={"app_user_name": "Jose"})
    Cleanup._record_slim_replacement(h, _loose_item(), "La Odisea (2026).mkv", 9 * GB,
                                     "/datos/peliculas", "La Odisea 2026")
    e = got[0]
    assert (e["key_norm"], e["year"], e["tmdb_id"]) == ("odisea", "2026", 1234)
    assert e["heavy_remote_file"] == "/datos/peliculas/La Odisea (2026).mkv"
    # La ligera que llega (título TMDB) casa con él; por TMDB aunque el
    # nombre del servidor sea otro.
    assert slim_pending.find_match([e], "movie", "La Odisea", year="2026",
                                   local_size=3 * GB, tmdb_id=1234) is e
    otro = dict(e, key_norm="the beast")
    assert slim_pending.find_match([otro], "movie", "Beast (La bestia)", year="2026",
                                   local_size=3 * GB, tmdb_id=1234) is otro
    assert slim_pending.find_match([e], "movie", "La Odisea", year="2026",
                                   local_size=3 * GB, tmdb_id=999) is None, "otro TMDB no casa"


def test_pelicula_suelta_lleva_el_boton_en_su_fila_sin_desplegar():
    """🪶 junto a Jellyfin y borrar, en la fila de la película: desplegar
    una película (un solo archivo) no aporta nada."""
    from gui_qt.cleanup.tab import CleanupTab, Node
    h = _host()
    h._cleanup_ep_files = lambda it: Cleanup._cleanup_ep_files(h, it)
    h._slim_candidates_for_item = lambda it, f: Cleanup._slim_candidates_for_item(h, it, f)
    h._cleanup_movie_slim_candidate = lambda it: Cleanup._cleanup_movie_slim_candidate(h, it)
    h._is_favorite = h._is_reserved = lambda *a: False
    h._cleanup_deleting_paths, h._cleanup_rescanning = set(), set()
    tab = SimpleNamespace(host=h)
    gorda = _loose_item(source="jellyfin", server_id="x")
    assert [a.id for a in CleanupTab.actions_for(tab, Node("item", gorda))] == ["open_server", "slim_one", "delete"]
    ligera = _loose_item(loose_files=[["La Odisea (2026).mkv", GB]])
    assert [a.id for a in CleanupTab.actions_for(tab, Node("item", ligera))] == ["delete"]
    vieja = _loose_item(loose_files=None)   # análisis sin tamaños por archivo
    assert [a.id for a in CleanupTab.actions_for(tab, Node("item", vieja))] == ["delete"]
    cand = Cleanup._cleanup_movie_slim_candidate(h, gorda)
    assert (cand["name"], cand["folder"]) == ("La Odisea (2026).mkv", "/datos/peliculas")


def test_pelicula_suelta_no_se_despliega():
    from gui_qt.cleanup.tab import CleanupModel, Node
    h = _host()
    h._cleanup_expanded, h._cleanup_expanded_seasons = set(), set()
    h._render_cleanup_page = lambda: None
    Cleanup._toggle_cleanup_expand(h, _loose_item())
    assert h._cleanup_expanded == set()
    model = CleanupModel(SimpleNamespace(host=h))
    model.roots = [Node("item", _loose_item())]
    assert model.hasChildren(model.index(0, 0)) is False


def test_filtros_sin_arbol_sftp_siguen_viendo_las_peliculas_sueltas():
    """SFTP no tiene LIST -R: _cleanup_trees queda vacío tras analizar, y
    antes «Solo adelgazables»/«Por capítulo/película» decían siempre que
    faltaba un análisis reciente."""
    h = _host()
    h._set_status = lambda *a: None
    h._cleanup_files_by_owner = lambda items: Cleanup._cleanup_files_by_owner(h, items)
    h._cleanup_ep_files = lambda it: Cleanup._cleanup_ep_files(h, it)
    h._slim_candidates_for_item = lambda it, f: Cleanup._slim_candidates_for_item(h, it, f)
    gorda, ligera = _loose_item(), _loose_item(loose_files=[["La Odisea (2026).mkv", GB]])
    assert Cleanup._filter_slim_only(h, [gorda, ligera]) == [gorda]
    assert [r["name"] for r in Cleanup._build_cleanup_flat_rows(h, [gorda])] == ["La Odisea (2026).mkv"]


# ── Al terminar un adelgazamiento, Liberar espacio se pone al día solo ──
# (antes el gancho estaba vacío desde la migración a Qt y la película
# seguía saliendo como adelgazable hasta volver a analizar).

def test_reemplazo_actualiza_pelicula_suelta_sin_red():
    from core.cleanup_candidates import apply_slim_replacement
    it = _loose_item()
    before = it.size_bytes
    got = apply_slim_replacement([it], {}, {}, "/datos/peliculas/La Odisea (2026).mkv",
                                 "/datos/peliculas/La Odisea (2026).mkv", 2 * GB)
    assert got is it
    assert ["La Odisea (2026).mkv", 2 * GB] in it.loose_files
    assert it.size_bytes == before - 7 * GB
    h = _host()
    assert Cleanup._slim_candidates_for_item(h, it, Cleanup._cleanup_ep_files(h, it))[0] == [], \
        "ya no es adelgazable"


def test_reemplazo_actualiza_capitulo_en_cache_de_la_serie():
    from core.cleanup_candidates import apply_slim_replacement
    serie = CleanupItem(tmdb_id=9, name="X", media_type="tv", ftp_path="/datos2/series/X",
                        category_name="Series", size_bytes=6 * GB)
    t1 = "/datos2/series/X/Temporada 01"
    cache = {serie.ftp_path: (1.0, [("X 1x01.mkv", 5 * GB, t1), ("X 1x02.mkv", GB, t1)])}
    trees = {t1: [("X 1x01.mkv", 5 * GB), ("X 1x02.mkv", GB)]}
    otra = _loose_item()
    got = apply_slim_replacement([otra, serie], trees, cache, f"{t1}/X 1x01.mkv",
                                 f"{t1}/X 1x01 ligera.mkv", GB)
    assert got is serie
    assert cache[serie.ftp_path][1][0] == ("X 1x01 ligera.mkv", GB, t1)
    assert trees[t1][0] == ("X 1x01 ligera.mkv", GB)
    assert serie.size_bytes == 2 * GB
    assert apply_slim_replacement([serie], {}, {}, "/otra/cosa.mkv", "", 1) is None


def test_evento_slim_replaced_refresca_liberar_espacio(monkeypatch):
    import core.cleanup_candidates_cache as ccc
    saved = []
    monkeypatch.setattr(ccc, "save_cache", lambda items, ts, by="": saved.append(len(items)))
    it = _loose_item()
    h = _host()
    h._cleanup_raw_items = [it]
    h._cleanup_last_scan_ts, h._cleanup_scanned_by = 1.0, "Jose"
    applied = []
    h._apply_cleanup_filters = lambda preserve_page=False: applied.append(preserve_page)
    Cleanup._refresh_cleanup_item_after_replace(
        h, "/datos/peliculas/La Odisea (2026).mkv", "/datos/peliculas/La Odisea (2026).mkv",
        2 * GB, 7 * GB)
    assert ["La Odisea (2026).mkv", 2 * GB] in it.loose_files
    assert saved == [1] and applied == [True]


def test_qtappcore_no_tapa_el_refresco_con_un_stub():
    from gui_qt.core_host import QtAppCore
    assert QtAppCore._refresh_cleanup_item_after_replace is Cleanup._refresh_cleanup_item_after_replace
