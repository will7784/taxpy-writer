' Ejecuta sync_vault.py sin mostrar ventana de consola
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c """"C:\Program Files\Python313\python.exe"""" sync_vault.py >> sync_vault.log 2>&1", 0, True
Set WshShell = Nothing
