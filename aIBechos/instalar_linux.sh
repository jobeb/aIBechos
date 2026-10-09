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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Instalando dependencias..."
python3 -m pip install --upgrade pip
# requirements.txt es la única fuente de verdad (la usan también los
# instaladores de Windows y macOS) — instalar los paquetes sueltos a mano
# aquí hacía que este script se desincronizara y dejara de instalar
# keyring, con lo que la app no llegaba ni a arrancar.
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt"

echo
# La interfaz (PySide6/Qt) trae sus propias librerías, pero en X11 su
# plugin "xcb" necesita libxcb-cursor del sistema (Qt 6.5+); sin ella la
# app sale con "could not load the Qt platform plugin xcb".
if command -v ldconfig &> /dev/null && ! ldconfig -p 2>/dev/null | grep -q libxcb-cursor; then
    echo "AVISO: parece que falta libxcb-cursor (la necesita la interfaz en X11):"
    echo "  Debian/Ubuntu: sudo apt install libxcb-cursor0"
    echo "  Fedora:        sudo dnf install xcb-util-cursor"
    echo "  Arch:          sudo pacman -S xcb-util-cursor"
fi

echo "============================================"
echo "  Instalación completada!"
echo "  Ejecuta: python3 main.py"
echo "============================================"
