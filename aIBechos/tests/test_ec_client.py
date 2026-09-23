"""Tests del cliente EC binario (core/ec_client.py), con paquetes fake.

Se construyen paquetes EC_OP_SEARCH_RESULTS con los mismos helpers de
serialización (_make_tag / _make_int_tag / _make_string_tag / _make_packet)
que usa el propio módulo, de modo que se valida el parseo: lectura de nombres
COMPLETOS sin el truncado a 80 chars que amulecmd imprime, campos numéricos y
hash MD4 para descargar.

Cubre el bug de taglen de los tags con hijos: para un tag con hijos el final
se desplaza 2 bytes más allá de start+7+taglen porque el uint16 de cuenta de
hijos NO cuenta dentro de taglen (verificado contra aMule 3.0.1 real).
"""

import pytest

from core.ec_client import (
    EC_OP_SEARCH_RESULTS,
    EC_OP_STRINGS,
    EC_TAG_PARTFILE_HASH,
    EC_TAG_PARTFILE_NAME,
    EC_TAG_PARTFILE_SIZE_FULL,
    EC_TAG_PARTFILE_SOURCE_COUNT,
    EC_TAG_PARTFILE_SOURCE_COUNT_XFER,
    EC_TAG_PARTFILE_STATUS,
    EC_TAG_SEARCHFILE,
    EcClient,
    EcProtocolError,
    _make_hash16_tag,
    _make_int_tag,
    _make_packet,
    _make_string_tag,
    _make_tag,
    _parse_packet,
)

_LONG_NAME = (
    "Star Wars Episodi I - L'amena\u00e7a fantasma - The phantom menace (1999) "
    "[m1080p] english, castellano + subs catal\u00e0, castellano, english per "
    "godowk.mkv"
)
assert len(_LONG_NAME) > 80, "el nombre de prueba debería exceder 80 chars"


def _searchfile_tag(number, name, size=1509949440, sources=7, complete=3,
                    status=0, hash16=b"\xab" * 16):
    return _make_tag(
        EC_TAG_SEARCHFILE, 4, number.to_bytes(4, "big"),
        children=[
            _make_int_tag(EC_TAG_PARTFILE_SIZE_FULL, size),
            _make_int_tag(EC_TAG_PARTFILE_SOURCE_COUNT, sources),
            _make_int_tag(EC_TAG_PARTFILE_SOURCE_COUNT_XFER, complete),
            _make_int_tag(EC_TAG_PARTFILE_STATUS, status),
            _make_string_tag(EC_TAG_PARTFILE_NAME, name),
            _make_hash16_tag(EC_TAG_PARTFILE_HASH, hash16),
        ])


def _results_body(*files):
    return bytes([EC_OP_SEARCH_RESULTS]) + len(files).to_bytes(2, "big") + b"".join(files)


def _results_packet(*files):
    return _make_packet(EC_OP_SEARCH_RESULTS, list(files))


def _parse_tag(tag_bytes):
    """Serializa un tag suelto como paquete de un solo resultado y devuelve
    el _Tag ya parseado (lo que recibe _parse_search_file en producción)."""
    op, tags = _parse_packet(_results_body(tag_bytes))
    assert op == EC_OP_SEARCH_RESULTS
    assert len(tags) == 1
    return tags[0]


class _FakeSocket:
    """Socket fake que entrega un único paquete y luego se agota."""

    def __init__(self, packet_bytes):
        self._buffer = packet_bytes

    def recv(self, n):
        if not self._buffer:
            return b""
        chunk = self._buffer[:n]
        self._buffer = self._buffer[n:]
        return chunk


def test_parse_keeps_full_name_and_metadata():
    file_tag = _searchfile_tag(number=42, name=_LONG_NAME)
    parsed = EcClient._parse_search_file(_parse_tag(file_tag))
    assert parsed is not None
    assert parsed.number == 42
    assert parsed.name == _LONG_NAME
    assert len(parsed.name) > 80
    assert parsed.sources == 7
    assert parsed.complete is True  # complete>0
    assert parsed.size_human == "1.41 GB"  # 1509949440 bytes
    assert parsed._ec_hash == b"\xab" * 16


def test_parse_many_files_keeps_order_and_names():
    names = [_LONG_NAME, "short.mkv", "Otra más (2020).mkv"]
    files = [_searchfile_tag(number=100 + i, name=n) for i, n in enumerate(names)]
    op, tags = _parse_packet(_results_body(*files))
    assert op == EC_OP_SEARCH_RESULTS
    assert len(tags) == 3
    parsed = [EcClient._parse_search_file(t) for t in tags]
    assert [p.number for p in parsed] == [100, 101, 102]
    assert [p.name for p in parsed] == names


