"""
Actualización automática: descarga el paquete de la release nueva de GitHub
que corresponde a esta instalación, lo deja preparado y lanza un proceso
auxiliar que espera a que la app se cierre, sustituye los archivos y la
vuelve a abrir. Sin diálogos (ver SettingsCoreMixin._start_auto_update).

Qué se descarga según cómo se instaló (install_kind):
  - "installer"     Windows con el instalador Inno (unins000.exe junto al
                    .exe): aIBechos_Setup_X.exe, ejecutado con /VERYSILENT.
  - "portable_win"  Windows portable: aIBechos_X_windows.zip.
  - "macos"         aIBechos.app: aIBechos_X.dmg (se monta y se copia la .app).
  - "linux"         aIBechos_X_linux.tar.gz.
Ejecutándose desde el código fuente (no congelado) devuelve None: ahí no
hay nada que reemplazar y se sigue avisando como antes.

El auxiliar es PowerShell en Windows y /bin/sh en macOS/Linux, y escribe lo
que hace en self_update.log (carpeta de datos de la app). Si la copia falla
deja restaurada la versión anterior y vuelve a abrir la app igualmente.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import requests

from core.appdirs import app_data_dir
from core.applog import get_logger

_log = get_logger("aIBechos.self_update", "self_update.log")

_ASSET_PATTERNS = {
    "installer": re.compile(r"^aIBechos_Setup_.*\.exe$", re.I),
    "portable_win": re.compile(r"_windows\.zip$", re.I),
    "macos": re.compile(r"\.dmg$", re.I),
    "linux": re.compile(r"_linux\.tar\.gz$", re.I),
}


@dataclass
class PreparedUpdate:
    tag: str
    kind: str
    payload: Path          # instalador .exe, o carpeta/.app ya extraída
    target: Path           # carpeta de la app (o la .app en macOS)
    exe: Path              # ejecutable a relanzar (Windows/Linux)
    extra: dict = field(default_factory=dict)


def _exe_path(executable=None) -> Path:
    return Path(executable or sys.executable).resolve()


def install_kind(frozen=None, executable=None, platform=None):
    """Tipo de instalación de ESTE proceso, o None si no se puede
    actualizar solo (código fuente, plataforma desconocida)."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    platform = platform or sys.platform
    if not frozen:
        return None
    exe = _exe_path(executable)
    if platform.startswith("win"):
        return "installer" if (exe.parent / "unins000.exe").exists() else "portable_win"
    if platform == "darwin":
        return "macos" if _macos_bundle(exe) is not None else None
    if platform.startswith("linux"):
        return "linux"
    return None


def _macos_bundle(exe: Path):
    """.../aIBechos.app/Contents/MacOS/aIBechos -> .../aIBechos.app"""
    for p in exe.parents:
        if p.suffix == ".app":
            return p
    return None


def pick_asset(assets, kind):
    """El adjunto de la release que sirve para *kind*, o None."""
    pat = _ASSET_PATTERNS.get(kind)
    if pat is None:
        return None
    for a in assets or []:
        if pat.search(a.get("name", "")) and a.get("url"):
            return a
    return None


def _update_dir() -> Path:
    return app_data_dir() / "update"


def _download(asset: dict, dest: Path, timeout: int = 30):
    expected = int(asset.get("size") or 0)
    digest = (asset.get("digest") or "").lower()
    h = hashlib.sha256()
    got = 0
    with requests.get(asset["url"], stream=True, timeout=timeout,
                      headers={"Accept": "application/octet-stream"}) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
    if expected and got != expected:
        raise IOError(f"descarga incompleta ({got} de {expected} bytes)")
    if digest.startswith("sha256:") and h.hexdigest() != digest.split(":", 1)[1]:
        raise IOError("la suma SHA-256 de la descarga no coincide")


def _single_subdir(folder: Path) -> Path:
    """Los paquetes traen una carpeta raíz (aIBechos/): devolverla."""
    items = [p for p in folder.iterdir() if not p.name.startswith(".")]
    if len(items) == 1 and items[0].is_dir():
        return items[0]
    return folder


