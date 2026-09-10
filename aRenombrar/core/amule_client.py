import os
import re
import subprocess
import threading
import time as _time
from dataclasses import dataclass
from typing import Optional


@dataclass
class AmuleSearchResult:
    number: int
    name: str
    size_human: str
    sources: int
    complete: bool = False


def _no_console_kwargs() -> dict:
    """En Windows, evita que amulecmd.exe abra una ventana de consola:
    cuando el proceso padre no tiene consola propia (la app empaquetada
    con PyInstaller, o arrancada con pythonw como hace lanzar.vbs según
    cómo se lance), un hijo de consola como amulecmd recibe una consola
    NUEVA visible -- el parpadeo de cmd que se ve al buscar. Con
    CREATE_NO_WINDOW el hijo no crea ninguna. No-op en el resto de
    plataformas (POSIX no tiene consolas aisladas)."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}


def _find_amulecmd(custom_path: str) -> Optional[str]:
    candidates = []
    if custom_path:
        candidates.append(custom_path)
    if os.name == "nt":
        candidates.extend([
            r"C:\Program Files\aMule\bin\amulecmd.exe",
            r"C:\Program Files (x86)\aMule\bin\amulecmd.exe",
        ])
    else:
        candidates.extend([
            "amulecmd",
            "/usr/bin/amulecmd",
            "/usr/local/bin/amulecmd",
        ])
    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        if os.path.isfile(path):
            return path
        try:
            subprocess.run([path, "--help"], capture_output=True, timeout=10,
                           **_no_console_kwargs())
            return path
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
    return None


def hash_in_shared_output(output: str, hash_hex: str) -> bool:
    """True si el hash MD4 (hex, 32 chars) aparece en la salida de
    `amulecmd show shared` -- es decir, aMule ya lo tiene en
    compartidos/completados y no lo volverá a bajar aunque se le pida.

    Comparación insensible a mayúsculas; cualquier salida vacía, hash
    inválido o ausencia devuelve False (no se puede afirmar nada)."""
    if not output or not hash_hex or len(hash_hex) != 32:
        return False
    try:
        h = hash_hex.lower()
    except Exception:
        return False
    try:
        return h in output.lower()
    except Exception:
        return False


class AmuleSession:
    """Mantiene una sesión interactiva de amulecmd abierta.
    Search + Results + Download deben compartir la misma sesión."""

    def __init__(self, exe: str, host: str, port: int, password: str):
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._output: list[str] = []
        self._lock = threading.Lock()
        self._closed = False

        args = [exe]
        if host:
            args.extend(["-h", host])
        if port:
            args.extend(["-p", str(port)])
        if password:
            args.extend(["-P", password])

        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            **_no_console_kwargs(),
        )
        if self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("No se pudo abrir amulecmd")

        def _reader():
            try:
                for line in iter(self._proc.stdout.readline, ""):
                    with self._lock:
                        self._output.append(line)
            except ValueError:
                pass

        self._reader_thread = threading.Thread(target=_reader, daemon=True)
        self._reader_thread.start()
        # Esperar a que arranque
        _time.sleep(0.5)

    def send(self, cmd: str, wait_seconds: float = 0) -> None:
        if self._closed or self._proc is None or self._proc.stdin is None:
            return
        try:
            self._proc.stdin.write(cmd + "\n")
            self._proc.stdin.flush()
        except OSError:
            self._closed = True
            return
        if wait_seconds > 0:
            _time.sleep(wait_seconds)

    def read_output(self) -> str:
        with self._lock:
            lines = list(self._output)
            self._output.clear()
        return "".join(lines)

    def flush_output(self) -> str:
        """Lee todo el output acumulado hasta ahora."""
        _time.sleep(0.2)
        return self.read_output()

    def is_alive(self) -> bool:
        if self._closed or self._proc is None:
            return False
        if self._proc.poll() is not None:
            self._closed = True
            return False
        return True

    def close(self) -> None:
        if self._closed or self._proc is None:
            return
        self._closed = True
        try:
            if self._proc.stdin:
                self._proc.stdin.write("exit\n")
                self._proc.stdin.flush()
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                self._proc.kill()
                self._proc.wait(timeout=3)
            except OSError:
                pass
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=3)


class AmuleClient:
    def __init__(self, host="localhost", port=4712, password="", amulecmd_path="amulecmd"):
        self.host = host
        self.port = port
        self.password = password
        self._amulecmd_exe = _find_amulecmd(amulecmd_path)

    @property
    def is_available(self) -> bool:
        return self._amulecmd_exe is not None

    def _build_args(self, command: str) -> Optional[list[str]]:
        if not self._amulecmd_exe:
            return None
        args = [self._amulecmd_exe, "-c", command]
        if self.host:
            args.extend(["-h", self.host])
        if self.port:
            args.extend(["-p", str(self.port)])
        if self.password:
            args.extend(["-P", self.password])
        return args

    def _run_command(self, command: str, timeout=30) -> str:
        if not self._amulecmd_exe:
            return ""
        args = self._build_args(command)
        if args is None:
            return ""
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                    **_no_console_kwargs())
            combined = (result.stdout or "") + (result.stderr or "")
            return combined.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def test_connection(self) -> tuple[bool, str]:
        if not self._amulecmd_exe:
            return False, "no se encontró amulecmd"
        output = self._run_command("status", timeout=15)
        if not output:
            return False, "no se pudo ejecutar amulecmd"
        output_lower = output.lower()
        err_keywords = ("connection failed", "unable to connect", "failed to connect",
                        "cannot connect", "refused", "timed out", "error")
        if any(kw in output_lower for kw in err_keywords):
            return False, output
        return True, output

    def create_session(self) -> Optional[AmuleSession]:
        if not self._amulecmd_exe:
            return None
        try:
            return AmuleSession(self._amulecmd_exe, self.host, self.port, self.password)
        except (RuntimeError, OSError):
            return None

    def iter_search_in_session(self, session: AmuleSession, query: str,
                               search_type="Kad", file_type: str = "",
                               poll_interval: float = 3.0,
                               max_duration: float = 60.0):
        """Igual que search_in_session pero SIN espera fija: lanza la
        búsqueda y consulta "results" cada poll_interval segundos durante
        max_duration como mucho, entregando cada vez la lista COMPLETA
        acumulada hasta ese momento (aMule la va llenando sola con los
        resultados que van llegando). El llamador puede ir mostrando y
        actualizando la lista en vivo en vez de esperar los 10 segundos
        de golpe. Generador: rinde una lista nueva por cada sondeo y
        termina al agotarse max_duration."""
        cmd = f"search {search_type} {query}"
        if file_type:
            cmd += f" --type {file_type}"
        session.send(cmd)
        deadline = _time.monotonic() + max_duration
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                return
            _time.sleep(min(poll_interval, remaining))
            session.send("results", wait_seconds=0.5)
            output = session.flush_output()
            yield self._parse_results(output)

    def search_in_session(self, session: AmuleSession, query: str,
                          search_type="Kad", wait_seconds=10,
                          file_type: str = "") -> list[AmuleSearchResult]:
        """Búsqueda de una sola pasada: espera fija y devuelve la lista
        final. Para ir actualizando en vivo usa iter_search_in_session
        (esta es justo esa llamada con poll_interval=max_duration=wait)."""
        results = []
        for current in self.iter_search_in_session(
                session, query, search_type=search_type, file_type=file_type,
                poll_interval=wait_seconds, max_duration=wait_seconds):
            results = current
        return results

    def download_in_session(self, session: AmuleSession, result_number: int) -> tuple[bool, str]:
        session.send(f"download {result_number}", wait_seconds=0.5)
        output = session.flush_output()
        if not output:
            return False, ""
        err_keywords = ("error", "failed", "invalid", "not found", "no such result")
        if any(kw in output.lower() for kw in err_keywords):
            return False, output
        return True, output

    @staticmethod
    def _parse_results(output: str) -> list[AmuleSearchResult]:
        if not output:
            return []
        results = []
        sep_found = False
        for line in output.splitlines():
            if line.startswith("---"):
                sep_found = True
                continue
            if not sep_found:
                continue
            line = line.strip()
            if not line or "Number of search results" in line:
                continue
            parts = re.split(r'\s{2,}', line)
            if len(parts) < 4:
                continue
            num_str = parts[0].rstrip(".")
            if not num_str.isdigit():
                continue
            try:
                sources_val = int(float(parts[3].strip()))
            except (ValueError, IndexError):
                continue
            results.append(AmuleSearchResult(
                number=int(num_str),
                name=parts[1].strip(),
                size_human=f"{parts[2].strip()} MB",
                sources=sources_val,
                complete=sources_val > 0,
            ))
        return results

    # ---- Legacy API (kept for compatibility) ----

    def search(self, query: str, search_type="Kad", wait_seconds=10) -> tuple[list[AmuleSearchResult], str]:
        self._run_command(f"search {search_type} {query}", timeout=15)
        _time.sleep(wait_seconds)
        output = self._run_command("results", timeout=15)
        return self._parse_results(output), output

    def download(self, result_number: int) -> tuple[bool, str]:
        raise NotImplementedError("download() no funciona con -c. Usa create_session() + download_in_session()")
