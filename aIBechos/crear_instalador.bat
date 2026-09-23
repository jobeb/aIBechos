@echo off
cd /d "%~dp0"
echo ============================================
echo   aIBechos - Crear instalador Setup.exe
echo ============================================
echo.

:: Detectar Python
set PYTHON=
python --version >nul 2>&1 && set PYTHON=python
if "%PYTHON%"=="" (py --version >nul 2>&1 && set PYTHON=py)
if "%PYTHON%"=="" set PYTHON=C:\Users\Jose\AppData\Local\Programs\Python\Python313\python.exe

echo Usando Python: %PYTHON%
echo.

:: [1] Instalar dependencias + PyInstaller (desde requirements.txt -- una
:: lista a mano aqui se desincroniza con el tiempo y puede dejar fuera algo
:: tan critico como "keyring", que config.py importa sin red de seguridad)
echo [1/4] Instalando dependencias...
"%PYTHON%" -m pip install -r requirements.txt pyinstaller --quiet
echo       OK
echo.

:: [2] Compilar con PyInstaller usando aIBechos.spec
echo [2/4] Compilando a .exe con PyInstaller...
if exist dist\aIBechos rmdir /s /q dist\aIBechos
if exist build rmdir /s /q build

"%PYTHON%" -m PyInstaller --noconfirm aIBechos.spec

if errorlevel 1 (
    echo ERROR al compilar con PyInstaller.
    pause
    exit /b 1
)
echo       OK
echo.

:: [3] Buscar Inno Setup (busqueda amplia)
echo [3/4] Buscando Inno Setup...
set ISCC=

:: Buscar con PowerShell en Program Files (cubre cualquier version)
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-ChildItem -Path 'C:\Program Files','C:\Program Files (x86)' -Filter ISCC.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName"') do set ISCC=%%i

if "%ISCC%"=="" (
    echo.
    echo ERROR: Inno Setup no encontrado.
    echo Asegurate de haberlo instalado desde: https://jrsoftware.org/isdl.php
    echo Luego vuelve a ejecutar este script.
    pause
    exit /b 1
)
echo       Encontrado: %ISCC%
echo.

:: [4] Compilar el instalador -- setup.iss lee la version el mismo
:: directamente de core/version.py, no hace falta pasarsela desde aqui
:: (un intento anterior de hacerlo con /DMyAppVersion desde un comando de
:: Python aparte fallaba en silencio en algunos entornos, dejando la
:: version vacia y el instalador sin compilar).
echo [4/4] Generando aIBechos_Setup.exe...
if exist installer_output rmdir /s /q installer_output
"%ISCC%" setup.iss

if errorlevel 1 (
    echo ERROR al generar el instalador.
    pause
    exit /b 1
)
echo       OK
echo.

echo ============================================
echo   Instalador listo en:
echo   installer_output\aIBechos_Setup.exe
echo ============================================
echo.
echo Puedes compartir ese archivo con quien quieras.
echo Al ejecutarlo, instala la app con acceso directo