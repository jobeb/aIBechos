#define MyAppName "aIBechos"

;; Version real, leida de core/version.py -- para que el instalador nunca
;; se quede desincronizado de la version de la app (antes se quedaba
;; clavada en "1.0" mientras la app ya iba por 1.1.0). Se probo primero
;; pasarla por linea de comandos (/DMyAppVersion) desde crear_instalador.bat,
;; pero si ese paso fallaba en silencio (entorno del usuario, quoting de
;; cmd.exe...) el instalador se compilaba con la version VACIA y el propio
;; Inno Setup lo rechazaba con "must include an AppVersion directive" --
;; leerlo aqui mismo, sin depender de nada externo, evita ese fallo entero.
;; OJO: #define dentro de un #sub/#for NO se propaga fuera de esa llamada
;; en este Inno Setup (probado) -- por eso el bucle va desenrollado a mano
;; en vez de con #for, y por eso hay margen de sobra (10 lineas) por si el
;; docstring de core/version.py creciera.
#define VersionFileHandle FileOpen(AddBackslash(SourcePath) + "core\version.py")
#define VLine1 FileRead(VersionFileHandle)
#define VLine2 FileRead(VersionFileHandle)
#define VLine3 FileRead(VersionFileHandle)
#define VLine4 FileRead(VersionFileHandle)
#define VLine5 FileRead(VersionFileHandle)
#define VLine6 FileRead(VersionFileHandle)
#define VLine7 FileRead(VersionFileHandle)
#define VLine8 FileRead(VersionFileHandle)
#define VLine9 FileRead(VersionFileHandle)
#define VLine10 FileRead(VersionFileHandle)
#expr FileClose(VersionFileHandle)

#if Pos("__version__", VLine1) > 0
  #define VersionLine VLine1
#elif Pos("__version__", VLine2) > 0
  #define VersionLine VLine2
#elif Pos("__version__", VLine3) > 0
  #define VersionLine VLine3
#elif Pos("__version__", VLine4) > 0
  #define VersionLine VLine4
#elif Pos("__version__", VLine5) > 0
  #define VersionLine VLine5
#elif Pos("__version__", VLine6) > 0
  #define VersionLine VLine6
#elif Pos("__version__", VLine7) > 0
  #define VersionLine VLine7
#elif Pos("__version__", VLine8) > 0
  #define VersionLine VLine8
#elif Pos("__version__", VLine9) > 0
  #define VersionLine VLine9
#elif Pos("__version__", VLine10) > 0
  #define VersionLine VLine10
#else
  #define VersionLine ""
#endif

;; Valor de emergencia si por lo que sea no se encontro la linea -- para
;; que el compilador nunca se quede sin AppVersion en absoluto.
#define MyAppVersion "0.0.0-dev"
#if Len(VersionLine) > 0
  #define VQ1 Pos('"', VersionLine)
  #define VRest Copy(VersionLine, VQ1 + 1, Len(VersionLine) - VQ1)
  #define VQ2 Pos('"', VRest)
  #define MyAppVersion Copy(VRest, 1, VQ2 - 1)
#endif

#define MyAppPublisher "Jose"
#define MyAppExeName "aIBechos.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=aIBechos_Setup_{#MyAppVersion}
SetupIconFile=iconoPrincipal.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.chm
RestartApplications=yes
AppMutex=aIBechosSingletonMutex

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el &Escritorio"; GroupDescription: "Iconos adicionales:"

[Files]
Source: "dist\aIBechos\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  // Intento silencioso de cerrar la app si sigue en ejecución (bandeja,
  // pystray, etc. — Restart Manager a veces no la detecta, ver reporte de
  // Victor con el diálogo "no pudo cerrar"). taskkill /f es inmediato y no
  // pide confirmación; si no está en ejecución, devuelve código !=0 y se ignora.
  Exec('taskkill.exe', '/f /im aIBechos.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // Pequeña espera para que el bloqueo de fichero .lock se libere
  Sleep(800);
  Result := '';
end;

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Iniciar {#MyAppName}"; Flags: nowait postinstall skipifsilent
