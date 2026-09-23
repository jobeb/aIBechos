#!/bin/bash
set -e
echo "============================================"
echo "  aIBechos - Instalador Linux"
echo "============================================"
echo

# Verificar Python 3
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 no encontrado."
    echo "Instálalo con el gestor de paquetes de tu distro, p.ej.:"
    echo "  Debian/Ubuntu: sudo apt install python3 python3-pip"
    echo "  Fedora:        sudo dnf install python3 python3-pip"
    echo "  Arch:          sudo pacman -S python python-pip"
    exit 1
fi

# tkinter no se instala con pip -- en muchas distros (sobre todo
# instalaciones mínimas de Debian/Ubuntu) no viene con python3 por
# defecto, y sin él la app ni siquiera arranca.
if ! python3 -c "import tkinter" &> /dev/null; then
    echo "ERROR: falta el módulo tkinter de Python."
    echo "No se instala con pip -- instálalo con el gestor de paquetes de tu distro:"
    echo "  Debian/Ubuntu: sudo apt install python3-tk"
    echo "  Fedora:        sudo dnf install python3-tkinter"
    echo "  Arch:          sudo pacman -S tk"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Instalando dependencias..."
python3 -m pip install --upgrade pip
# requirements.txt es la única fuente de verdad (la usan también los
# instaladores de Windows y macOS) — instalar los paquetes sueltos a mano
# aquí hacía que este script se desincronizara y dejara de instalar
# keyring y tkinterdnd2, con lo que la app no llegaba ni a arrancar.
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt"

echo
echo "============================================"
echo "  Instalación completada!"
echo "  Ejecuta: python3 main.py"
echo "============================================"
