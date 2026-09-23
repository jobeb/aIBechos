from core.reservations import (
    add_reservation, remove_reservation, is_reserved, reserved_by, reserved_at,
    used_bytes, remaining_bytes, fits_in_quota, QUOTA_BYTES,
    load_local_cache, save_local_cache,
    is_name_taken, transfer_reservations, remove_all_by_owner,
)

_GB = 1024 ** 3


def test_add_reservation_returns_new_dict_without_mutating_original():
    original = {}
    result = add_reservation(original, "tv", 1234, "Breaking Bad", 5 * _GB, "Jose")
    assert original == {}   # no mutado
    assert is_reserved(result, "tv", 1234) is True


def test_add_reservation_stores_expected_fields():
    result = add_reservation({}, "tv", 1234, "Breaking Bad", 5 * _GB, "Jose")
    assert result["tv:1234"] == {
        "media_type": "tv", "tmdb_id": 1234, "name": "Breaking Bad",
        "size_bytes": 5 * _GB, "reserved_by": "Jose", "reserved_at": 0,
    }


def test_add_reservation_stores_when():
    result = add_reservation({}, "tv", 1234, "Breaking Bad", 5 * _GB, "Jose", at=1789700000)
    assert reserved_at(result, "tv", 1234) == 1789700000


def test_reserved_at_zero_when_missing_or_invalid():
    assert reserved_at({}, "tv", 9999) == 0
    legacy = {"tv:1234": {"media_type": "tv", "tmdb_id": 1234, "reserved_by": "Jose"}}
    assert reserved_at(legacy, "tv", 1234) == 0
    assert reserved_at({"tv:1234": "basura"}, "tv", 1234) == 0


def test_remove_reservation_returns_new_dict_without_the_key():
    data = add_reservation({}, "movie", 42, "Inception", 1 * _GB, "Jose")
    result = remove_reservation(data, "movie", 42)
    assert is_reserved(data, "movie", 42) is True    # original intacto
    assert is_reserved(result, "movie", 42) is False


def test_remove_reservation_is_a_no_op_when_not_present():
    result = remove_reservation({}, "movie", 999)
    assert result == {}


def test_is_reserved_distinguishes_media_type_with_same_tmdb_id():
    data = add_reservation({}, "tv", 1234, "Serie", 1 * _GB, "Jose")
    assert is_reserved(data, "tv", 1234) is True
    assert is_reserved(data, "movie", 1234) is False


def test_reserved_by_returns_owner_or_none():
    data = add_reservation({}, "tv", 1234, "Serie", 1 * _GB, "Jose")
    assert reserved_by(data, "tv", 1234) == "Jose"
    assert reserved_by(data, "tv", 9999) is None


def test_used_bytes_only_counts_the_given_user():
    data = add_reservation({}, "tv", 1, "A", 10 * _GB, "Jose")
    data = add_reservation(data, "tv", 2, "B", 20 * _GB, "Ana")
    data = add_reservation(data, "movie", 3, "C", 5 * _GB, "Jose")
    assert used_bytes(data, "Jose") == 15 * _GB
    assert used_bytes(data, "Ana") == 20 * _GB
    assert used_bytes(data, "Nadie") == 0


def test_remaining_bytes_clamped_at_zero_when_over_quota():
    data = add_reservation({}, "tv", 1, "A", QUOTA_BYTES + 10 * _GB, "Jose")
    assert remaining_bytes(data, "Jose") == 0


def test_remaining_bytes_full_quota_for_unused_user():
    assert remaining_bytes({}, "Jose") == QUOTA_BYTES


def test_fits_in_quota_true_when_under_limit():
    data = add_reservation({}, "tv", 1, "A", 50 * _GB, "Jose")
    assert fits_in_quota(data, "Jose", 40 * _GB) is True


def test_fits_in_quota_false_when_would_exceed_limit():
    data = add_reservation({}, "tv", 1, "A", 50 * _GB, "Jose")
    assert fits_in_quota(data, "Jose", 51 * _GB) is False


