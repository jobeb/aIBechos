from core import download_requests as dr


def test_new_request_builds_pending_entry():
    req_id, entry = dr.new_request(1234, "tv", "Serie", "Jose", season=1,
                                   episode=2, year="2024", now=1000.0)
    assert len(req_id) == 16
    assert entry["status"] == "pending"
    assert entry["claimed_by"] == ""
    assert entry["attempts"] == 0
    assert entry["season"] == 1 and entry["episode"] == 2
    assert entry["requested_at"] == 1000.0


def test_new_request_movie_drops_season_episode():
    _, entry = dr.new_request(99, "movie", "Peli", "Jose", season=1,
                              episode=2, now=5.0)
    assert entry["season"] is None and entry["episode"] is None


def test_new_request_rejects_garbage():
    import pytest
    for args in [(-1, "tv", "T", "J"), (0, "tv", "T", "J"),
                 (1, "libro", "T", "J"), (1, "tv", "", "J"),
                 (1, "tv", "T", ""), ("xx", "tv", "T", "J")]:
        try:
            dr.new_request(*args)
            assert False, f"debería fallar: {args}"
        except ValueError:
            pass


def test_claim_pending_by_first_user():
    _, entry = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    data = {"abc": entry}
    claimed = dr.claim(data, "abc", "Jose", now=200.0)
    assert claimed is not None
    assert claimed["abc"]["status"] == "claimed"
    assert claimed["abc"]["claimed_by"] == "Jose"
    assert claimed["abc"]["claimed_at"] == 200.0
    # Sin mutar el original.
    assert data["abc"]["status"] == "pending"


def test_claim_rejects_taken_without_expiry():
    _, entry = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    data = dr.claim({"a": entry}, "a", "Jose", now=200.0)
    assert dr.claim(data, "a", "Otro", now=300.0) is None


def test_claim_allows_stale_takeover_and_counts_attempt():
    _, entry = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    data = dr.claim({"a": entry}, "a", "Jose", now=200.0)
    retaken = dr.claim(data, "a", "Otro",
                       now=200.0 + dr.CLAIM_TTL_SECONDS + 1)
    assert retaken is not None
    assert retaken["a"]["claimed_by"] == "Otro"
    assert retaken["a"]["attempts"] == 1


def test_claim_rejects_final_and_missing():
    _, entry = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    data = {"a": {**entry, "status": "done"}}
    assert dr.claim(data, "a", "Jose", now=200.0) is None
    assert dr.claim({}, "zzz", "Jose", now=200.0) is None
    assert dr.claim(data, "a", "", now=200.0) is None


def test_mark_status_moves_and_seals_done():
    _, entry = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    data = dr.claim({"a": entry}, "a", "Jose", now=200.0)
    moved = dr.mark_status(data, "a", "downloading", user="Jose", now=300.0)
    assert moved["a"]["status"] == "downloading"
    done = dr.mark_status(moved, "a", "done", user="Jose", now=400.0)
    assert done["a"]["completed_at"] == 400.0
    failed = dr.mark_status(moved, "a", "failed", user="Jose",
                            error="sin fuentes", now=500.0)
    assert failed["a"]["last_error"] == "sin fuentes"


def test_mark_status_respects_owner_and_validates():
    _, entry = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    data = dr.claim({"a": entry}, "a", "Jose", now=200.0)
    assert dr.mark_status(data, "a", "done", user="Otro", now=300.0) is None
    assert dr.mark_status(data, "a", "inventado", now=300.0) is None
    assert dr.mark_status({}, "zzz", "done", now=300.0) is None


def test_merge_newest_wins_per_request():
    _, e1 = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    old = {"a": e1, "b": e1}
    new = dict(e1)
    new["status"] = "claimed"
    new["updated_at"] = 200.0
    merged = dr.merge(old, {"a": new})
    assert merged["a"]["status"] == "claimed"
    assert merged["b"]["status"] == "pending"
    # La remota vieja no pisa la local nueva.
    assert dr.merge({"a": new}, {"a": e1})["a"]["status"] == "claimed"


