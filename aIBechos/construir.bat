@echo off
cd /d "%~dp0"
echo ============================================
echo   aIBechos - Construir ejecutable (.exe)
echo ============================================
echo.

:: Detectar Python
set PYTHON=
python --version >nul 2>&1 && set PYTHON=python
if "%PYTHON%"=="" (py --version >nul 2>&1 && set PYTHON=py)
if "%PYTHON%"=="" set PYTHON=C:\Users\Jose\AppData\Local\Programs\Python\Python313\python.exe

echo Usando Python: %PYTHON%
echo.

:: Instalar dependencias (requirements.txt) + PyInstaller
echo [1/4] Instalando dependencias...
"%PYTHON%" -m pip install -r requirements.txt pyinstaller --quiet
echo       OK
echo.

:: Limpiar builds anteriores
echo [2/4] Limpiando builds anteriores...
if exist dist\aIBechos rmdir /s /q dist\aIBechos
if exist build rmdir /s /q build
echo       OK
echo.

:: Construir con PyInstaller usando aIBechos.spec
echo [3/4] Compilando a .exe (puede tardar 1-2 min)...
"%PYTHON%" -m PyInstaller --noconfirm aIBechos.spec

if errorlevel 1 (
    echo ERROR al compilar. Revisa los mensajes de arriba.
    pause
    exit /b 1
)
echo       OK
echo.

:: Crear acceso directo .lnk en el Escritorio
echo [4/4] Creando acceso directo en el Escritorio...
set EXE_PATH=%~dp0dist\aIBechos\aIBechos.exe
set WORK_DIR=%~dp0dist\aIBechos

powershell -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\aIBechos.lnk');" ^
  "$s.TargetPath='%EXE_PATH%';" ^
  "$s.WorkingDirectory='%WORK_DIR%';" ^
  "$s.Description='aIBechos - Renombrador de series y peliculas';" ^
  "$s.Save()"
echo       OK
echo.

echo ============================================
echo   Listo! Ejecutable en: dist\aIBechos\
echo   Acceso directo creado en el Escritorio
echo ============================================
echo.
echo Iniciando la aplicacion...
start "" "%EXE_PATH%"
pause
