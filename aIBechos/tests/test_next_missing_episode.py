from core.missing_ep_rows import next_missing_episode


def test_primer_hueco_sin_especiales_ni_ignorados():
    row = {"missing": {0: [1], 3: [5, 2], 2: [7]}, "ignored_seasons": [2],
           "ignored_episodes": {3: {2}}}
    assert next_missing_episode(row) == (3, 5)


def test_sin_huecos_o_serie_ignorada():
    assert next_missing_episode({"missing": {}}) is None
    assert next_missing_episode({"missing": {1: [1]}, "ignored": True}) is None
    assert next_missing_episode({"missing": {0: [1, 2]}}) is None
    assert next_missing_episode(None) is None
