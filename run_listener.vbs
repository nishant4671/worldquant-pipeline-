Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\HP\worldquant-pipeline"
WshShell.Run """C:\Users\HP\worldquant-pipeline\venv\Scripts\pythonw.exe"" main.py listen", 0, False
Set WshShell = Nothing
