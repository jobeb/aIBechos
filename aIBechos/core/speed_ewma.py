"""Velocidad suavizada, para que la cifra que se ve no dé botes.

La velocidad de una subida se mide por tramos: bytes enviados desde la última
vez, dividido por el tiempo que ha pasado. Esa cifra cruda es muy nerviosa, y
al repartir un archivo entre varias conexiones se vuelve inservible: los avisos
llegan a ráfagas (varias conexiones terminan su bloque casi a la vez), así que
un tramo se queda casi vacío y el siguiente recoge el trabajo de todos. Medido
en una subida real: saltos de 8,3 a 1,7 MB/s de un aviso al siguiente.

Además esa cifra cruda **engaña**, y no solo por nerviosa: como cada muestra
pesa igual dure lo que dure su tramo, los tramos cortos con ráfaga cuentan
tanto como los largos y la media sale por encima de la realidad (5,7 MB/s
mostrados frente a 4,4 reales en esa misma subida).

Esta media exponencial arregla las dos cosas: da más peso a las muestras que
cubren más tiempo, así que la cifra converge al caudal de verdad.

Se descartó la alternativa de una media sobre los últimos N segundos porque da
saltos de escalón cada vez que una muestra vieja sale de la ventana -- justo lo
que se quiere evitar -- y la mediana porque, aunque aplana los picos, no ve un
cambio sostenido, que sí hay que mostrar (al activar un límite de velocidad, o
al empezar o terminar otro archivo).
"""

import math


class SpeedEWMA:
    """Media exponencial de velocidad, ponderada por el tiempo real.

    *tau* es la constante de tiempo en segundos: cuánto tarda en reflejar un
    cambio sostenido (a los *tau* segundos ha recorrido el 63 % del camino, y
    a los 3x *tau* el 95 %). Más alto se ve más quieto pero reacciona tarde.
    """

    def __init__(self, tau: float = 2.0):
        self.tau = max(0.001, float(tau))
        self._valor = None
        self._ultimo = None

    @property
    def valor(self) -> float:
        """La velocidad suavizada actual (0.0 mientras no haya nada)."""
        return 0.0 if self._valor is None else self._valor

    def update(self, muestra: float, ahora: float) -> float:
        """Añade una lectura tomada en el instante *ahora* y devuelve la media.

        La primera lectura se adopta tal cual en vez de arrastrarla desde cero:
        así la cifra no arranca en 0 ni sube en rampa hasta alcanzar la real."""
        if self._valor is None or self._ultimo is None:
            self._valor = float(muestra)
            self._ultimo = ahora
            return self._valor
        transcurrido = max(0.0, ahora - self._ultimo)
        self._ultimo = ahora
        # El peso sale del tiempo que cubre la muestra, no de un factor fijo:
        # es lo que hace que un tramo largo pese más que uno corto y que la
        # media acabe siendo el caudal de verdad y no un promedio de picos.
        peso = 1.0 - math.exp(-transcurrido / self.tau)
        self._valor += peso * (float(muestra) - self._valor)
        return self._valor

    def reset(self):
        """Olvida lo anterior (al terminar una tanda, para que la siguiente no
        arrastre la velocidad de la anterior)."""
        self._valor = None
        self._ultimo = None
