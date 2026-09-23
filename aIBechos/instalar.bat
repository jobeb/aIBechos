@echo off
echo ============================================
echo   aIBechos - Instalador Windows
echo ============================================
echo.

:: Intentar ejecutar via PowerShell con bypass (resuelve la politica de ejecucion)
powershell -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
if %errorlevel% == 0 goto :fin

:: Si falla PowerShell, usar py directamente
echo Intentando con el launcher de Python (py)...
echo.

py --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: No se encontro Python.
    echo.
    echo Python 3.13 esta instalado pero no responde.
    echo Prueba a ejecutar manualmente:
    echo   py main.py
    echo.
    pause
    exit /b 1
)

py -m pip install -r "%~dp0requirements.txt"
echo.
echo Creando acceso directo en el Escritorio...
py "%~dp0crear_acceso_directo.py"
echo.
echo Iniciando aplicacion...
cd /d "%~dp0"
py main.py

:fin
