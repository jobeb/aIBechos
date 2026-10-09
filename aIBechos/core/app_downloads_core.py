"""
Ayudantes sin interfaz de la pestaña Descargas (aMule): abrir una conexión
EC, saber si un hash ya está en completados, tiempo restante y estado de una
descarga activa, y la consulta para buscar una alternativa. Mixin que hereda
QtAppCore (gui_qt/core_host.py).
"""

from core.amule_search import build_amule_query
from core.api_client import detect_episode
from core.applog import get_logger
from core.ec_client import EcAuthError, EcClient, EcConnectionError

_log = get_logger("aIBechos.gui", "app.log")


class DownloadsCoreMixin:

    def _downloads_open_ec(self):
        """Abre UNA conexión EC nueva (no reutilizable: aMule solo soporta
        una conexión a la vez y una nueva cierra la anterior, así que no se
        mantiene sesión persistente entre operaciones). Devuelve el EcClient
        conectado o None si aMule no responde. Debe llamarse bajo
        self._amule_ec_lock."""
        ec = EcClient(
            host=self.config_data.get("amule_host", "localhost"),
            port=self.config_data.get("amule_port", 4712),
            password=self.config_data.get("amule_password", ""),
            timeout=10.0,
        )
        try:
            ec.connect()
        except (EcConnectionError, EcAuthError, OSError):
            try:
                ec.close()
            except Exception:
                pass
            return None
        return ec

    def _eta_for_download(self, d: dict) -> str:
        try:
            speed = d.get("speed",0) or 0
            if speed <= 0:
                return "—"
            remain = max(0, d.get("size_full",0) - d.get("size_done",0))
            secs = int(remain / speed) if speed else 0
            if secs <= 0:
                return "—"
            if secs < 60:
                return f"{secs}s"
            if secs < 3600:
                return f"{secs//60}m {secs%60}s"
            h = secs//3600
            m = (secs%3600)//60
            return f"{h}h {m}m"
        except Exception:
            return "—"

    def _status_label_for_download(self, code: int) -> str:
        # Códigos de aMule PartFileStatus (aprox): 0=waiting, 1=paused, 2=downloading, etc.
        mapping = {0: "Esperando", 1: "Pausado", 2: "Descargando", 3: "Descargando", 4: "Completado", 7: "Detenido"}
        return mapping.get(code, str(code) if code else "—")

    def _alternative_query_for_download(self, d: dict) -> str:
        """Query limpia para buscar alternativa a partir del nombre de la descarga."""
        try:
            name = d.get("name", "") or ""
            det = detect_episode(name)
            title = (det.get("title") or "").strip() if det else ""
            if title:
                season = det.get("season")
                episode = det.get("episode")
                templates = self.config_data.get("series_search_patterns") or {}
                prefers = "castellano" in name.lower()
                try:
                    q = build_amule_query(title, season, episode, templates=templates, prefers_castellano=prefers)
                    if q:
                        return q
                except Exception:
                    pass
                return title
            base = name.rsplit(".", 1)[0] if "." in name else name
            return base[:80].strip() or name[:80]
        except Exception:
            return (d.get("name", "") or "")[:80]

    def _download_hash_in_shared(self, hash_hex: str) -> bool:
        """True si aMule ya tiene ese hash MD4 en compartidos (ya descargado)
        y por tanto no lo volverá a bajar aunque se le pida.

        Se comprueba primero la cola de descargas (si está ahí, se está
        bajando de verdad) y solo si falta se mira `amulecmd show shared`.
        Cualquier duda (sin hash, sin amulecmd, error) devuelve False: no se
        puede afirmar nada y no se acusa. Se llama desde un hilo worker,
        nunca desde la interfaz."""
        if not hash_hex or len(hash_hex) != 32:
            return False
        try:
            import time as _t2
            _t2.sleep(1.0)
            h = hash_hex.lower()
            with self._amule_ec_lock:
                ec = self._downloads_open_ec()
                if ec is None:
                    in_queue = False
                else:
                    try:
                        q = ec.get_download_queue()
                    finally:
                        try:
                            ec.close()
                        except Exception:
                            pass
                    in_queue = any((d.get("hash_hex", "") or "").lower() == h for d in q)
            if in_queue:
                return False
            from core.amule_client import _find_amulecmd, decode_console_output, hash_in_shared_output
            from core.ec_client import _no_console_kwargs
            import subprocess
            exe = _find_amulecmd("")
            if not exe:
                return False
            args = [exe, "-c", "show shared",
                    "-h", self.config_data.get("amule_host", "localhost"),
                    "-p", str(self.config_data.get("amule_port", 4712) or 4712),
                    "-P", self.config_data.get("amule_password", "")]
            r = subprocess.run(args, capture_output=True, timeout=6,
                               **_no_console_kwargs())
            return hash_in_shared_output(decode_console_output(r.stdout or b""), h)
        except Exception:
            return False