def prepare(tag: str, asset: dict, kind: str, executable=None) -> PreparedUpdate:
    """Descarga y deja listo el paquete (en un hilo de fondo: tarda).
    Lanza excepción si algo falla; no toca la instalación actual."""
    exe = _exe_path(executable)
    work = _update_dir()
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    dl = work / asset["name"]
    _log.info("Descargando %s para actualizar a %s", asset["name"], tag)
    _download(asset, dl)

    if kind == "installer":
        return PreparedUpdate(tag, kind, dl, exe.parent, exe)

    staging = work / "nuevo"
    staging.mkdir()
    if kind == "portable_win":
        _check_writable(exe.parent)
        with zipfile.ZipFile(dl) as z:
            z.extractall(staging)
        src = _single_subdir(staging)
        if not (src / exe.name).exists():
            raise IOError(f"el paquete no trae {exe.name}")
        return PreparedUpdate(tag, kind, src, exe.parent, exe)

    if kind == "linux":
        _check_writable(exe.parent)
        with tarfile.open(dl, "r:gz") as t:
            try:
                t.extractall(staging, filter="data")
            except TypeError:           # Python sin filtros de tarfile
                t.extractall(staging)
        src = _single_subdir(staging)
        if not (src / exe.name).exists():
            raise IOError(f"el paquete no trae {exe.name}")
        return PreparedUpdate(tag, kind, src, exe.parent, exe)

    if kind == "macos":
        bundle = _macos_bundle(exe)
        _check_writable(bundle.parent)
        mnt = work / "dmg"
        mnt.mkdir()
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen",
                        "-mountpoint", str(mnt), str(dl)], check=True, capture_output=True)
        try:
            apps = list(mnt.glob("*.app"))
            if not apps:
                raise IOError("el .dmg no trae ninguna .app")
            dst = staging / bundle.name
            subprocess.run(["ditto", str(apps[0]), str(dst)], check=True, capture_output=True)
        finally:
            subprocess.run(["hdiutil", "detach", "-force", str(mnt)], capture_output=True)
        return PreparedUpdate(tag, kind, dst, bundle, exe)

    raise ValueError(f"tipo de instalación desconocido: {kind}")


def cleanup_leftovers() -> None:
    """Borra la carpeta de descargas de actualizaciones anteriores. Solo se
    llama cuando no hay ninguna en curso; nunca lanza."""
    try:
        shutil.rmtree(_update_dir(), ignore_errors=True)
    except Exception:
        pass


def _check_writable(folder: Path):
    if not os.access(folder, os.W_OK):
        raise PermissionError(f"sin permiso de escritura en {folder}")


_PS1 = r"""
param([int]$AppPid, [string]$Mode, [string]$Payload, [string]$AppDir,
      [string]$Exe, [string]$Log, [int]$Minimized = 0)
function Write-Log($m) {
  Add-Content -LiteralPath $Log -Encoding UTF8 -Value ("{0} [aux] {1}" -f (Get-Date -Format s), $m)
}
function Start-App {
  if ($Minimized -eq 1) { Start-Process -FilePath $Exe -ArgumentList '--minimized' }
  else { Start-Process -FilePath $Exe }
}
try {
  Write-Log "Esperando a que se cierre la app (PID $AppPid)"
  Wait-Process -Id $AppPid -Timeout 180 -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
  if ($Mode -eq 'installer') {
    $p = Start-Process -FilePath $Payload -Wait -PassThru `
         -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-'
    Write-Log "Instalador terminado (código $($p.ExitCode))"
  } else {
    $stamp = Get-Date -Format yyyyMMddHHmmss
    $old = @()
    foreach ($item in Get-ChildItem -LiteralPath $Payload) {
      $dst = Join-Path $AppDir $item.Name
      if (Test-Path -LiteralPath $dst) {
        $bak = "$dst.old-$stamp"
        Rename-Item -LiteralPath $dst -NewName (Split-Path $bak -Leaf) -ErrorAction Stop
        $old += ,@($dst, $bak)
      }
    }
    try {
      Copy-Item -Path (Join-Path $Payload '*') -Destination $AppDir -Recurse -Force -ErrorAction Stop
      foreach ($o in $old) { Remove-Item -LiteralPath $o[1] -Recurse -Force -ErrorAction SilentlyContinue }
      Write-Log "Archivos sustituidos"
    } catch {
      Write-Log "Fallo al copiar, se restaura la versión anterior: $_"
      foreach ($o in $old) {
        Remove-Item -LiteralPath $o[0] -Recurse -Force -ErrorAction SilentlyContinue
        Rename-Item -LiteralPath $o[1] -NewName (Split-Path $o[0] -Leaf) -ErrorAction SilentlyContinue
      }
    }
  }
} catch {
  Write-Log "ERROR: $_"
}
Start-App
Write-Log "App relanzada"
"""

