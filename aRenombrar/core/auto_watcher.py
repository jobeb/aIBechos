"""
Monitor automático de carpeta.
Recorre la carpeta vigilada de forma recursiva (subcarpetas incluidas) y
detecta vídeo (identificado con TMDB), libros/cómics (identificados con
Google Books/ComicVine, ver core/book_identify.py) y, si
"auto_extract_archives" está activado en Ajustes, archivos comprimidos
(.zip/.7z/.rar/.tar, ver core/archive_extract.py) que descomprime antes de que
el siguiente ciclo de escaneo identifique lo que haya salido de ahí. Todo
lo identificado se renombra y se sube por FTP.

Excepciones manejadas:
  - Archivo no reconocido (ni vídeo, ni libro/cómic, ni comprimido) → ignorado silenciosamente
  - Ya procesado anteriormente → saltado
  - No identificable (TMDB/ComicVine/Google Books) → marcado como 'sin_resultados', notificado
  - FTP no configurado         → renombrado pero no subido, notificado
  - Archivo ya subido          → saltado
"""

import difflib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from core.adult_content import adult_reason, looks_adult
from core.api_client import detect_episode
from core.renamer import (build_name_for_media_info, rename_file,
                           is_video_file, is_book_file, is_comic_file, is_archive_file)
from core.ftp_client import FTPClient, _ftp_safe
from core.series_match import best_match
from core.ftp_categories import choose_category
from core.upload_slots import UploadSlotManager
from core.appdirs import app_data_dir
from core.applog import get_logger


def _app_dir() -> Path:
    return app_data_dir()


def _processed_db_path() -> Path:
    return _app_dir() / "auto_processed.json"


# Serializa TODAS las lecturas+escrituras de auto_processed.json, tanto las
# de AutoWatcher (_save_entry/_delete_entry) como las de la GUI para subidas
# manuales (gui/app.py::_mark_auto_processed/_unmark_auto_processed/
# _cleanup_stale_uploading_marks, que importan este mismo lock) -- sin esto,
# dos hilos que hacen su propio leer-modificar-escribir sin coordinarse (p.ej.
# dos subidas manuales en paralelo terminando casi a la vez con "Subidas
# simultáneas" > 1, o una subida manual y AutoWatcher a la vez) pueden pisarse:
# el segundo en escribir parte de una lectura ya desactualizada y borra sin
# querer la marca que el primero acababa de guardar. Visto de verdad: archivos
# recién subidos a mano "reaparecían" como nuevos para AutoWatcher.
_DB_LOCK = threading.Lock()

_log = get_logger("aIBechos.auto", "auto_watcher.log", level=logging.DEBUG)


def mark_discarded(path: str) -> bool:
    """Marca *path* como "descartado" para que AutoWatcher lo deje en paz.

    La llama la GUI cuando el usuario quita una fila a mano ("este archivo
    no lo quiero"): sin esto el siguiente escaneo lo vuelve a detectar,
    dispara el evento "start" y la fila reaparece sola, con lo que quitarla
    se vuelve imposible.

    Vive aquí, y no dentro de la clase App, por dos razones: la GUI estaba
    duplicando el formato de las entradas y la lista de estados protegidos
    (dos sitios que tenían que cambiar a la vez sin que nada lo comprobara),
    y App no se puede instanciar en un test, así que el escritor no tenía
    NINGUNA cobertura -- solo la tenía el lector.

    Devuelve True si ha escrito la marca, False si no hacía falta porque el
    archivo ya tenía un estado protegido ("subido", "identificado_manual"...):
    esos ya hacen que el watcher lo ignore y llevan información que no
    conviene perder.
    """
    try:
        with _DB_LOCK:
            p = _processed_db_path()
            db = {}
            if p.exists():
                try:
                    db = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    db = {}
            if db.get(path, {}).get("status", "") in _PROTECTED_STATUSES:
                return False
            db[path] = {"status": "descartado", "new_name": "", "ts": time.time()}
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
            _log.info("Descartado a mano desde la lista: %s", Path(path).name)
            return True
    except Exception:
        _log.warning("No se pudo marcar como descartado: %s", path, exc_info=True)
        return False

STABLE_WAIT  = 6    # segundos esperando que el archivo deje de crecer
DEFAULT_POLL = 10   # segundos entre escaneos (por defecto)

# Estados que solo escribe la GUI cuando el usuario interviene a mano
# (identificar, renombrar, subir manualmente -- ver
# gui/app.py::_mark_auto_processed). AutoWatcher nunca debe pisarlos con un
# resultado propio: ver _mark(). "descomprimido" es la excepción -- lo
# escribe el propio AutoWatcher (_process_archive), pero necesita el mismo
# tratamiento: sin esto, un archivo comprimido con "Mantener original"
# configurado (no se mueve ni se borra) se detectaría y descomprimiría de
# nuevo en cada ciclo de escaneo.
# "adulto_omitido" va aquí (y no entre los estados que se reintentan) a
# propósito: no es un fallo de identificación que pueda arreglarse en el
# siguiente ciclo, es una decisión firme. Reintentarlo solo serviría para
# volver a intentar clasificar porno solo, que es justo lo que no se quiere.
_PROTECTED_STATUSES = ("subido", "renombrado", "identificado_manual", "subiendo", "duplicado",
                       "descomprimido", "descartado", "adulto_omitido")

# Veces que se reintenta un archivo que falló por causas NO protegidas
# (sin_resultados, confianza insuficiente...) antes de dejarlo en paz.
# Sin este tope, _should_process borraba la marca y lo reprocesaba en CADA
# ciclo de escaneo, para siempre: visto de verdad con una película que
# sencillamente no existe en TMDB (una parodia), que acumuló >2000 entradas
# de log reintentándose cada 5-10 s, gastando cuota de la API y volviendo a
# aparecer en la lista de Archivos cada vez que el usuario la quitaba. Los
# fallos transitorios de verdad (TMDB caído, corte de red) se resuelven de
# sobra en los primeros intentos; a partir de ahí insistir no aporta nada.
# Renombrar el archivo cambia su clave y vuelve a empezar de cero.
_MAX_RETRY_ATTEMPTS = 3

# Fragmentos (en minúsculas) que delatan "archivo bloqueado por otro
# proceso" en el mensaje de error de rename_file(), sea cual sea el SO:
# Windows (WinError 32, en español o inglés) o POSIX (EBUSY/EACCES).
_LOCKED_FILE_HINTS = (
    "32", "utilizado", "being used", "locked",
    "resource busy", "ebusy", "device or resource busy",
    "permission denied", "acceso denegado",
)