def test_fits_in_quota_exact_boundary_is_allowed():
    data = add_reservation({}, "tv", 1, "A", 50 * _GB, "Jose")
    assert fits_in_quota(data, "Jose", 50 * _GB) is True


def test_remaining_bytes_respects_configured_quota_override():
    custom_quota = 200 * _GB
    data = add_reservation({}, "tv", 1, "A", 150 * _GB, "Jose")
    assert remaining_bytes(data, "Jose", quota_bytes=custom_quota) == 50 * _GB


def test_fits_in_quota_respects_configured_quota_override():
    custom_quota = 200 * _GB
    data = add_reservation({}, "tv", 1, "A", 150 * _GB, "Jose")
    # No cabria con la cuota por defecto (100GB), pero si con una mayor configurada
    assert fits_in_quota(data, "Jose", 40 * _GB) is False
    assert fits_in_quota(data, "Jose", 40 * _GB, quota_bytes=custom_quota) is True


def test_fits_in_quota_ignores_other_users_reservations():
    data = add_reservation({}, "tv", 1, "A", QUOTA_BYTES, "Ana")
    assert fits_in_quota(data, "Jose", QUOTA_BYTES) is True


def test_load_local_cache_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("core.reservations.app_data_dir", lambda: tmp_path)
    assert load_local_cache() == {}


def test_save_and_load_local_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("core.reservations.app_data_dir", lambda: tmp_path)
    data = add_reservation({}, "tv", 1234, "Breaking Bad", 5 * _GB, "Jose")

    save_local_cache(data)

    assert load_local_cache() == data


def test_load_local_cache_returns_empty_dict_on_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.setattr("core.reservations.app_data_dir", lambda: tmp_path)
    (tmp_path / "reservations.json").write_text("{esto no es json", encoding="utf-8")
    assert load_local_cache() == {}


def test_is_name_taken_true_when_someone_else_has_it():
    data = add_reservation({}, "tv", 1, "A", 1 * _GB, "Ana")
    assert is_name_taken(data, "Ana") is True


def test_is_name_taken_false_when_nobody_has_it():
    data = add_reservation({}, "tv", 1, "A", 1 * _GB, "Ana")
    assert is_name_taken(data, "Jose") is False


def test_is_name_taken_excludes_own_previous_name():
    data = add_reservation({}, "tv", 1, "A", 1 * _GB, "Jose")
    assert is_name_taken(data, "Jose", exclude="Jose") is False


def test_is_name_taken_still_true_for_someone_else_even_if_excluding_a_different_name():
    data = add_reservation({}, "tv", 1, "A", 1 * _GB, "Ana")
    assert is_name_taken(data, "Ana", exclude="Jose") is True


def test_transfer_reservations_moves_only_the_given_owners_entries():
    data = add_reservation({}, "tv", 1, "A", 10 * _GB, "Jose")
    data = add_reservation(data, "movie", 2, "B", 5 * _GB, "Ana")

    result = transfer_reservations(data, "Jose", "Jose2")

    assert reserved_by(result, "tv", 1) == "Jose2"
    assert reserved_by(result, "movie", 2) == "Ana"   # intacta
    assert reserved_by(data, "tv", 1) == "Jose"        # original no mutado


def test_transfer_reservations_no_op_when_owner_has_nothing():
    data = add_reservation({}, "tv", 1, "A", 10 * _GB, "Ana")
    result = transfer_reservations(data, "Jose", "Jose2")
    assert result == data


def test_remove_all_by_owner_only_removes_that_owner():
    data = add_reservation({}, "tv", 1, "A", 10 * _GB, "Jose")
    data = add_reservation(data, "movie", 2, "B", 5 * _GB, "Ana")

    result = remove_all_by_owner(data, "Jose")

    assert is_reserved(result, "tv", 1) is False
    assert is_reserved(result, "movie", 2) is True
    assert is_reserved(data, "tv", 1) is True   # original no mutado
