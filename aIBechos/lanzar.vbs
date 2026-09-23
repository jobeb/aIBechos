Dim python, projectDir, shell, fso, reqFile, markerFile, needsInstall, exitCode, f
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

python = "C:\Users\Jose\AppData\Local\Programs\Python\Python313\python.exe"
' Carpeta del proyecto = donde esté este script (no una ruta fija, que
' se queda obsoleta al mover o renombrar carpetas).
projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
reqFile = projectDir & "\requirements.txt"
' Marcador de la ultima instalacion que se hizo con exito -- comparar su
' fecha con la de requirements.txt para no volver a invocar "pip install"
' en CADA arranque. Aunque no haya nada que instalar, pip tiene que
' resolver todas las dependencias igualmente (comprobar version de cada
' paquete instalado) antes de decidir que no hace falta hacer nada, y
' eso por si solo tarda varios segundos -- visto de verdad: sumaba ~10s
' de mas a cada arranque de la app sin aportar nada la mayoria de veces.
markerFile = projectDir & "\.deps_installed"

needsInstall = True
If fso.FileExists(markerFile) And fso.FileExists(reqFile) Then
    If fso.GetFile(markerFile).DateLastModified >= fso.GetFile(reqFile).DateLastModified Then
        needsInstall = False
    End If
End If

If needsInstall Then
    exitCode = shell.Run(  _
        """" & python & """ -m pip install -r """ & reqFile & """ --quiet", 0, True)
    If exitCode = 0 Then
        ' Solo se marca como "ya instalado" si pip termino sin error --
        ' si fallo (por ejemplo sin conexion la primera vez), se reintenta
        ' en el proximo arranque en vez de dejarlo marcado como hecho.
        ' Si el marcador no se puede escribir (un antivirus mirandolo justo
        ' en ese momento, otra copia del lanzador arrancando a la vez...), la
        ' unica consecuencia es que el proximo arranque vuelva a pasar por
        ' pip. Lo que NO puede pasar es que eso impida abrir la aplicacion:
        ' antes el error cortaba el script aqui y no se llegaba a la linea
        ' que la lanza (visto de verdad: "Permiso denegado", codigo 800A0046).
        On Error Resume Next
        Set f = fso.CreateTextFile(markerFile, True)
        If Err.Number = 0 Then f.Close
        Err.Clear
        On Error GoTo 0
    End If
End If

' Lanzar la aplicacion
shell.CurrentDirectory = projectDir
shell.Run """" & python & """ """ & projectDir & "\main.py""", 0, False
