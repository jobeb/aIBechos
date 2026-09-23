from core.slim_candidates import find_oversize_flat
from core.download_quality import resolution_label

MB = 1024 * 1024


# --- resolution_label ----------------------------------------------------

def test_resolution_label_detecta():
    assert resolution_label("Peli.2021.4K.mkv") == "4K"
    # "2160p" no casa con \b2160\b (no hay frontera entre 0 y p): cae a
    # SD igual que en _size_range_for_resolution -- la etiqueta sigue a
    # esa función a propósito para no contradecir el techo aplicado.
    assert resolution_label("Peli.2021.2160p.mkv") == "SD"
    assert resolution_label("Serie 1x01 1080p.mkv") == "1080p"
    assert resolution_label("Serie 1x01 720p.mkv") == "720p"
    assert resolution_label("vieja.avi") == "SD"
    assert resolution_label("") == "SD"


# --- find_oversize_flat: series -------------------------------------------

def test_plano_serie_temporada_y_episodio_parseados():
    files = [(f"Serie 1x0{i} 720p.mkv", 400 * MB) for i in range(1, 6)]
    files.append(("Serie 1x06 1080p.mkv", 1500 * MB))
    rows = find_oversize_flat(files, "Serie")
    assert len(rows) == 1
    r = rows[0]
    assert r["name"] == "Serie 1x06 1080p.mkv"
    assert r["season"] == 1
    assert r["episode"] == 6
    assert r["ratio"] > 2.0
    assert r["saving"] > 0


def test_plano_serie_mediana_por_temporada_no_mezcla():
    # T01 ligera, T02 con un gordo: el gordo solo se compara con su
    # temporada (si la mediana fuese global daría igual aquí, pero el
    # season del resultado debe ser 2 y T01 no debe aportar nada).
    files = [(f"Serie 1x0{i} 720p.mkv", 400 * MB) for i in range(1, 6)]
    files += [(f"Serie 2x0{i} 720p.mkv", 400 * MB) for i in range(1, 5)]
    files.append(("Serie 2x05 1080p.mkv", 3000 * MB))
    rows = find_oversize_flat(files, "Serie")
    assert [r["name"] for r in rows] == ["Serie 2x05 1080p.mkv"]
    assert rows[0]["season"] == 2
    assert rows[0]["episode"] == 5


def test_plano_serie_temporada_pesada_no_contamina_otra():
    # T02 entera en remux 1080p (~3 GB): su mediana sale inflada y cae al
    # techo por resolución; T01 ligera no debe salir marcada por eso.
    files = [(f"Serie 1x0{i} 720p.mkv", 400 * MB) for i in range(1, 6)]
    files += [(f"Serie 2x0{i} 1080p REMUX.mkv", 3000 * MB) for i in range(1, 7)]
    rows = find_oversize_flat(files, "Serie")
    assert all(r["season"] == 2 for r in rows)
    assert not any(r["season"] == 1 for r in rows)


# --- find_oversize_flat: películas -----------------------------------------

def test_plano_pelis_mediana_por_resolucion():
    # 1080p pequeñas + 720p normal: con mediana global la de 720p
    # (1500 MB vs mediana 850) saldría marcada por error; por grupos no.
    files = [("PeliA.2020.1080p.mkv", 800 * MB),
             ("PeliB.2021.1080p.mkv", 850 * MB),
             ("PeliC.2019.720p.mkv", 1500 * MB)]
    rows = find_oversize_flat(files, "Pelis", is_movie=True)
    assert rows == []


def test_plano_pelis_gordo_en_su_resolucion():
    files = [("PeliA.2020.1080p.mkv", 1500 * MB),
             ("PeliB.2021.1080p.mkv", 1600 * MB),
             ("PeliC.2022.1080p.mkv", 5000 * MB)]
    rows = find_oversize_flat(files, "Pelis", is_movie=True)
    assert [r["name"] for r in rows] == ["PeliC.2022.1080p.mkv"]
    assert rows[0]["resolution"] == "1080p"
    assert rows[0]["source"] == "typical"


def test_plano_peli_sola_sin_mediana_cae_al_techo():
    # Una sola peli 720p de 7 GB: sin mediana, objetivo = techo 720p
    # (3 GB) -> 7 >= 3*2 y ahorro 4 GB -> candidata por resolution_cap.
    rows = find_oversize_flat([("Sola.2020.720p.mkv", 7000 * MB)],
                              "Pelis", is_movie=True)
    assert len(rows) == 1
    assert rows[0]["source"] == "resolution_cap"
    # Y una sola peli normal no sale marcada.
    assert find_oversize_flat([("Normal.2020.720p.mkv", 1500 * MB)],
                              "Pelis", is_movie=True) == []


# --- comunes ---------------------------------------------------------------

def test_plano_ordenado_por_ratio_y_respeta_desired():
    files = [(f"Serie 1x0{i} 720p.mkv", 800 * MB) for i in range(1, 5)]
    assert find_oversize_flat(files, "Serie") == []
    rows = find_oversize_flat(files, "Serie", desired_bytes=300 * MB)
    assert len(rows) == 4
    ratios = [r["ratio"] for r in rows]
    assert ratios == sorted(ratios, reverse=True)


def test_plano_ignora_no_video():
    files = [("Serie 1x01.nfo", 5 * MB), ("cover.jpg", 2 * MB),
             ("Serie 1x01 720p.mkv", 400 * MB)]
    assert find_oversize_flat(files, "Serie") == []
