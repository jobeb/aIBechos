"""Capítulos sin doblaje confirmado: solo se descargan candidatos cuyo
nombre declara castellano -- real: Dragon Ball Daima 1x11/1x14 se bajaron
en japonés ("Dragon Ball Daima - 1X11 - Leyenda.mkv", sin idioma) desde
"Descargar temporada" de Episodios."""

import logging

import pytest

import core.amule_download as ad
from core.amule_client import AmuleSearchResult as R
from core.download_quality import declares_castellano_audio


@pytest.mark.parametrize("name", [
    "Dragon Ball Daima 1x15 [Castellano] [H265 - 1080p].mp4",
    "Serie 1x01 Spanish 1080p.mkv",
    "Serie 1x01 (DUAL) 720p.mkv",
    "Serie.1x01.SPA.mkv",
    "Serie 1x01 Español.mkv",
])
def test_declara_castellano(name):
    assert declares_castellano_audio(name)


@pytest.mark.parametrize("name", [
    "Dragon Ball Daima - 1X11 - Leyenda.mkv",
    "Serie 1x01 (V.O.S. Spa-Eng) - Spanish subs.mkv",
    "Serie 1x01 VOSE.mkv",
    "",
])
def test_no_declara_castellano(name):
    assert not declares_castellano_audio(name)


class _FakeEc:
    def __init__(self, results):
        self.results, self.downloaded = results, []
        self.host, self.port, self.password = "h", 1, "p"

    def connect(self):
        pass

    def close(self):
        pass

    def iter_search(self, query, **kw):
        yield self.results
        yield self.results

    def download(self, r):
        self.downloaded.append(r)
        return True, ""

    def get_download_queue(self):
        return [{"hash_hex": ""}]


def _run(monkeypatch, results, require):
    ec = _FakeEc(results)
    monkeypatch.setattr(ad, "ec_client_from_config", lambda cfg: ec)
    monkeypatch.setattr(ad, "_already_completed", lambda ec, best: False)
    import threading
    out = ad.auto_download({}, threading.Lock(), "Dragon Ball Daima 1x11",
                           typical_size=193 * 1024 * 1024, require_castellano=require)
    return out, ec


JAPO = R(1, "Dragon Ball Daima - 1X11 - Leyenda.mkv", "311.0 MB", 5)
CAST = R(2, "Dragon Ball Daima 1x11 [Castellano] [H265 - 1080p].mp4", "283.4 MB", 1)


def test_sin_castellano_no_descarga_nada(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="aIBechos.amule_download")
    (ok, why, _h, _n), ec = _run(monkeypatch, [JAPO], require=True)
    assert not ok and why == ad.NO_CASTELLANO_REASON
    assert ec.downloaded == []
    # Los candidatos quedan en el log aunque no se descargue
    assert "Leyenda.mkv" in caplog.text


def test_con_castellano_elige_ese(monkeypatch):
    (ok, _why, _h, name), ec = _run(monkeypatch, [JAPO, CAST], require=True)
    assert ok and name == CAST.name


def test_sin_exigir_sigue_como_antes(monkeypatch):
    (ok, _why, _h, name), _ec = _run(monkeypatch, [JAPO], require=False)
    assert ok and name == JAPO.name


def test_log_marca_el_elegido(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="aIBechos.amule_download")
    _run(monkeypatch, [JAPO, CAST], require=False)
    chosen_line = [l for l in caplog.text.splitlines() if "→" in l]
    assert chosen_line and "Castellano" in chosen_line[0]
