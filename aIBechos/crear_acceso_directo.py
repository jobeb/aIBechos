"""
Crea un acceso directo de aIBechos en el Escritorio.
Funciona en Windows sin necesidad de PowerShell.
"""
import os
import sys
import winreg
from pathlib import Path


def find_python() -> str:
    """Devuelve la ruta al ejecutable Python actual."""
    return sys.executable


def get_desktop() -> Path:
    """Obtiene la ruta del Escritorio del usuario actual."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        )
        desktop, _ = winreg.QueryValueEx(key, "Desktop")
        winreg.CloseKey(key)
        return Path(desktop)
    except Exception:
        return Path.home() / "Desktop"


def create_shortcut():
    script_dir = Path(__file__).parent.resolve()
    python_exe = find_python()
    desktop = get_desktop()
    shortcut_path = desktop / "aIBechos.lnk"

    try:
        import pythoncom
        from win32com.shell import shell
        import win32com.client
    except ImportError:
        # pywin32 no está instalado: crear un .bat en el escritorio como alternativa
        bat_path = desktop / "aIBechos.bat"
        bat_path.write_text(
            f'@echo off\ncd /d "{script_dir}"\n"{python_exe}" main.py\n',
            encoding="utf-8"
        )
        print(f"Acceso directo (.bat) creado en: {bat_path}")
        return

    shell_obj = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell_obj.CreateShortcut(str(shortcut_path))
    shortcut.TargetPath = python_exe
    shortcut.Arguments = f'"{script_dir / "main.py"}"'
    shortcut.WorkingDirectory = str(script_dir)
    shortcut.Description = "aIBechos - Renombrador de series y películas"
    icon_path = script_dir / "iconoPrincipal.ico"
    if icon_path.exists():
        shortcut.IconLocation = str(icon_path)
    shortcut.Save()
    print(f"Acceso directo creado en: {shortcut_path}")


if __name__ == "__main__":
    create_shortcut()