def test_merge_ignores_garbage():
    assert dr.merge({"a": "basura"}, {"b": None}) == {}
    assert dr.merge(None, None) == {}


def test_prune_drops_only_old_final():
    _, e = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    now = 10_000_000.0
    done_old = {**e, "status": "done", "updated_at": now - dr.PRUNE_AFTER_SECONDS - 1}
    done_new = {**e, "status": "done", "updated_at": now - 100.0}
    pending = {**e, "status": "pending", "updated_at": 100.0}
    data = {"o": done_old, "n": done_new, "p": pending}
    pruned = dr.prune(data, now=now)
    assert set(pruned) == {"n", "p"}


def test_pending_for_worker_orders_oldest_first():
    _, e1 = dr.new_request(1, "tv", "A", "Ana", now=100.0)
    _, e2 = dr.new_request(2, "tv", "B", "Ana", now=50.0)
    data = {"a": e1, "b": e2}
    pending = dr.pending_for_worker(data, now=200.0)
    assert [r for r, _ in pending] == ["b", "a"]


def test_is_claim_stale_only_for_active():
    _, e = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    assert dr.is_claim_stale(e, now=200.0) is False
    claimed = dr.claim({"a": e}, "a", "Jose", now=200.0)["a"]
    assert dr.is_claim_stale(claimed, now=300.0) is False
    assert dr.is_claim_stale(claimed,
                             now=300.0 + dr.CLAIM_TTL_SECONDS) is True


def test_release_back_to_pending_and_counts_attempt():
    _, entry = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    data = dr.claim({"a": entry}, "a", "Jose", now=200.0)
    back = dr.release(data, "a", user="Jose", error="sin fuentes", now=300.0)
    assert back["a"]["status"] == "pending"
    assert back["a"]["claimed_by"] == ""
    assert back["a"]["attempts"] == 1
    assert back["a"]["last_error"] == "sin fuentes"


def test_release_respects_owner():
    _, entry = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    data = dr.claim({"a": entry}, "a", "Jose", now=200.0)
    assert dr.release(data, "a", user="Otro", now=300.0) is None
    assert dr.release({}, "zzz", now=300.0) is None


def test_release_fails_after_max_attempts():
    _, entry = dr.new_request(1, "tv", "T", "Ana", now=100.0)
    data = {"a": {**entry, "status": "claimed", "claimed_by": "Jose",
                  "attempts": dr.MAX_ATTEMPTS - 1}}
    out = dr.release(data, "a", user="Jose", error="boom", now=200.0)
    assert out["a"]["status"] == "failed"
    assert out["a"]["attempts"] == dr.MAX_ATTEMPTS


def test_describe_never_raises():
    assert dr.describe({"title": "T", "year": "2024", "season": 1,
                        "episode": 2}) == "T (2024) [1x02]"
    assert dr.describe({"title": "T", "season": 2}) == "T [T2]"
    assert dr.describe({"title": "Peli"}) == "Peli"
    assert dr.describe({}) == "¿?"
    assert dr.describe(None) == "¿?"


def test_matches_upload_movie_by_title_and_year():
    movie = {"tmdb_id": 974835, "media_type": "movie", "season": None,
             "episode": None, "title": "Hit Man. Asesino por casualidad",
             "year": "2024"}
    assert dr.matches_upload(
        movie, "/datos/peliculas//Hit Man. Asesino por casualidad (2024).mkv")
    assert not dr.matches_upload(
        movie, "/datos/peliculas//Otra Cosa (2024).mkv")
    # Año distinto en el pedido descarta (remake/homónimos).
    assert not dr.matches_upload(dict(movie, year="2023"),
        "/datos/peliculas//Hit Man. Asesino por casualidad (2024).mkv")