class AutoWatcher:
    def __init__(self, folder: str, config, tmdb_client, ftp_client, on_event, on_file_event=None,
                 upload_slots=None, ftp_lock=None, ftp_factory=None,
                 comicvine_client=None, book_client=None, openlibrary_client=None):
        """
        on_event(tipo, mensaje)
          tipo: "info" | "ok" | "skip" | "error"
        on_file_event(path, tipo, **kwargs)
          tipos: "start" | "renamed" | "queued" | "uploading" | "uploaded" | "skip" | "error"
          kwargs: new_name, progress, speed, size (bytes, solo en "uploaded" --
          ver _save_history_entry, necesario para que el historial/las
          estadísticas compartidas no cuenten esta subida como 0 B),
          remote_full (ruta completa en el servidor, solo en "uploaded")
        upload_slots: UploadSlotManager compartido con la subida manual de la
          GUI, para que "Subidas simultáneas" limite el total real entre
          ambos orígenes. Si no se pasa (uso standalone/tests), se crea uno
          propio — sigue limitando el modo automático consigo mismo.
        ftp_lock: candado compartido con quien sea que use *ftp_client* fuera
          de AutoWatcher (ver gui/app.py::App -- self._ftp_cmd_lock). Si no
          se pasa (uso standalone/tests), se crea uno propio -- pero entonces
          solo protege a AutoWatcher de sí mismo, no de otros usuarios
          concurrentes de la misma conexión.
        ftp_factory: fábrica sin argumentos para crear la conexión FTP
          DEDICADA de cada transferencia real (ver _upload_to_ftp) -- nunca
          self.ftp, que es solo para comandos ligeros de control. Por
          defecto crea una FTPClient() nueva de verdad; los tests pasan un
          MagicMock aquí para no abrir conexiones reales.
        comicvine_client/book_client/openlibrary_client: mismos
          ComicVineClient/GoogleBooksClient/OpenLibraryClient que usa la
          identificación manual (ver gui/app.py, core/book_identify.py).
          Opcionales -- si no se pasa ninguno de los que corresponda al tipo
          de archivo (comicvine_client para cómics; openlibrary_client y/o
          book_client para ebooks -- OpenLibrary es el proveedor PRINCIPAL,
          Google Books queda de apoyo automático), los archivos de ese tipo
          se detectan pero no se pueden identificar (ver _process).
        """
        self.folder             = Path(folder)
        self.config             = config
        self.tmdb               = tmdb_client
        self.ftp                = ftp_client
        self.comicvine          = comicvine_client
        self.book_client        = book_client
        self.openlibrary_client = openlibrary_client
        self.on_event           = on_event
        self.on_file_event      = on_file_event or (lambda *a, **kw: None)
        self.upload_slots       = upload_slots or UploadSlotManager(config)
        self._ftp_factory       = ftp_factory or FTPClient
        self.poll_interval      = int(config.get("poll_interval", DEFAULT_POLL))
        self._stop              = threading.Event()
        self._thread            = None
        self._processed         = self._load_db()
        self._in_progress       = set()
        # {clave: nº de reintentos ya gastados} -- puente entre
        # _should_process (que borra la entrada para reprocesar) y _mark
        # (que la vuelve a escribir), ver _MAX_RETRY_ATTEMPTS.
        self._pending_attempts  = {}
        # Claves cuyo "Saltado (...)" ya se escribió en el log durante esta
        # sesión. Sin esto, _should_process escribía una línea por archivo ya
        # procesado en CADA ciclo de escaneo (80 archivos cada 10 s = 480
        # líneas/minuto): auto_watcher.log llegaba a los 2 MB de rotación en
        # unos 10 minutos y se llevaba por delante todo el historial útil
        # (subidas, errores, renombrados) justo cuando hacía falta para
        # diagnosticar algo. El motivo del salto no cambia entre ciclos, así
        # que con registrarlo una vez por archivo sobra.
        self._logged_skips      = set()
        # Reutilización de carpetas de serie ya existentes en el FTP (evita
        # crear una carpeta duplicada por idioma/nombre corto distinto)
        self._series_folder_cache = {}
        self._ftp_dir_cache       = {}
        # self.ftp es UNA conexión de control compartida por todos los hilos
        # de _process() (uno por archivo nuevo detectado en un mismo escaneo)
        # Y, normalmente, también con quien construya este AutoWatcher (ver
        # ftp_lock arriba); FTP es un protocolo secuencial sobre un único
        # socket, así que hay que serializar cualquier uso de self.ftp entre
        # TODOS esos hilos con el mismo candado, no solo entre los propios.
        self._ftp_lock = ftp_lock or threading.Lock()

    # ── Ciclo de vida ──────────────────────────────────────────────────────────

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="AutoWatcher")
        self._thread.start()

    def stop(self):
        self._stop.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Bucle principal ────────────────────────────────────────────────────────

    def _loop(self):
        _log.info("=== AutoWatcher iniciado | carpeta: %s | poll: %ss ===", self.folder, self.poll_interval)
        try:
            self.on_event("info", f"Modo automático activo — vigilando: {self.folder}")
        except Exception:
            # Sin este try/except, un fallo aquí (antes incluso de entrar
            # en el bucle) mataba el hilo en silencio: el botón ya se había
            # puesto en "Detener" (ver App._toggle_auto, que lo actualiza
            # justo después de start(), sin esperar a que el hilo
            # sobreviva su primera línea) y se quedaba así para siempre,
            # aunque el modo automático llevara muerto desde el primer
            # instante -- mismo patrón que ya se aplicó al bucle de abajo.
            _log.exception("Error inesperado al avisar del arranque de AutoWatcher")
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception as e:
                _log.exception("Error inesperado en _scan")
                self.on_event("error", f"Error en escaneo: {e}")
            self._stop.wait(self.poll_interval)
        _log.info("=== AutoWatcher detenido ===")
        self.on_event("info", "Modo automático detenido")

    def _iter_watch_files(self):
        """Recorre self.folder de forma recursiva (subcarpetas incluidas),
        excluyendo cualquier subcarpeta llamada "procesados" a cualquier
        profundidad -- ahí es donde acaban los archivos ya subidos o
        descomprimidos (ver acción post-proceso en _upload_to_ftp/
        _process_archive); sin esta exclusión, cada escaneo los volvería a
        ver y reprocesar."""
        for dirpath, dirnames, filenames in os.walk(self.folder):
            dirnames[:] = [d for d in dirnames if d != "procesados"]
            for name in filenames:
                yield Path(dirpath) / name

    def _log_skip_once(self, key: str, reason: str, name: str):
        """Escribe "Saltado (motivo): archivo" solo la primera vez que ese
        archivo se salta por ese motivo (ver _logged_skips). Si el motivo
        cambia (p. ej. "en proceso" -> "subido") vuelve a registrarse, que
        eso sí es información nueva."""
        mark = (key, reason)
        if mark in self._logged_skips:
            return
        self._logged_skips.add(mark)
        _log.debug("Saltado (%s): %s", reason, name)

    def _should_process(self, key: str, name: str) -> bool:
        """Comprueba _in_progress/_processed y decide si *key* debe
        procesarse ahora -- mismo criterio para vídeo/libro/cómic
        (_process) y para archivos comprimidos (_process_archive)."""
        if key in self._in_progress:
            self._log_skip_once(key, "en proceso", name)
            return False
        if key in self._processed:
            status = self._processed[key].get("status", "")
            if status in _PROTECTED_STATUSES:
                # "renombrado", "identificado_manual" y "subiendo" los
                # escribe la GUI cuando el usuario renombra/identifica/sube
                # el archivo a mano (ver _mark_auto_processed) — justamente
                # para que el watcher lo deje en paz. Si se reprocesara
                # aquí, este hilo competiría por el mismo archivo con la
                # acción manual del usuario: desde renombrarlo de nuevo
                # mientras está abierto para subir ("WinError 32: en uso
                # por otro proceso" en bucle) hasta pisar el estado
                # "Listo"/"Omitido" o interrumpir una subida en marcha si
                # el modo automático se activa a mitad. "subiendo" se
                # quita solo si la subida manual no termina bien (ver
                # _unmark_auto_processed), así que no se queda bloqueado
                # para siempre si algo falla o se cierra la app a medias.
                # "duplicado" (detectado por find_duplicate, ver más abajo)
                # y "descomprimido" (ver _process_archive) son estados
                # deliberados, no un fallo -- sin esto en la lista, cada
                # escaneo lo trataba como "hay que reintentar" y volvía a
                # detectar/marcar el mismo resultado sin parar (visto de
                # verdad con Dr. Stone 4x32, que "reaparecía" cada vez que
                # se activaba el automático).
                self._log_skip_once(key, status, name)
                return False
            # Estado no exitoso (fallo del propio AutoWatcher) → reprocesar,
            # pero solo unas cuantas veces (ver _MAX_RETRY_ATTEMPTS): hay
            # archivos que NUNCA van a identificarse (un título que no existe
            # en TMDB), y reintentarlos en cada ciclo era un bucle infinito.
            attempts = self._processed[key].get("attempts", 0)
            if attempts >= _MAX_RETRY_ATTEMPTS:
                # Tras actualizar el filtro italiano, un archivo con baja_confianza
                # previo (3 intentos) debe poder reintentarse si sigue en la carpeta
                # y ha pasado un tiempo prudencial, en vez de quedar bloqueado para siempre
                ts = float(self._processed[key].get("ts", 0) or 0)
                if ts and (time.time() - ts) < 24 * 3600:
                    self._log_skip_once(key, f"{status}, {attempts} intentos agotados", name)
                    return False
                _log.info("Reintentando tras 24h pese a intentos agotados (filtro actualizado): %s", name)
            else:
                _log.info("Reprocesando (estado anterior=%s, intento %d/%d): %s",
                          status, attempts + 1, _MAX_RETRY_ATTEMPTS, name)
            # La cuenta de intentos NO se borra con la entrada -- se lleva al
            # siguiente _mark() (ver _pending_attempts), o el contador se
            # reiniciaría solo y el tope no serviría de nada.
            self._pending_attempts[key] = attempts + 1
            del self._processed[key]
            self._delete_entry(key)
        return True

    def _scan(self):
        if not self.folder.exists():
            _log.error("Carpeta no encontrada: %s", self.folder)
            self.on_event("error", f"Carpeta vigilada no encontrada: {self.folder}")
            return

        # Recargar DB de disco para incluir archivos marcados por la UI manual
        self._processed.update(self._load_db())

        extract_enabled = bool(self.config.get("auto_extract_archives", False))
        media_items   = []
        archive_items = []
        for item in sorted(self._iter_watch_files()):
            if self._stop.is_set():
                break
            path_str = str(item)
            if is_video_file(path_str) or is_book_file(path_str):
                media_items.append(item)
            elif extract_enabled and is_archive_file(path_str):
                archive_items.append(item)
            else:
                _log.debug("Ignorando (tipo no reconocido): %s", item.name)

        _log.debug("Scan: %d archivo(s) de vídeo/libro/cómic, %d comprimido(s) | in_progress=%d | processed=%d",
                   len(media_items), len(archive_items), len(self._in_progress), len(self._processed))

        for item in media_items:
            key = str(item)
            if not self._should_process(key, item.name):
                continue
            _log.info("NUEVO ARCHIVO DETECTADO: %s", item.name)
            self.on_event("info", f"Nuevo archivo detectado: {item.name}")
            self._in_progress.add(key)
            # Turno de subida reservado AQUÍ, en el bucle de _scan() -- de un
            # solo hilo, así que el orden queda fijado sin ninguna carrera
            # posible. Se probó reservarlo dentro de _process(), justo antes
            # del evento "start" de cada hilo, pensando que así seguiría el
            # orden real de la tabla -- pero la propia espera de estabilidad
            # (STABLE_WAIT) no dura EXACTAMENTE lo mismo para archivos reales
            # (la E/S de disco varía), así que dos hilos lanzados casi a la
            # vez podían terminarla en cualquier orden: visto de verdad,
            # un archivo detectado 4º subió antes que uno detectado 3º.
            # Aquí, en cambio, no hay hilos todavía -- el orden es el de
            # media_items (ya ordenado más arriba), sin más.
            ticket = self.upload_slots.reserve_ticket()
            threading.Thread(
                target=self._process, args=(item, ticket), daemon=True
            ).start()

        for item in archive_items:
            key = str(item)
            if not self._should_process(key, item.name):
                continue
            # No reserva turno de "Subidas simultáneas" -- un archivo
            # comprimido no se sube él mismo, solo se descomprime.
            _log.info("ARCHIVO COMPRIMIDO DETECTADO: %s", item.name)
            self.on_event("info", f"Archivo comprimido detectado: {item.name}")
            self._in_progress.add(key)
            threading.Thread(
                target=self._process_archive, args=(item,), daemon=True
            ).start()

    def _is_stable(self, path: Path) -> bool:
        """Comprueba que el tamaño no cambia en STABLE_WAIT segundos."""
        try:
            s1 = path.stat().st_size
            if s1 == 0:
                return False
            time.sleep(STABLE_WAIT)
            s2 = path.stat().st_size
            return s1 == s2 and s2 > 0
        except OSError:
            return False

    def _apply_post_process_action(self, path: Path):
        """Acción post-proceso configurada ("auto_action" en Ajustes) --
        compartida entre una subida de vídeo/libro/cómic exitosa
        (_upload_to_ftp) y una descompresión exitosa (_process_archive):
        mantener el archivo, moverlo a la subcarpeta "procesados", o
        eliminarlo."""
        action = self.config.get("auto_action", "Mantener original")
        _log.debug("Acción post-proceso: %s", action)
        if action == "Mover a subcarpeta 'procesados'":
            try:
                dest_dir = path.parent / "procesados"
                dest_dir.mkdir(exist_ok=True)
                import shutil
                shutil.move(str(path), str(dest_dir / path.name))
                _log.info("Movido a procesados: %s", path.name)
            except Exception as e:
                _log.error("No se pudo mover: %s", e)
                self.on_event("skip", f"No se pudo mover archivo: {e}")
        elif action == "Eliminar original":
            try:
                if path.exists():
                    path.unlink()
                    _log.info("Eliminado: %s", path.name)
            except Exception as e:
                _log.error("No se pudo eliminar: %s", e)
                self.on_event("skip", f"No se pudo eliminar archivo: {e}")

    def _process_archive(self, path: Path):
        """Descomprime un archivo comprimido encontrado en la carpeta
        vigilada (ver "auto_extract_archives" en Ajustes) -- a diferencia
        de _process, no reserva turno de "Subidas simultáneas": un archivo
        comprimido no se sube él mismo, solo se descomprime. El contenido
        extraído queda como una carpeta hermana normal (ver
        core/archive_extract.py); el SIGUIENTE ciclo de escaneo recursivo
        lo descubre solo -- incluidos archivos comprimidos anidados dentro,
        que se resuelven en ciclos sucesivos sin ninguna lógica especial de
        recursión-de-extracción."""
        key = str(path)
        _log.info("--- Descomprimiendo: %s ---", path.name)
        try:
            if not self._is_stable(path):
                _log.warning("Archivo inestable o vacío, descartado: %s", path.name)
                return
            if not path.exists():
                _log.warning("Archivo desaparecido tras espera: %s", path.name)
                return
            if self._load_db().get(key, {}).get("status", "") in _PROTECTED_STATUSES:
                _log.info("Abandonando: %s ya se gestionó mientras se esperaba", path.name)
                return

            from core.archive_extract import extract_archive
            ok, msg = extract_archive(path)
            if not ok:
                _log.error("No se pudo descomprimir %s: %s", path.name, msg)
                self.on_event("error", f"No se pudo descomprimir {path.name}: {msg}")
                self._mark(key, "error_descomprimir")
                return

            _log.info("Descomprimido OK: %s -> %s", path.name, msg)
            self.on_event("ok", f"Descomprimido: {path.name}")
            self._mark(key, "descomprimido")
            self._apply_post_process_action(path)
        except Exception as e:
            _log.exception("Error inesperado descomprimiendo: %s", path.name)
            self.on_event("error", f"Error inesperado descomprimiendo {path.name}: {e}")
        finally:
            self._in_progress.discard(key)

    def _try_ai_fallback(self, path: Path, detected: dict):
        """Igual que en gui/app.py::_try_ai_fallback -- último recurso
        cuando TMDB no encuentra nada con el título limpiado localmente. Solo
        aprende (persiste) los términos nuevos si de verdad ayudan a
        encontrar resultado, para no contaminar la lista con un despiste de
        la IA. Devuelve (results, detected) o None."""
        if not self.config.get("ai_fallback_enabled"):
            return None
        api_key = self.config.get("ai_api_key", "")
        if not api_key:
            return None
        from core.ai_title_fallback import guess_title_via_ai
        stem = re.sub(r"\.[a-zA-Z0-9]{2,4}$", "", path.name)
        ai_result = guess_title_via_ai(stem, api_key)
        if not ai_result:
            return None
        retry_detected = detect_episode(path.name, extra_junk_terms=ai_result["junk_tokens"])
        if not retry_detected.get("title"):
            return None
        retry_media_type = retry_detected.get("media_type", "tv")
        if retry_media_type == "tv":
            raw = self.tmdb.search_tv(retry_detected["title"])
            retry_results = [dict(r, media_type="tv") for r in raw]
        else:
            raw = self.tmdb.search_movie(retry_detected["title"])
            retry_results = [dict(r, media_type="movie") for r in raw]
        if not retry_results:
            retry_results = self.tmdb.search_multi(retry_detected["title"])
        if not retry_results:
            return None
        from core.learned_terms import add_learned_terms
        add_learned_terms(ai_result["junk_tokens"])
        _log.info("Fallback de IA aprendio terminos nuevos tras %s: %s",
                   path.name, ai_result["junk_tokens"])
        return retry_results, retry_detected

    # ── Procesado de un archivo ────────────────────────────────────────────────

    def _process(self, path: Path, ticket: int = None):
        key = str(path)
        _log.info("--- Procesando: %s ---", path.name)
        # El ticket ya viene reservado desde _scan() (de un solo hilo, sin
        # ninguna carrera). El finally de más abajo libera el ticket en
        # CUALQUIER salida de esta función (return normal o excepción) que
        # no haya llegado a entregárselo a _upload_to_ftp() -- que a partir
        # de la entrega pasa a ser el único responsable de liberarlo (ver su
        # propio try/finally) -- así que las salidas tempranas de aquí abajo
        # (archivo inestable, desaparecido, o ya gestionado a mano) no
        # necesitan liberarlo cada una por su cuenta.
        ticket_handed_off = False
        try:
            # Esperar a que el archivo deje de crecer (en este hilo, no en _scan)
            _log.debug("Esperando estabilidad (%ss): %s", STABLE_WAIT, path.name)
            if not self._is_stable(path):
                _log.warning("Archivo inestable o vacío, descartado: %s", path.name)
                self._in_progress.discard(key)
                return
            # Verificar que el archivo sigue existiendo tras la espera
            if not path.exists():
                _log.warning("Archivo desaparecido tras espera: %s", path.name)
                self.on_event("skip", f"Archivo desaparecido tras espera: {path.name}")
                self._in_progress.discard(key)
                return
            _log.debug("Archivo estable (%.1f MB): %s", path.stat().st_size / 1024**2, path.name)

            # Si mientras se esperaba la estabilidad (6s) -- o en un reintento
            # ya en marcha desde un ciclo de escaneo anterior -- el usuario
            # identificó este archivo a mano desde la GUI, no hay que ni
            # empezar a re-detectarlo. _mark() ya evita que el resultado
            # propio de este hilo LLEGUE A PERSISTIRSE, pero sin este chequeo
            # el evento de "Confianza insuficiente"/etc. se sigue disparando
            # igual, y la fila vuelve a verse "Omitido" un instante de más --
            # visto de verdad: hacía falta identificar el mismo archivo a
            # mano DOS veces para que la tabla dejara de parpadear.
            if self._load_db().get(key, {}).get("status", "") in _PROTECTED_STATUSES:
                _log.info("Abandonando proceso: %s ya se gestionó a mano mientras se esperaba", path.name)
                self._in_progress.discard(key)
                return

            # Contenido para adultos: fuera del automático, siempre.
            # detect_episode() tira "Xxx"/"A Porn Parody"/el reparto como
            # basura del nombre, así que a partir de aquí esa señal ya no
            # existe y lo que queda ("Star Wars") se parece peligrosamente a
            # títulos legítimos. Caso real: dos parodias porno archivadas en
            # /datos2/seriespeques/ por casar con una serie preescolar.
            # Se marca con estado protegido para que no se reintente en
            # bucle; el usuario decide a mano qué hacer con él.
            if looks_adult(path.name):
                motivo = adult_reason(path.name)
                _log.warning("Contenido adulto detectado (%s), fuera del automático: %s",
                             motivo, path.name)
                self.on_event("skip", f"Contenido adulto ({motivo}), no se clasifica solo: {path.name}")
                self.on_file_event(key, "skip",
                                    reason=f"Contenido para adultos detectado ({motivo}). "
                                           f"Clasifícalo a mano para que no acabe en una carpeta equivocada.")
                self._mark(key, "adulto_omitido")
                return

            self.on_file_event(key, "start")
            self.on_event("info", f"Identificando: {path.name}")

            is_book  = is_book_file(str(path))
            is_comic = is_comic_file(str(path)) if is_book else False

            # 1. Detección local (patrones de nombre) -- folder_hint: apoyo
            # para series/mangas cuyos capítulos vienen numerados a secas y
            # el nombre real está puesto en la carpeta que los contiene (ver
            # core/api_client.py::detect_episode).
            detected = detect_episode(path.name, is_book=is_book, is_comic=is_comic,
                                       folder_hint=path.parent.name)
            _log.debug("Detección local: %s", detected)
            if not detected.get("title"):
                _log.warning("Sin patrón reconocible: %s", path.name)
                self.on_event("skip", f"No identificado (sin patrón): {path.name}")
                self.on_file_event(key, "skip",
                                    reason="No se reconoció ningún patrón de serie/película en el nombre del archivo")
                self._mark(key, "no_identificado")
                return

            season  = detected.get("season")
            episode = detected.get("episode")
            min_conf = int(self.config.get("min_confidence", 70))

            if is_book:
                # 2. Identificación vía ComicVine (cómics) u OpenLibrary con
                # Google Books de apoyo (ebooks de texto) -- misma lógica
                # exacta que el panel manual de Archivos (incluida la
                # traducción de título vía IA + caché para ComicVine), ver
                # core/book_identify.py.
                if is_comic and self.comicvine is None:
                    _log.warning("Sin cliente de ComicVine configurado: %s", path.name)
                    self.on_event("skip", f"Sin cliente de ComicVine configurado: {path.name}")
                    self.on_file_event(key, "skip", reason="AutoWatcher no tiene configurado ComicVine")
                    self._mark(key, "sin_resultados")
                    return
                if not is_comic and self.openlibrary_client is None and self.book_client is None:
                    _log.warning("Sin cliente de OpenLibrary/Google Books configurado: %s", path.name)
                    self.on_event("skip", f"Sin cliente de OpenLibrary/Google Books configurado: {path.name}")
                    self.on_file_event(key, "skip",
                                        reason="AutoWatcher no tiene configurado OpenLibrary/Google Books")
                    self._mark(key, "sin_resultados")
                    return
                from core.book_identify import identify_book_or_comic
                result = identify_book_or_comic(
                    detected, is_comic, self.comicvine, self.book_client,
                    openlibrary_client=self.openlibrary_client,
                    ai_fallback_enabled=bool(self.config.get("ai_fallback_enabled")),
                    ai_api_key=self.config.get("ai_api_key", ""))
                if result.error:
                    _log.warning("%s: %s (%r)", result.error, path.name, result.used_query)
                    self.on_event("skip", f"{result.error}: {path.name}")
                    self.on_file_event(key, "skip", reason=result.error)
                    self._mark(key, "sin_resultados")
                    return
                confidence = result.confidence
                _log.debug("%s match | confianza=%d%% (mín=%d%%)",
                           "ComicVine" if is_comic else (result.provider or "libro"), confidence, min_conf)
                if min_conf > 0 and confidence < min_conf:
                    _log.warning("Confianza insuficiente (%d%% < %d%%): %s", confidence, min_conf, path.name)
                    self.on_event("skip", f"Confianza insuficiente ({confidence}% < {min_conf}%): {path.name}")
                    self.on_file_event(key, "skip",
                                        reason=f"Confianza insuficiente ({confidence}% < {min_conf}%)")
                    self._mark(key, "baja_confianza")
                    return
                media_info = result.media_info
            else:
                # 2. Búsqueda TMDB
                lang = self.config.get("language", "es-ES")
                media_type = detected.get("media_type", "tv")
                _log.debug("Buscando en TMDB: '%s' (tipo=%s, lang=%s)", detected["title"], media_type, lang)
                # media_type no era solo para el log: se le pasa a la
                # búsqueda para que los resultados de ESE tipo vayan
                # primero (ver TMDBClient.search_multi). Antes mandaba la
                # popularidad y "Amadeus 1x05" se identificaba como la
                # película "Amadeus (1984)".
                results = self.tmdb.search_multi(detected["title"], prefer_type=media_type)
                if not results:
                    # Intentar búsqueda alternativa con template por serie
                    from core.amule_search import build_amule_query
                    series_patterns = self.config.get("series_search_patterns")
                    if series_patterns and detected["title"] in series_patterns:
                        alt_query = build_amule_query(
                            detected["title"],
                            season,
                            episode,
                            templates=series_patterns,
                            prefers_castellano=True
                        )
                        if alt_query and alt_query != detected["title"]:
                            _log.debug("Usando query alternativa del patrón de serie: '%s'", alt_query)
                            # Usar el template para TMDB search
                            if media_type == "tv":
                                raw = self.tmdb.search_tv(alt_query)
                                results = [dict(r, media_type="tv") for r in raw]
                            else:
                                raw = self.tmdb.search_movie(alt_query)
                                results = [dict(r, media_type="movie") for r in raw]
                            if results:
                                _log.debug("Query alternativa exitosa (patrón de serie)")
                            else:
                                _log.debug("Query alternativa falló (patrón de serie)")

                    # Intentar búsqueda específica (original)
                    if media_type == "tv":
                        raw = self.tmdb.search_tv(detected["title"])
                        results = [dict(r, media_type="tv") for r in raw]
                    else:
                        raw = self.tmdb.search_movie(detected["title"])
                        results = [dict(r, media_type="movie") for r in raw]

                if not results:
                    fallback = self._try_ai_fallback(path, detected)
                    if fallback:
                        results, detected = fallback
                        media_type = detected.get("media_type", media_type)
                        season    = detected.get("season")
                        episode   = detected.get("episode")

                if not results:
                    _log.warning("Sin resultados TMDB para: %s", path.name)
                    self.on_event("skip", f"Sin resultados TMDB: {path.name}")
                    self.on_file_event(key, "skip",
                                        reason=f"Sin resultados en TMDB para '{detected['title']}'")
                    self._mark(key, "sin_resultados")
                    return

                result = results[0]

                # Comprobar confianza mínima
                result_title = (result.get("name", "") or result.get("title", "")).lower()
                confidence   = round(difflib.SequenceMatcher(
                    None, detected["title"].lower(), result_title).ratio() * 100)
                _log.debug("TMDB match: '%s' | confianza=%d%% (mín=%d%%)", result_title, confidence, min_conf)
                if min_conf > 0 and confidence < min_conf:
                    _log.warning("Confianza insuficiente (%d%% < %d%%): %s → '%s'",
                                 confidence, min_conf, path.name, result_title)
                    self.on_event("skip",
                        f"Confianza insuficiente ({confidence}% < {min_conf}%): {path.name} → '{result_title}'")
                    self.on_file_event(key, "skip",
                                        reason=f"Confianza insuficiente ({confidence}% < {min_conf}%) con '{result_title}'")
                    self._mark(key, "baja_confianza")
                    return

                media_info = self.tmdb.build_media_info(result, season=season, episode=episode)

                # Enriquecer con info de episodio si es serie
                if media_info.media_type == "tv" and season and episode:
                    try:
                        ep_info = self.tmdb.get_episode_info(media_info.tmdb_id, season, episode)
                        if ep_info.get("name"):
                            from dataclasses import replace
                            media_info = replace(media_info, episode_title=ep_info["name"])
                    except Exception:
                        pass

            # 3. Construir nuevo nombre
            templates = {k: self.config.get(k, "") for k in (
                "movie_template", "anime_template", "comic_template",
                "libro_template", "tv_template")}
            new_name = build_name_for_media_info(media_info, path.suffix, templates)
            _log.debug("Nuevo nombre calculado: '%s'", new_name)
            if not new_name:
                _log.error("No se pudo construir nombre para: %s", path.name)
                self.on_event("skip", f"No se pudo construir el nombre: {path.name}")
                self.on_file_event(key, "error",
                                    reason="No se pudo construir el nombre con la plantilla configurada")
                self._mark(key, "error_nombre")
                return

            # 4. Renombrar en origen (opcional — "Renombrar archivos en
            # origen" en Ajustes). Con reintentos si el archivo está
            # bloqueado por otro proceso (típico en Windows — WinError 32 —
            # pero también se cubren los equivalentes de macOS/Linux, donde
            # esto es mucho más raro porque POSIX permite renombrar archivos
            # abiertos, salvo en discos de red con locks o permisos).
            rename_local = self.config.get("rename_local", True)
            if rename_local:
                ok, result_path = False, ""
                for _attempt in range(4):
                    ok, result_path = rename_file(str(path), new_name)
                    if ok:
                        break
                    _is_locked = any(s in str(result_path).lower() for s in _LOCKED_FILE_HINTS)
                    _log.warning("Rename intento %d falló: %s | locked=%s", _attempt + 1, result_path, _is_locked)
                    if _is_locked and _attempt < 3:
                        self.on_event("info", f"Archivo en uso, reintentando ({_attempt+1}/3): {path.name}")
                        time.sleep(8)
                    else:
                        break
                if not ok:
                    _log.error("Rename fallido definitivamente: %s → %s", path.name, result_path)
                    self.on_event("error", f"Error al renombrar {path.name}: {result_path}")
                    # new_name/media_info/confidence también aquí: aunque el
                    # renombrado en disco falló, el usuario puede querer
                    # resolverlo a mano (p.ej. el destino "Ya existe" porque
                    # se detectó mal el episodio) — sin esto la entrada se
                    # quedaba sin info de TMDB y no se podía hacer nada con
                    # ella desde la GUI salvo buscarla de cero otra vez.
                    self.on_file_event(key, "error", new_name=new_name,
                                        media_info=media_info, confidence=confidence,
                                        reason=f"Error al renombrar: {result_path}")
                    is_collision = result_path.startswith("Ya existe:")
                    self._mark(key, "error_rename_existe" if is_collision else "error_rename")
                    return
                _log.info("Renombrado OK: %s → %s", path.name, new_name)
                new_path = result_path
                self.on_event("info", f"Renombrado → {new_name}")
            else:
                _log.info("Renombrado en origen desactivado, se mantiene el nombre en disco: %s", path.name)
                new_path = str(path)
            self.on_file_event(key, "renamed", new_name=new_name,
                                media_info=media_info, confidence=confidence,
                                renamed_on_disk=rename_local)

            # Registrar el nuevo path en _in_progress INMEDIATAMENTE para que el
            # siguiente scan no lo detecte como un archivo nuevo y lance un segundo hilo.
            new_key = new_path
            if new_key != key:
                self._in_progress.add(new_key)
                _log.debug("Nuevo path protegido en _in_progress: %s", Path(new_key).name)

            def _mark_both(status, **kw):
                """Marca original y nuevo path en processed y libera _in_progress."""
                self._mark(key, status, **kw)
                if new_key != key:
                    self._mark(new_key, status, **kw)

            def _discard_both():
                self._in_progress.discard(key)
                if new_key != key:
                    self._in_progress.discard(new_key)

            # 5. Subir por FTP (serializado: self.ftp es una única conexión
            # compartida por todos los hilos de _process)
            ticket_handed_off = True
            self._upload_to_ftp(key, new_name, media_info, season, new_path, _mark_both, _discard_both, ticket)

        except Exception as e:
            _log.exception("Error inesperado procesando: %s", path.name)
            self.on_event("error", f"Error inesperado ({path.name}): {e}")
            self._in_progress.discard(key)
        finally:
            if ticket is not None and not ticket_handed_off:
                self.upload_slots.release_ticket_unused(ticket)

    def _get_speed_limit_kbs(self) -> int:
        """Límite de velocidad configurado, en KB/s (0 = sin límite). Se relee
        en cada llamada para poder cambiarlo en caliente durante una subida
        larga, igual que en la subida manual."""
        try:
            mbs = float(self.config.get("ftp_speed_limit", 0) or 0)
            return int(mbs * 1024) if mbs > 0 else 0
        except Exception:
            return 0

    def _upload_to_ftp(self, key, new_name, media_info, season, new_path, _mark_both, _discard_both, ticket=None):
        """Construye la ruta remota y sube el archivo ya renombrado.
        Los pasos previos (comprobar/abrir conexión, resolver categoría,
        listar duplicados, espacio libre) van bajo self._ftp_lock: usan
        self.ftp, la única conexión de control compartida entre hilos, y FTP
        es un protocolo secuencial sobre un socket — usarlo desde varios
        hilos a la vez desincroniza peticiones/respuestas (se ven errores
        como "200 NOOP ok" o "331 Please specify the password" tratados como
        fallo) aunque el archivo acabe subiendo en un reintento posterior.
        La transferencia real, en cambio, usa una conexión FTP PROPIA por
        hilo (ver más abajo) en vez de self.ftp, para que varios archivos
        puedan subir de verdad en paralelo cuando "Subidas simultáneas" > 1."""
        host = self.config.get("ftp_host", "")
        if not host:
            _log.info("FTP no configurado, archivo guardado como: %s", new_name)
            self.on_event("skip", f"FTP no configurado — guardado como: {new_name}")
            self.on_file_event(key, "skip", new_name=new_name,
                                reason="FTP no configurado — el archivo se renombró pero no se subió")
            _mark_both("sin_ftp", new_name=new_name)
            self.upload_slots.release_ticket_unused(ticket)
            return

        with self._ftp_lock:
            if not self.ftp.is_connected():
                _log.info("Conectando FTP a %s...", host)
                ok2, msg2 = self.ftp.connect(
                    host,
                    int(self.config.get("ftp_port", 21)),
                    self.config.get("ftp_user", ""),
                    self.config.get("ftp_password", ""),
                    bool(self.config.get("ftp_use_tls", False)),
                )
                if not ok2:
                    _log.error("FTP conexion fallida: %s", msg2)
                    self.on_event("error", f"FTP no disponible: {msg2}")
                    self.on_file_event(key, "error", new_name=new_name, reason=f"FTP no disponible: {msg2}")
                    _mark_both("error_ftp", new_name=new_name)
                    self.upload_slots.release_ticket_unused(ticket)
                    return
                _log.info("FTP conectado OK")

            cats = self.config.get("ftp_categories", {"tv": [], "movie": [], "libro": []})
            type_cats = cats.get(media_info.media_type, [])
            # La organización real del servidor prevalece sobre la
            # clasificación automática por género -- ver
            # _find_category_with_existing_folder. Caso real que motivó
            # esto: una serie de animación para adultos, ya archivada a
            # mano fuera de la categoría infantil (mismo género,
            # "Animación", que sí coincide con esa categoría), se subió a
            # una carpeta NUEVA en la categoría infantil en vez de a la
            # que ya tenía, porque antes esta función se saltaba
            # directamente a choose_category (solo género) sin comprobar
            # si la serie ya existía en OTRA categoría.
            # El nombre ORIGINAL (key), no new_path: si el archivo ya se
            # renombró, el nombre limpio ya no conserva las marcas que
            # delatan el contenido adulto.
            category, _existing_folder = self._find_category_with_existing_folder(
                type_cats, media_info.title, original_name=Path(key).name)
            if category is None:
                category = choose_category(media_info.genre_ids, type_cats)
            if category is None:
                _log.error("Sin categoría FTP configurada para: %s (%s)", new_name, media_info.media_type)
                self.on_event("error", f"Sin categoría FTP configurada: {new_name}")
                self.on_file_event(key, "error", new_name=new_name,
                                    reason="Sin categoría FTP configurada para este tipo de contenido")
                _mark_both("sin_categoria", new_name=new_name)
                self.upload_slots.release_ticket_unused(ticket)
                return
            root = category.get("root", "")
            if not root:
                _log.error("Categoría '%s' sin ruta configurada: %s", category.get("name"), new_name)
                self.on_event("error", f"Categoría '{category.get('name')}' sin ruta configurada: {new_name}")
                self.on_file_event(key, "error", new_name=new_name,
                                    reason=f"Categoría '{category.get('name')}' sin ruta configurada")
                _mark_both("sin_ruta", new_name=new_name)
                self.upload_slots.release_ticket_unused(ticket)
                return

            if media_info.media_type == "tv":
                serie_name = self._resolve_series_folder(category, media_info)
            else:
                serie_name = media_info.title

            full_tpl = root.rstrip("/") + "/" + category.get("template", "{serie}/")
            remote_path = self.ftp.build_remote_path(
                full_tpl,
                serie=serie_name,
                season=season,
                year=str(media_info.year or ""),
                media_type=media_info.media_type,
            )

            # "Renombrar archivos en destino" (Ajustes) — si está desactivado,
            # se sube con el nombre ORIGINAL (el que tenía al detectarlo,
            # antes de cualquier renombrado local), aunque la carpeta se siga
            # organizando por serie/temporada según TMDB.
            remote_filename = new_name if self.config.get("rename_remote", True) else Path(key).name

            # "Ya existe en el servidor": si en la carpeta remota de destino
            # ya hay un archivo con EXACTAMENTE este mismo nombre y ya está
            # completo, no se sube. find_duplicate (justo debajo) excluye a
            # propósito el nombre exacto (others = [f for f in existing if
            # f != current_filename]) porque asume que un archivo ya subido
            # con ese nombre ES el propio -- pero eso falla cuando el mismo
            # contenido se descarga dos veces (p.ej. la misma película con
            # otra fuente): el nombre limpio coincide y un STOR posterior
            # sobrescribía en silencio la copia ya subida. Visto de verdad:
            # "La Odisea (2026)" descargada dos veces, la 2ª subida machacó
            # la 1ª. En modo automático no hay diálogo de "sobrescribir"
            # como en la subida manual: se omite. Un parcial más pequeño no
            # cuenta como "ya existe": se sube de nuevo para completarse.
            remote_file_full = f"{remote_path.rstrip('/')}/{remote_filename}"
            remote_size = self.ftp.get_remote_size(remote_file_full)
            try:
                local_size = Path(new_path).stat().st_size
            except OSError:
                local_size = 0
            if remote_size is not None and remote_size >= local_size:
                _log.info("Ya existe en el servidor, se omite: %s (ya existe %s)", new_name, remote_filename)
                self.on_event("skip", f"Ya existe en el servidor ({remote_filename}): {new_name}")
                self.on_file_event(key, "skip", new_name=new_name,
                                    reason=f"Ya existe en el servidor: {remote_filename}")
                _mark_both("duplicado", new_name=new_name)
                self.upload_slots.release_ticket_unused(ticket)
                return

            # Detección de duplicados: ¿ya hay en esa carpeta remota un
            # archivo DISTINTO que representa el mismo contenido (mismo
            # episodio, u otra versión de la misma película)? En modo
            # automático, sin nadie mirando un diálogo, se omite
            # directamente en vez de preguntar -- más seguro que subir
            # (o sobrescribir) sin supervisión.
            from core.duplicate_detect import find_duplicate
            existing_files = self.ftp.list_files(remote_path)
            dup = find_duplicate(existing_files, media_info, remote_filename)
            if dup:
                _log.info("Duplicado detectado, se omite: %s (ya existe %s)", new_name, dup)
                self.on_event("skip", f"Duplicado (ya existe {dup}): {new_name}")
                self.on_file_event(key, "skip", new_name=new_name,
                                    reason=f"Ya existe otro archivo para este mismo contenido: {dup}")
                _mark_both("duplicado", new_name=new_name)
                self.upload_slots.release_ticket_unused(ticket)
                return

            # Comprobación de espacio libre en el disco concreto de destino
            # (no del servidor en general -- el mismo servidor puede tener
            # varias rutas en discos distintos) antes de empezar a subir
            # nada. Si el servidor no soporta ningún comando de espacio
            # libre (p.ej. vsftpd, el nuestro), get_free_space() devuelve
            # None y este chequeo simplemente no aplica -- igual que en la
            # subida manual.
            try:
                local_size = Path(new_path).stat().st_size
            except OSError:
                local_size = 0
            free = self.ftp.get_free_space(remote_path)
            if free is None and self.config.get("jellyfin_enabled"):
                # Igual que en la subida manual: si el FTP no da el dato,
                # usar el de Jellyfin (>= 10.11) si está configurado,
                # emparejando por la raíz de la categoría.
                from core.media_server_refresh import get_jellyfin_free_space_for_root
                free = get_jellyfin_free_space_for_root(
                    root, self.config.get("jellyfin_host", ""),
                    self.config.get("jellyfin_api_key", ""))
            if free is not None and free < local_size:
                _log.error("Sin espacio en el disco de destino para %s (libre: %s, necesita: %s)",
                           new_name, free, local_size)
                self.on_event("error", f"Disco lleno en el servidor: {new_name}")
                self.on_file_event(key, "error", new_name=new_name,
                                    reason=f"Disco lleno en el servidor (libre: {free/(1024**3):.1f} GB)")
                _mark_both("disco_lleno", new_name=new_name)
                self.upload_slots.release_ticket_unused(ticket)
                return

            # "en cola": TODAVÍA no hay turno de "Subidas simultáneas"
            # garantizado -- si varios archivos nuevos se detectan a la vez,
            # cada uno llega hasta aquí en su propio hilo (esta parte, bajo
            # self._ftp_lock, es rápida) y todos pasarían este punto casi a
            # la vez. Marcarlos "subiendo" ya aquí hacía que 5 archivos
            # detectados juntos se vieran los 5 "Subiendo" aunque solo uno
            # pudiera transferir de verdad a la vez -- el evento "uploading"
            # de verdad se dispara más abajo, solo tras conseguir turno.
            _log.info("En cola: %s → %s/%s", new_name, remote_path, remote_filename)
            self.on_file_event(key, "queued", new_name=new_name)

        # A PROPÓSITO fuera de self._ftp_lock: "Subidas simultáneas" es un
        # cupo GLOBAL compartido con la subida manual (ver
        # core/upload_slots.py) -- si está ocupado por una subida manual
        # puede tardar minutos en liberarse, y mientras se espera no hay
        # ningún uso real de self.ftp en curso. Esperar aquí DENTRO del
        # candado (como estaba antes) monopolizaba la conexión compartida
        # todo ese tiempo, dejando sin responder cualquier otra parte de la
        # app que también la necesita (refresco de espacio libre, "Probar
        # conexión"...) aunque no hubiera ninguna transferencia en marcha --
        # visto de verdad: el indicador de espacio se quedaba sin
        # actualizarse mientras el automático llevaba un rato en marcha.
        def _progress_cb(sent, total, speed):
            pct = sent / total if total > 0 else 0.0
            self.on_file_event(key, "uploading", new_name=new_name, progress=pct, speed=speed)

        if not self.upload_slots.acquire(cancel_event=self._stop, ticket=ticket):
            _log.info("Subida cancelada esperando turno: %s", new_name)
            self.on_event("info", "Subida cancelada por parada del modo automático")
            self.on_file_event(key, "skip", new_name=new_name,
                                reason="Subida cancelada al detener el modo automático")
            _discard_both()
            return
        _log.info("Subiendo: %s → %s/%s", new_name, remote_path, remote_filename)
        self.on_event("info", f"Subiendo: {new_name}")
        self.on_file_event(key, "uploading", new_name=new_name, progress=0.0, speed=0.0)
        try:
            # Conexión PROPIA para la transferencia real, NO self.ftp: ese es
            # el canal de control compartido (correcto para las operaciones
            # rápidas de arriba, bajo self._ftp_lock), pero reutilizarlo
            # también aquí serializaba TODAS las subidas detrás de ese mismo
            # candado -- upload_slots ya podía conceder varios turnos a la
            # vez, pero todos esperaban el mismo socket para transferir de
            # verdad (varios archivos en "Subiendo" a la vez, solo uno
            # avanzando). upload_slots.acquire() ya limita cuántas
            # conexiones de este tipo se abren a la vez (mismo cupo que la
            # subida manual), así que abrir una por transferencia es seguro.
            own_ftp = self._ftp_factory()
            ok_conn, msg_conn = own_ftp.connect(
                host,
                int(self.config.get("ftp_port", 21)),
                self.config.get("ftp_user", ""),
                self.config.get("ftp_password", ""),
                bool(self.config.get("ftp_use_tls", False)),
            )
            if not ok_conn:
                _log.error("FTP conexion fallida (subida): %s", msg_conn)
                ok3, msg3 = False, msg_conn
            else:
                try:
                    ok3, msg3 = own_ftp.upload_file(
                        new_path, remote_path,
                        progress_cb=_progress_cb,
                        cancel_event=self._stop,
                        speed_limit_kbs=self._get_speed_limit_kbs,
                        remote_filename=remote_filename,
                    )
                finally:
                    own_ftp.disconnect()
        finally:
            self.upload_slots.release()
        if ok3:
            _log.info("FTP upload OK: %s -> %s", new_name, remote_path)
            self.on_event("ok", f"✓ Subido: {new_name}")
            # remote_full: la ruta REAL en el servidor. Sin esto la GUI
            # guardaba en el historial la ruta local en el campo "remote"
            # (ver gui/app.py, evento "uploaded"), así que de 500 registros
            # 241 no sabían dónde había quedado el archivo -- justo el dato
            # que hace falta para poder ofrecer "borrar también del servidor".
            self.on_file_event(key, "uploaded", new_name=new_name, size=local_size,
                                remote_full=f"{remote_path}/{remote_filename}")
            _mark_both("subido", new_name=new_name)
            from core.media_server_refresh import trigger_refresh
            trigger_refresh(self.config)
            # Acción post-proceso: solo tras subida exitosa
            self._apply_post_process_action(Path(new_path))
        elif msg3 == "cancelado":
            _log.info("Subida cancelada: %s", new_name)
            self.on_event("info", "Subida cancelada por parada del modo automático")
            self.on_file_event(key, "skip", new_name=new_name,
                                reason="Subida cancelada al detener el modo automático")
            _discard_both()
        else:
            _log.error("FTP upload fallido: %s | %s", new_name, msg3)
            self.on_event("error", f"Error FTP al subir {new_name}: {msg3}")
            self.on_file_event(key, "error", new_name=new_name, reason=f"Error FTP al subir: {msg3}")
            _mark_both("error_ftp", new_name=new_name)

    # ── Carpeta de serie en el FTP ─────────────────────────────────────────────

    def _find_category_with_existing_folder(self, categories: list, title: str,
                                             original_name: str = None) -> tuple:
        """Busca en TODAS las *categories* (ya filtradas por tipo de media)
        si el título a subir ya tiene carpeta en alguna de ellas -- para
        que la organización real del servidor prevalezca sobre la
        clasificación automática por género (ver el comentario en
        _process, que llama a esto antes de choose_category). Mismo
        emparejamiento de alta confianza que la GUI (ver
        gui/app.py::_find_category_with_existing_folder), compartido vía
        core.ftp_categories.find_existing_category_folder -- aquí solo
        cambia CÓMO se lista/cachea cada raíz: siempre una conexión ya
        abierta (self.ftp, dentro de self._ftp_lock) y siempre de verdad
        (sin equivalente a use_cache_only -- este método solo se llama una
        vez por archivo procesado, no en un redibujado de la interfaz)."""
        def dir_lookup(root):
            if root not in self._ftp_dir_cache:
                # Dos listados, no uno -- mismo motivo que en
                # gui/app.py::_find_category_with_existing_folder: un único
                # NLST a una raíz grande a veces vuelve incompleto sin dar
                # ningún error, y eso es precisamente el tipo de fallo que
                # este método existe para evitar (una carpeta ya existente
                # que no se detecta, así que se crea una nueva por error).
                first = set(self.ftp.list_dirs(root))
                second = set(self.ftp.list_dirs(root))
                self._ftp_dir_cache[root] = list(first | second)
            return self._ftp_dir_cache[root]

        from core.ftp_categories import find_existing_category_folder
        return find_existing_category_folder(categories, title, None, dir_lookup,
                                              original_name=original_name)

    def _resolve_series_folder(self, category: dict, media_info) -> str:
        """Si ya existe en la raíz de *category* una carpeta con nombre muy
        parecido (idioma, abreviatura...) a la serie a subir, la reutiliza en
        vez de crear una nueva a mayores. Sin interacción posible aquí, solo
        se aplica si la coincidencia es muy alta; si no, se crea carpeta nueva
        como hasta ahora."""
        desired = media_info.title
        key = media_info.tmdb_id
        if key in self._series_folder_cache:
            return self._series_folder_cache[key]

        chosen = desired
        root = category.get("root", "")
        if root:
            if root not in self._ftp_dir_cache:
                self._ftp_dir_cache[root] = self.ftp.list_dirs(root)
            existing = self._ftp_dir_cache[root]
            sanitized_desired = _ftp_safe(desired)
            if sanitized_desired in existing:
                chosen = sanitized_desired
            else:
                candidate, ratio = best_match(desired, existing, min_ratio=0.90)
                if candidate:
                    chosen = candidate
                    _log.info("Carpeta de serie reutilizada (%.0f%% parecido): '%s' -> '%s'",
                              ratio * 100, desired, candidate)

        self._series_folder_cache[key] = chosen
        return chosen

    # ── Persistencia ───────────────────────────────────────────────────────────

    def _mark(self, key: str, status: str, new_name: str = None):
        if status not in _PROTECTED_STATUSES:
            # No pisar en disco un estado protegido que la GUI ya haya
            # fijado a mano MIENTRAS este archivo se procesaba por su
            # cuenta -- p.ej. el usuario identifica el TMDB ID a mano justo
            # mientras un hilo de _process() ya en marcha, con su propia
            # detección desactualizada, seguía intentando. _save_db() vuelca
            # TODO self._processed sin fusionar con el disco, así que sin
            # esta comprobación el resultado tardío de ese hilo borraba la
            # marca manual del archivo, y el siguiente escaneo lo volvía a
            # ver como "fallido" y lo reprocesaba con el mismo resultado de
            # siempre, en bucle indefinido (visto de verdad: "Confianza
            # insuficiente" repitiéndose cada ciclo tras asignar el TMDB a
            # mano).
            current_status = self._load_db().get(key, {}).get("status", "")
            if current_status in _PROTECTED_STATUSES:
                _log.info("_mark: se descarta '%s' para %s -- ya protegido en disco como '%s'",
                          status, Path(key).name, current_status)
                self._in_progress.discard(key)
                return
        _log.debug("_mark: %s → %s", Path(key).name, status)
        entry = {
            "status":   status,
            "new_name": new_name,
            "ts":       time.time(),
        }
        # Arrastra la cuenta de reintentos que dejó _should_process al borrar
        # la entrada anterior -- solo para los estados de fallo, que son los
        # únicos que se reintentan (ver _MAX_RETRY_ATTEMPTS).
        attempts = self._pending_attempts.pop(key, 0)
        if status not in _PROTECTED_STATUSES and attempts:
            entry["attempts"] = attempts
        self._processed[key] = entry
        self._in_progress.discard(key)
        self._save_entry(key, entry)

    def _load_db(self) -> dict:
        try:
            p = _processed_db_path()
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_entry(self, key: str, entry: dict):
        """Fusión leer-modificar-escribir con lo que haya AHORA MISMO en
        disco -- no basta con volcar self._processed entero (lo que hacía
        antes _save_db()): mientras este hilo esperaba la estabilidad del
        archivo o la respuesta de TMDB (varios segundos), la GUI puede
        haber marcado a mano OTRO archivo distinto (identificado/subido
        manualmente) directamente en el JSON. Un volcado ciego de
        self._processed -- desactualizado desde el último _scan(), que es
        el único momento en que se sincroniza con disco -- borraba esa
        marca reciente sin querer. Visto de verdad: archivos ya subidos a
        mano volvían a aparecer como "nuevos" para AutoWatcher tras
        reiniciar la app, porque el _save_db() de OTRO archivo procesado
        casi a la vez pisaba el "subido" que la GUI acababa de escribir."""
        try:
            with _DB_LOCK:
                db = self._load_db()
                db[key] = entry
                _processed_db_path().write_text(
                    json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _delete_entry(self, key: str):
        """Mismo motivo que _save_entry -- fusión con disco en vez de
        volcar self._processed entero."""
        try:
            with _DB_LOCK:
                db = self._load_db()
                if key in db:
                    del db[key]
                    _processed_db_path().write_text(
                        json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