def test_parse_marks_incomplete_when_no_full_sources():
    file_tag = _searchfile_tag(number=1, name="x.mkv", complete=0)
    parsed = EcClient._parse_search_file(_parse_tag(file_tag))
    assert parsed.complete is False


def test_parse_skips_file_without_name_child():
    tag = _make_tag(EC_TAG_SEARCHFILE, 4, (5).to_bytes(4, "big"), children=[])
    assert EcClient._parse_search_file(_parse_tag(tag)) is None


def test_recv_packet_roundtrip_with_socket():
    """El parseo de un paquete completo (cabecera + cuerpo) devuelve el
    mismo resultado que construir el tag directamente, probando que el bug
    de taglen no rompe la lectura de tags con hijos."""
    file_tag = _searchfile_tag(number=7, name=_LONG_NAME)
    packet = _results_packet(file_tag)
    client = EcClient("127.0.0.1", 4712, "pw", sock=_FakeSocket(packet))
    client._connected = True
    op, tags = client._recv_packet()
    assert op == EC_OP_SEARCH_RESULTS
    assert len(tags) == 1
    parsed = EcClient._parse_search_file(tags[0])
    assert parsed.name == _LONG_NAME


def test_missing_hash_means_download_refused():
    """download() sobre un resultado sin _ec_hash (p.ej. llegado desde el
    parser de amulecmd) devuelve (False, ...) sin tocar el socket."""
    from core.amule_client import AmuleSearchResult
    client = EcClient("127.0.0.1", 4712, "pw")
    result = AmuleSearchResult(number=1, name="x.mkv", size_human="1 MB",
                               sources=1, complete=False)
    ok, err = client.download(result)
    assert ok is False
    assert "hash" in err


class _MultiSocket:
    """Socket fake que entrega una cola de paquetes en orden (recv respeta el
    tamaño pedido, para que _recv_exact lea cabecera y cuerpo por separado)."""

    def __init__(self, packets):
        self._queue = list(packets)
        self._saw_recv = 0

    def recv(self, n):
        self._saw_recv += 1
        while self._queue:
            packet = self._queue[0]
            chunk = packet[:n]
            if len(packet) > n:
                self._queue[0] = packet[n:]
            else:
                self._queue.pop(0)
            if chunk:
                return chunk
        return b""

    def sendall(self, data):
        return None


def test_iter_search_wake_event_skips_the_first_wait(monkeypatch):
    """wake_event=Event() ya puesto despierta el primer sondeo de inmediato:
    iter_search entrega su primer lote SIN esperar el poll_interval (lo que
    permite enviar una descarga pendiente por la misma conexión al instante).
    Sin wake_event sí se espera el intervalo antes del primer lote."""
    import threading
    import core.ec_client as mod
    file_tag = _searchfile_tag(number=1, name="Serie S01E01 720p.mkv")
    # ack de SEARCH_START + un lote de resultados por cada get_results()
    ack = _make_packet(EC_OP_STRINGS, [])
    batch = _make_packet(EC_OP_SEARCH_RESULTS, [file_tag])

    slept = []
    now = [0.0]
    monkeypatch.setattr(mod._time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(mod._time, "monotonic", lambda: now[0])

    # Sin wake_event: antes del primer lote se duerme poll_interval.
    client = EcClient("127.0.0.1", 4712, "pw", sock=_MultiSocket([ack, batch]))
    client._connected = True
    it = iter(client.iter_search("X", poll_interval=5.0, max_duration=100.0))
    first = next(it)
    assert [r.name for r in first] == ["Serie S01E01 720p.mkv"]
    assert slept and slept[0] == pytest.approx(5.0)

    # Con wake_event ya puesto: el primer lote sale sin dormir.
    slept.clear()
    client2 = EcClient("127.0.0.1", 4712, "pw", sock=_MultiSocket([ack, batch]))
    client2._connected = True
    wake = threading.Event()
    wake.set()
    it2 = iter(client2.iter_search("X", poll_interval=5.0, max_duration=100.0,
                                   wake_event=wake))
    first2 = next(it2)
    assert [r.name for r in first2] == ["Serie S01E01 720p.mkv"]
    assert not slept
