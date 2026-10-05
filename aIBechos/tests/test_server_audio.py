from core.server_audio import (classify_audio_tracks, audio_map_to_episodes,
                                cutoff_from_audio, remap_audio_keys)


def _jelly(spanish_title, code="spa"):
    return {"Type": "Audio", "Language": code, "DisplayTitle": spanish_title}


def test_castellano_explicito_vale():
    assert classify_audio_tracks([_jelly("Español (España)")]) is True
    assert classify_audio_tracks([_jelly("Castellano")]) is True
    assert classify_audio_tracks([{"streamType": 2, "languageTag": "es-ES"}]) is True


def test_latino_solo_no_vale():
    assert classify_audio_tracks([_jelly("Español (Latinoamérica)")]) is False
    assert classify_audio_tracks([_jelly("Latino", "es-419")]) is False
    assert classify_audio_tracks([_jelly("Español (México)")]) is False


def test_generico_espanol_se_acepta():
    assert classify_audio_tracks([_jelly("Español")]) is True
    assert classify_audio_tracks([{"Type": "Audio", "Language": "spa"}]) is True


def test_latino_mas_castellano_gana_castellano():
    tracks = [_jelly("Latino"), _jelly("Castellano")]
    assert classify_audio_tracks(tracks) is True


def test_solo_ingles_no_hay_doblaje():
    tracks = [{"Type": "Audio", "Language": "eng", "DisplayTitle": "English"}]
    assert classify_audio_tracks(tracks) is False


def test_sin_pistas_no_hay_dato():
    assert classify_audio_tracks([]) is None
    assert classify_audio_tracks(None) is None
    assert classify_audio_tracks([{"Type": "Video", "Language": "spa"}]) is None


def test_audio_map_a_episodios_quita_unknowns():
    m = {(1, 1): True, (1, 2): False, (1, 3): None}
    assert audio_map_to_episodes(m) == {"1x01": True, "1x02": False}


def test_cutoff_solo_tramo_inicial_completo():
    m = {(1, 1): True, (1, 2): True, (1, 3): None, (1, 4): True}
    assert cutoff_from_audio(m) == {1: 2}
    assert cutoff_from_audio({(2, 1): False}) == {}


def test_remap_absoluto_igual_que_present():
    m = {(1, 101): True, (1, 102): False}
    assert remap_audio_keys(m, {1: 100, 2: 100}) == {(2, 1): True, (2, 2): False}
