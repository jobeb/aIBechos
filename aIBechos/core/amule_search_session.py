"""
Búsqueda manual en aMule de la pestaña Descargas, sin interfaz.

aMule admite UNA sola conexión EC: abrir otra mata la que hubiera. Por eso,
mientras una búsqueda sondea resultados (hasta 60 s), las descargas que pida
el usuario no abren conexión propia -- se encolan y el hilo de la búsqueda las
envía por SU conexión entre sondeos. Sin búsqueda en curso, la descarga abre
su propia conexión. Es el mismo esquema que App._downloads_perform_search/
_downloads_drain_jobs/_downloads_do_download_worker de la versión Tk.

Todos los callbacks se llaman desde hilos de trabajo: quien los recibe se
encarga de pasar al hilo de la interfaz.
"""

from __future__ import annotations

import threading
import time

from core.applog import get_logger

_log = get_logger("aIBechos.gui", "app.log")

FILE_TYPES = {
    "Cualquiera": "", "Video": "Video", "Audio": "Audio", "Imagen": "Image",
    "Documento": "Doc", "Programa": "Pro", "Archivo": "Arc", "ISO": "Iso",
}
NETWORKS = ["Kad", "Global", "Local"]
SEARCH_MAX_S = 60.0
SEARCH_POLL_S = 5.0
JOB_WAIT_S = 45.0


class AmuleSearchSession:
    def __init__(self, ec_lock, open_ec):
        """*open_ec()* devuelve un EcClient conectado o None (llamar bajo
        *ec_lock*), ver DownloadsCoreMixin._downloads_open_ec."""
        self.ec_lock = ec_lock
        self.open_ec = open_ec
        self._token = 0
        self._jobs: list = []
        self._jobs_lock = threading.Lock()
        self._wake = threading.Event()
        self.active = False
        self.started_ts = 0.0

    # ── Búsqueda ──

    def start(self, query: str, search_type: str, file_type: str,
              on_results, on_done, on_error) -> int:
        """Lanza la búsqueda (invalida la anterior). on_results(lista) en cada
        sondeo; on_done() al acabar; on_error(texto) si falla. Devuelve el
        token de esta búsqueda."""
        self._token += 1
        token = self._token

        def worker():
            ec = None
            try:
                with self.ec_lock:
                    ec = self.open_ec()
                    if ec is None:
                        if token == self._token:
                            on_error("no se pudo conectar con aMule")
                        return
                    self.active = True
                    self.started_ts = time.monotonic()
                    self._wake.clear()
                    try:
                        for results in ec.iter_search(query, search_type=search_type, file_type=file_type,
                                                      poll_interval=SEARCH_POLL_S, max_duration=SEARCH_MAX_S,
                                                      wake_event=self._wake):
                            if token != self._token:
                                return
                            self._drain_jobs(ec)
                            on_results(results)
                        self._drain_jobs(ec)
                    finally:
                        with self._jobs_lock:
                            self.active = False
                            for job in self._jobs:
                                if job["ok"] is None:
                                    job["done"].set()   # que la descarga lo intente por su cuenta
            except Exception as e:
                if token == self._token:
                    on_error(str(e))
            finally:
                if ec is not None:
                    try:
                        ec.close()
                    except Exception:
                        pass
            if token == self._token:
                on_done()
        threading.Thread(target=worker, daemon=True).start()
        return token

    def stop(self):
        """Parar la búsqueda en curso: el hilo sale en el siguiente sondeo."""
        self._token += 1
        self._wake.set()
        with self._jobs_lock:
            self.active = False

    def is_current(self, token: int) -> bool:
        return token == self._token

    def _drain_jobs(self, ec):
        with self._jobs_lock:
            jobs = list(self._jobs)
        for job in jobs:
            if job["ok"] is not None:
                continue
            try:
                ok, raw = ec.download(job["result"])
            except Exception as e:
                ok, raw = False, str(e)
            job["ok"], job["raw"] = ok, raw
            job["done"].set()
            with self._jobs_lock:
                try:
                    self._jobs.remove(job)
                except ValueError:
                    pass

    # ── Descarga de un resultado ──

    def download(self, result) -> tuple:
        """(ok, respuesta_cruda). Bloquea: llamar desde un hilo."""
        with self._jobs_lock:
            via_search = self.active
            if via_search:
                job = {"result": result, "ok": None, "raw": "", "done": threading.Event()}
                self._jobs.append(job)
                self._wake.set()
        if via_search:
            job["done"].wait(timeout=JOB_WAIT_S)
            if job["ok"] is not None:
                return job["ok"], job["raw"]
        try:
            with self.ec_lock:
                ec = self.open_ec()
                if ec is None:
                    return False, "no se pudo conectar con aMule"
                try:
                    return ec.download(result)
                finally:
                    try:
                        ec.close()
                    except Exception:
                        pass
        except Exception as e:
            return False, str(e)
