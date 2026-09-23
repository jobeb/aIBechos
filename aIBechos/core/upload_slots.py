"""
Límite de subidas FTP simultáneas compartido entre el modo automático
(AutoWatcher) y la cola de subida manual de la GUI. Sin esto, cada uno usa su
propia conexión y "ftp_parallel" solo limitaba el pool de la subida manual —
el modo automático podía subir en paralelo a lo que estuviera subiendo a
mano, ignorando el límite configurado por el usuario.
"""

import threading


class UploadSlotManager:
    """Cupo dinámico de subidas activas. El límite se relee de la config en
    cada acquire(), así que cambiarlo en Ajustes se aplica de inmediato a
    las próximas subidas (no hace falta reiniciar ni relanzar nada)."""

    def __init__(self, config):
        self.config = config
        self._cond = threading.Condition()
        self._active = 0
        # FIFO real: cada solicitante recibe un número de turno correlativo,
        # y solo puede pasar quien tenga el turno más bajo pendiente -- sin
        # esto, con varias subidas esperando cupo a la vez (varios archivos
        # detectados de golpe por el modo automático, cada uno en su propio
        # hilo de identificación con TMDB que tarda lo que tarde), el orden
        # real de subida dependía de qué hilo ganara la carrera al
        # despertar, no del orden de la lista/carpeta.
        self._next_ticket   = 0
        self._next_to_serve = 0
        self._cancelled_tickets = set()

    def _max_slots(self) -> int:
        try:
            return max(1, min(5, int(self.config.get("ftp_parallel", 1))))
        except Exception:
            return 1

    def reserve_ticket(self) -> int:
        """Reserva un número de turno YA, sin esperar cupo -- para fijar el
        orden de subida en el momento en que se decide encolar un archivo
        (p.ej. al detectarlo, en el orden en que aparece en la carpeta),
        en vez de en el momento en que su hilo llega a acquire(), que puede
        variar según cuánto tarde su propia identificación TMDB. Pásalo
        luego a acquire(ticket=...)."""
        with self._cond:
            ticket = self._next_ticket
            self._next_ticket += 1
            return ticket

    def _advance_past_cancelled(self):
        while self._next_to_serve in self._cancelled_tickets:
            self._cancelled_tickets.discard(self._next_to_serve)
            self._next_to_serve += 1

    def _cancel_ticket_locked(self, ticket: int):
        """Ya con self._cond agarrado: da por perdido *ticket* sin que
        llegue a consumir cupo. Debe llamarse SIEMPRE que un ticket
        reservado con reserve_ticket() no vaya a pasar por acquire() de
        verdad -- si no, el contador de turnos se queda parado en ese
        ticket para siempre, bloqueando a cualquiera con un ticket mayor
        aunque haya cupos libres."""
        if ticket == self._next_to_serve:
            self._next_to_serve += 1
            self._advance_past_cancelled()
        else:
            self._cancelled_tickets.add(ticket)
        self._cond.notify_all()

    def release_ticket_unused(self, ticket: int = None):
        """Libera un ticket reservado con reserve_ticket() que al final no
        va a pasar por acquire() -- p.ej. el archivo no llegó a necesitar
        subir (falló la identificación, sin resultados TMDB, confianza
        insuficiente, sin categoría FTP configurada...). Sin esto, ver el
        docstring de _cancel_ticket_locked: la cola entera se queda
        bloqueada para siempre en cuanto CUALQUIER archivo falla antes de
        llegar a subir, que es el caso más común. No-op si ticket es None
        (nunca se reservó ninguno)."""
        if ticket is None:
            return
        with self._cond:
            self._cancel_ticket_locked(ticket)

    def acquire(self, cancel_event=None, ticket: int = None) -> bool:
        """Bloquea hasta que haya un cupo libre Y le toque el turno (FIFO
        por orden de llegada, o el *ticket* ya reservado con
        reserve_ticket() si se pasa uno). Devuelve False si cancel_event se
        activó mientras esperaba (no se reservó cupo) -- en ese caso, si a
        ese ticket todavía no le había tocado el turno, queda marcado como
        cancelado para que no bloquee a los que vienen detrás en la cola."""
        with self._cond:
            if ticket is None:
                ticket = self._next_ticket
                self._next_ticket += 1
            while True:
                self._advance_past_cancelled()
                if cancel_event is not None and cancel_event.is_set():
                    self._cancel_ticket_locked(ticket)
                    return False
                if ticket == self._next_to_serve and self._active < self._max_slots():
                    self._active += 1
                    self._next_to_serve += 1
                    self._cond.notify_all()
                    return True
                self._cond.wait(timeout=0.5)

    def release(self):
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify_all()