_SH = r"""#!/bin/sh
PID="$1"; MODE="$2"; PAYLOAD="$3"; TARGET="$4"; EXE="$5"; LOG="$6"; shift 6
log() { echo "$(date '+%Y-%m-%dT%H:%M:%S') [aux] $*" >> "$LOG"; }
log "Esperando a que se cierre la app (PID $PID)"
i=0
while kill -0 "$PID" 2>/dev/null && [ "$i" -lt 180 ]; do sleep 1; i=$((i+1)); done
sleep 1
STAMP=$(date +%Y%m%d%H%M%S)
if [ "$MODE" = "macos" ]; then
  if mv "$TARGET" "$TARGET.old-$STAMP"; then
    if ditto "$PAYLOAD" "$TARGET"; then
      rm -rf "$TARGET.old-$STAMP"; log "App sustituida"
    else
      log "Fallo al copiar, se restaura la versión anterior"
      rm -rf "$TARGET"; mv "$TARGET.old-$STAMP" "$TARGET"
    fi
  else
    log "No se pudo apartar $TARGET"
  fi
  open "$TARGET" --args "$@"
else
  OLD=""
  OK=1
  for item in "$PAYLOAD"/* ; do
    name=$(basename "$item"); dst="$TARGET/$name"
    if [ -e "$dst" ]; then
      if mv "$dst" "$dst.old-$STAMP"; then OLD="$OLD $name"; else OK=0; fi
    fi
  done
  if [ "$OK" = 1 ] && cp -a "$PAYLOAD"/. "$TARGET"/; then
    for name in $OLD; do rm -rf "$TARGET/$name.old-$STAMP"; done
    log "Archivos sustituidos"
  else
    log "Fallo al copiar, se restaura la versión anterior"
    for name in $OLD; do rm -rf "$TARGET/$name"; mv "$TARGET/$name.old-$STAMP" "$TARGET/$name"; done
  fi
  nohup "$EXE" "$@" >/dev/null 2>&1 &
fi
log "App relanzada"
"""


def launch_helper(prep: PreparedUpdate, pid: int, launch_args=()) -> None:
    """Arranca el auxiliar desacoplado de este proceso. Hay que cerrar la
    app justo después: el auxiliar espera a que termine *pid*."""
    log = str(app_data_dir() / "self_update.log")
    work = _update_dir()
    if sys.platform.startswith("win"):
        script = work / "actualizar.ps1"
        script.write_text(_PS1, encoding="utf-8-sig")
        ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / \
            "WindowsPowerShell" / "v1.0" / "powershell.exe"
        cmd = [str(ps), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-WindowStyle", "Hidden", "-File", str(script),
               "-AppPid", str(pid), "-Mode", prep.kind, "-Payload", str(prep.payload),
               "-AppDir", str(prep.target), "-Exe", str(prep.exe), "-Log", log]
        if "--minimized" in launch_args:
            cmd += ["-Minimized", "1"]
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(cmd, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, cwd=str(app_data_dir()))
    else:
        script = work / "actualizar.sh"
        script.write_text(_SH, encoding="utf-8")
        cmd = ["/bin/sh", str(script), str(pid), prep.kind, str(prep.payload),
               str(prep.target), str(prep.exe), log, *launch_args]
        subprocess.Popen(cmd, start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, cwd=str(app_data_dir()))
    _log.info("Auxiliar de actualización lanzado (%s -> %s)", prep.kind, prep.tag)
