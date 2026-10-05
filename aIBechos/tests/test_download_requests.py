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