def test_matches_upload_episode_needs_numbers_and_series():
    ep = {"tmdb_id": 1, "media_type": "tv", "season": 1, "episode": 7,
          "title": "Dragon Ball Daima", "year": ""}
    ok = "/datos2/series/Dragon Ball Daima/Temporada 01/Dragon Ball Daima 1x07 Glorio.mkv"
    assert dr.matches_upload(ep, ok)
    assert not dr.matches_upload(
        ep, "/datos2/series/x/Dragon Ball Daima 1x08 Cotorra.mkv")
    assert not dr.matches_upload(
        ep, "/datos2/series/x/Naruto 1x07 Algo.mkv")
    assert not dr.matches_upload(
        ep, "/datos2/series/x/Dragon Ball Daima Especial.mkv")
    # Alcance temporada/serie no se confirma con un archivo.
    assert not dr.matches_upload(dict(ep, episode=None), ok)
    assert not dr.matches_upload({}, ok)
    assert not dr.matches_upload(ep, "")


def test_uploaded_request_only_counts_ok():
    movie = {"tmdb_id": 1, "media_type": "movie", "season": None,
             "episode": None, "title": "Hit Man. Asesino por casualidad",
             "year": "2024"}
    hist = [
        {"status": "error", "remote": "/datos/peliculas//Hit Man. Asesino por casualidad (2024).mkv"},
        {"status": "ok", "remote": "/datos/peliculas//Hit Man. Asesino por casualidad (2024).mkv"},
    ]
    assert dr.uploaded_request(hist, movie)
    assert not dr.uploaded_request(hist[:1], movie)
    assert not dr.uploaded_request([], movie)
    assert not dr.uploaded_request("basura", movie)


def test_norm_title_strips_junk_and_years():
    assert dr.norm_title(
        "Deadpool 2  Version Extendida [BluRay Rip][www.descargas2020.com]") == "deadpool 2"
    assert dr.norm_title("Cortocircuito (John Badham, 1986)") == "cortocircuito"
    assert dr.norm_title("") == ""


def test_names_match_mirror_web_cases():
    cases = [
        ("Cortocircuito", "1986", "Cortocircuito (John Badham", "1986", True),
        ("Deadpool 2", "2018",
         "Deadpool 2  Version Extendida [BluRay Rip]", "2018", True),
        ("It", "2017", "It Capitulo 2", "2019", False),
        ("Dune", "2021", "Dune Parte Dos", "2024", False),
        ("Avatar", "2009", "Avatar", "", True),
        ("Breaking Bad", "2008", "I+", "", False),
        ("Hit Man. Asesino por casualidad", "2024",
         "Hit Man. Asesino por casualidad", "2024", True),
    ]
    for tt, ty, raw, iy, want in cases:
        assert dr.names_match(tt, ty, raw, iy) is want, (tt, raw)


def test_match_any_name_never_raises():
    items = [("deadpool 2", 2018), ("avatar", 2009)]
    assert dr.match_any_name("Deadpool 2", "2018", items)
    assert not dr.match_any_name("Torrente", "1998", items)
    assert not dr.match_any_name("X", "2000", None)
    assert not dr.match_any_name("", "", items)


def test_uploaded_request_since_solo_cuenta_lo_nuevo():
    movie = {"tmdb_id": 1, "media_type": "movie", "season": None,
             "episode": None, "title": "Hit Man. Asesino por casualidad",
             "year": "2024", "replace": True}
    remote = "/datos/peliculas//Hit Man. Asesino por casualidad (2024).mkv"
    old = {"status": "ok", "remote": remote, "ts": 1000}
    new = {"status": "ok", "remote": remote, "ts": 2000}
    assert not dr.uploaded_request_since([old], movie, 1500)
    assert dr.uploaded_request_since([old, new], movie, 1500)
    assert not dr.uploaded_request_since([old, new], movie, None)
    assert not dr.uploaded_request_since([{"status": "ok", "remote": remote,
                                           "ts": "basura"}], movie, 1500)


