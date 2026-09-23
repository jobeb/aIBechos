#!/bin/bash
set -e
echo "============================================"
echo "  aIBechos - Instalador macOS"
echo "============================================"
echo

# Verificar Python 3
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 no encontrado."
    echo "Instálalo desde https://www.python.org/downloads/"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Instalando dependencias..."
python3 -m pip install --upgrade pip
# requirements.txt es la única fuente de verdad (la usan también los
# instaladores de Windows) — instalar los paquetes sueltos a mano aquí
# hacía que este script se desincronizara y dejara de instalar keyring y
# tkinterdnd2, con lo que la app no llegaba ni a arrancar.
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt"

echo
echo "============================================"
echo "  Instalación completada!"
echo "  Ejecuta: python3 main.py"
echo "============================================"
