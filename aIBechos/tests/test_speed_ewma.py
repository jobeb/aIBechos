"""La velocidad que se muestra no debe dar botes (core/speed_ewma.py).

Medido en una subida real ANTES de esto: la cifra saltaba de 8,3 a 1,7 MB/s de
un aviso al siguiente (un 98 % de la media), y además engañaba -- mostraba 5,7
MB/s de media cuando el caudal real era 4,4.
"""

from core.speed_ewma import SpeedEWMA


def test_la_primera_lectura_se_muestra_tal_cual():
    """Sin esto la cifra arrancaría en cero y subiría en rampa hasta la real,
    aparentando una lentitud que no existe."""
    m = SpeedEWMA(tau=2.0)
    assert m.update(8.0, ahora=0.0) == 8.0


def test_una_lectura_suelta_disparatada_no_dispara_la_cifra():
    """El caso que motivó todo: con el archivo repartido entre conexiones, los
    avisos llegan a ráfagas y un tramo recoge el trabajo de varias."""
    m = SpeedEWMA(tau=2.0)
    m.update(5.0, ahora=0.0)
    tras_el_pico = m.update(20.0, ahora=0.4)
    assert 5.0 < tras_el_pico < 8.0        # se mueve, pero no se va al pico


def test_un_cambio_sostenido_si_se_acaba_viendo():
    """No vale con aplanarlo todo: si de verdad baja la velocidad (al activar
    un límite, o al arrancar otro archivo), tiene que notarse."""
    m = SpeedEWMA(tau=2.0)
    m.update(10.0, ahora=0.0)
    t = 0.0
    for _ in range(25):                    # 10 s de lecturas cada 0,4 s
        t += 0.4
        m.update(2.0, ahora=t)
    assert abs(m.valor - 2.0) < 0.2


def test_las_lecturas_largas_pesan_mas_que_las_cortas():
    """Es lo que hace que la media sea el caudal DE VERDAD: si todas pesaran
    igual, los tramos cortos con ráfaga inflarían la cifra (pasaba: 5,7
    mostrados frente a 4,4 reales)."""
    corta = SpeedEWMA(tau=2.0)
    corta.update(4.0, ahora=0.0)
    corta.update(10.0, ahora=0.05)         # tramo muy corto

    larga = SpeedEWMA(tau=2.0)
    larga.update(4.0, ahora=0.0)
    larga.update(10.0, ahora=3.0)          # tramo largo

    assert larga.valor > corta.valor


def test_dos_lecturas_a_la_vez_no_rompen_nada():
    """Con varias conexiones pueden llegar dos avisos con el mismo instante."""
    m = SpeedEWMA(tau=2.0)
    m.update(5.0, ahora=1.0)
    assert m.update(9.0, ahora=1.0) == 5.0     # sin tiempo, sin cambio


def test_al_reiniciar_olvida_la_subida_anterior():
    m = SpeedEWMA(tau=2.0)
    m.update(9.0, ahora=0.0)
    m.reset()
    assert m.valor == 0.0
    assert m.update(1.0, ahora=5.0) == 1.0     # vuelve a sembrar