def test_replacement_flags():
    data = {"a": {"tmdb_id": 1, "status": "downloading", "replace": True},
            "b": {"tmdb_id": 2, "status": "downloading"}}
    assert dr.is_replacement(data["a"])
    assert not dr.is_replacement(data["b"])
    assert not dr.is_replacement(None)
    nd = dr.mark_replacement_launched(data, "a", now=1234.0)
    assert nd["a"]["replace_since"] == 1234.0
    assert "replace_since" not in data["a"], "no muta el original"
    assert dr.mark_replacement_launched(data, "zz") is None


def test_matches_upload_capitulo_sin_ano_en_el_nombre():
    """Caso real: sustitución de Reacher 1x01 subida como "Reacher 1x01
    Bienvenido a Margrave.mkv" (sin año, como casi todos los capítulos)
    y nunca dada por hecha porque se exigía el año en el nombre."""
    ep = {"title": "Reacher", "year": "2022", "media_type": "tv",
          "season": 1, "episode": 1}
    base = "/datos2/series/Reacher/Temporada 01//"
    assert dr.matches_upload(ep, base + "Reacher 1x01 Bienvenido a Margrave.mkv")
    assert dr.matches_upload(ep, base + "Reacher (2022) 1x01 Bienvenido.mkv")
    assert not dr.matches_upload(ep, base + "Reacher (2019) 1x01 Otra.mkv")
    assert not dr.matches_upload(ep, base + "Reacher 1x02 Primer paso.mkv")
    assert not dr.matches_upload(ep, "/x/Jack Reacher 1x01 Algo.mkv")
    assert not dr.matches_upload(ep, "/x/Lost 1x01 Pilot.mkv")


def test_uploaded_request_since_sustitucion_de_capitulo():
    ep = {"tmdb_id": 108978, "title": "Reacher", "year": "2022",
          "media_type": "tv", "season": 1, "episode": 1, "replace": True}
    remote = "/datos2/series/Reacher/Temporada 01//Reacher 1x01 Bienvenido a Margrave.mkv"
    hist = [{"status": "ok", "remote": remote, "ts": 2000}]
    assert dr.uploaded_request_since(hist, ep, 1500)
    assert not dr.uploaded_request_since(hist, ep, 2500)


def test_lost_in_amule_obsession():
    """Caso real: Obsession "downloading" desde el 06/10 pero quitada de
    aMule: se libera para relanzarla. Sigue en aMule, terminada en la
    carpeta vigilada o recién lanzada: no se toca."""
    now = 10_000.0
    old = now - 2 * dr.LOST_GRACE_SECONDS
    peli = {"tmdb_id": 1339713, "media_type": "movie", "title": "Obsession",
            "year": "2026", "status": "downloading", "claimed_at": old}
    assert dr.lost_in_amule(peli, [{"hash_hex": "aa", "name": "Otra cosa 2024.mkv"}], [], now=now)
    # Sin hashes guardados (antigua): por nombre en la cola
    assert not dr.lost_in_amule(peli, [{"hash_hex": "bb", "name": "Obsession (2026) 1080p Castellano.mkv"}], [], now=now)
    # Con hashes: por hash (aunque el nombre no case)
    con = dict(peli, amule_hashes=["abc123"])
    assert not dr.lost_in_amule(con, [{"hash_hex": "ABC123", "name": "x.mkv"}], [], now=now)
    assert dr.lost_in_amule(con, [{"hash_hex": "zzz", "name": "Obsession 2026.mkv"}], [], now=now)
    # Terminada y esperando a subirse en la carpeta vigilada
    assert not dr.lost_in_amule(peli, [], ["Obsession.2026.1080p.WEB-DL.Castellano.mkv"], now=now)
    # Recién lanzada
    assert not dr.lost_in_amule(dict(peli, claimed_at=now - 60), [], [], now=now)
    # Temporada / serie completa: no se decide así
    temporada = {"media_type": "tv", "title": "Reacher", "season": 1, "episode": None,
                 "status": "downloading", "claimed_at": old}
    assert not dr.lost_in_amule(temporada, [], [], now=now)
    # Capítulo: en la cola con SxE
    cap = {"media_type": "tv", "title": "Reacher", "year": "2022", "season": 1, "episode": 3,
           "status": "downloading", "claimed_at": old}
    assert not dr.lost_in_amule(cap, [{"hash_hex": "c", "name": "Reacher 1x03 HDTV.mkv"}], [], now=now)
    assert dr.lost_in_amule(cap, [{"hash_hex": "c", "name": "Reacher 1x04 HDTV.mkv"}], [], now=now)


