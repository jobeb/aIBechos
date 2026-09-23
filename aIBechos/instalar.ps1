# aIBechos - Instalador PowerShell
# Ejecutar con: powershell -ExecutionPolicy Bypass -File instalar.ps1

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  aIBechos - Instalador Windows" -ForegroundColor Cyan
Write-Host "============================================`n"

# Buscar Python: comando py, python, o ruta directa en AppData/Program Files
$python = $null

foreach ($cmd in @("py", "python", "python3")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3") {
            $python = $cmd
            Write-Host "Encontrado: $cmd -> $ver" -ForegroundColor Green
            break
        }
    } catch {}
}

# Si no se encontró, buscar en rutas típicas de instalación
if (-not $python) {
    $searchPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "$env:APPDATA\Python\Python3*\python.exe",
        "C:\Python3*\python.exe",
        "C:\Program Files\Python3*\python.exe",
        "C:\Users\*\AppData\Local\Programs\Python\Python3*\python.exe"
    )
    foreach ($pattern in $searchPaths) {
        $found = Get-Item $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) {
            $python = $found.FullName
            Write-Host "Encontrado en: $python" -ForegroundColor Green
            break
        }
    }
}

if (-not $python) {
    Write-Host "ERROR: Python no encontrado." -ForegroundColor Red
    Write-Host "Descarga Python 3 desde: https://www.python.org/downloads/" -ForegroundColor Yellow
    Read-Host "Pulsa Enter para salir"
    exit 1
}

Write-Host "`nInstalando dependencias..." -ForegroundColor Yellow
& $python -m pip install --upgrade pip --quiet
# requirements.txt es la unica fuente de verdad (tambien la usa el
# instalador de macOS) -- instalar paquetes sueltos a mano aqui hacia que
# este script se desincronizara y dejara de instalar keyring, con lo que
# la app no llegaba ni a arrancar (config.py importa keyring sin proteger).
& $python -m pip install -r "$PSScriptRoot\requirements.txt"

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  Instalacion completada!" -ForegroundColor Green
Write-Host "============================================`n"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Crear acceso directo en el Escritorio
Write-Host "Creando acceso directo en el Escritorio..." -ForegroundColor Yellow
try {
    $desktopPath = [System.Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktopPath "aIBechos.lnk"

    # Resolver ruta absoluta del ejecutable Python
    $pythonExe = (Get-Command $python -ErrorAction SilentlyContinue)?.Source
    if (-not $pythonExe) { $pythonExe = $python }

    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($shortcutPath)
    $Shortcut.TargetPath    = $pythonExe
    $Shortcut.Arguments     = "`"$scriptDir\main.py`""
    $Shortcut.WorkingDirectory = $scriptDir
    $Shortcut.Description   = "aIBechos - Renombrador de series y películas"
    $iconPath = Join-Path $scriptDir "iconoPrincipal.ico"
    if (Test-Path $iconPath) {
        $Shortcut.IconLocation = "$iconPath,0"
    } else {
        $Shortcut.IconLocation = "$pythonExe,0"
    }
    $Shortcut.Save()
    Write-Host "Acceso directo creado en: $shortcutPath" -ForegroundColor Green
} catch {
    Write-Host "No se pudo