def test_set_amule_hashes():
    data = {"a": {"status": "downloading"}}
    nd = dr.set_amule_hashes(data, "a", ["AB", "", "ab", "cd"])
    assert nd["a"]["amule_hashes"] == ["ab", "cd"]
    assert "amule_hashes" not in data["a"]
    assert dr.set_amule_hashes(data, "zz", ["x"]) is None


def test_history_rows():
    data = {
        "a": {"title": "Obsession", "year": "2026", "media_type": "movie", "requested_by": "Efren",
              "requested_at": 100, "status": "downloading", "claimed_by": "Jose"},
        "b": {"title": "Reacher", "media_type": "tv", "season": 1, "episode": 1, "requested_by": "Jose",
              "requested_at": 200, "status": "failed", "last_error": "sin fuentes"},
        "c": "basura",
    }
    rows = dr.history_rows(data)
    assert [r["id"] for r in rows] == ["b", "a"]
    assert rows[0]["name"] == "Reacher [1x01]"
    assert rows[0]["detail"] == "sin fuentes" and rows[0]["status_es"] == "Fallida"
    assert rows[1]["detail"] == "En el equipo de Jose" and rows[1]["person"] == "Efren"
    assert dr.history_rows(None) == []


def test_merge_cancelled_is_sticky():
    web = {"r": {"status": "cancelled", "cancelled_by": "Efren", "claimed_by": "Jose",
                 "cancel_done": False, "updated_at": 100}}
    pc = {"r": {"status": "downloading", "claimed_by": "Jose", "amule_hashes": ["ab"],
                "updated_at": 200}}
    for merged in (dr.merge(web, pc), dr.merge(pc, web)):
        e = merged["r"]
        assert e["status"] == "cancelled" and e["amule_hashes"] == ["ab"]
        assert e["cancel_done"] is False


def test_merge_cancel_while_claiming_keeps_claimer():
    # La web la canceló "pending"; a la vez un PC la reclamó y lanzó.
    web = {"r": {"status": "cancelled", "claimed_by": "", "cancel_done": True, "updated_at": 100}}
    pc = {"r": {"status": "claimed", "claimed_by": "Jose", "updated_at": 150}}
    e = dr.merge(pc, web)["r"]
    assert e["status"] == "cancelled" and e["claimed_by"] == "Jose" and e["cancel_done"] is False
    assert dr.to_cancel({"r": e}, "Jose") == ["r"]
    assert dr.to_cancel({"r": e}, "Otro") == []
    done = dr.mark_cancel_done({"r": e}, "r")
    assert dr.to_cancel(done, "Jose") == []


def test_amule_hashes_to_cancel():
    queue = [{"hash_hex": "AA", "name": "Obsession (2026).mkv"},
             {"hash_hex": "bb", "name": "Otra cosa (2020).mkv"}]
    assert dr.amule_hashes_to_cancel({"amule_hashes": ["aa"]}, queue) == ["aa"]
    legacy = {"title": "Obsession", "year": "2026", "media_type": "movie"}
    assert dr.amule_hashes_to_cancel(legacy, queue) == ["aa"]
    assert dr.amule_hashes_to_cancel({"amule_hashes": ["cc"]}, queue) == []


def test_prune_and_history_cancelled():
    old = {"r": {"status": "cancelled", "updated_at": 0, "cancelled_by": "Ana", "title": "X"}}
    assert dr.prune(old, now=10 ** 9) == {}
    row = dr.history_rows({"r": {**old["r"], "updated_at": 5}})[0]
    assert row["status_es"] == "Cancelada" and row["detail"] == "Cancelada por Ana"
