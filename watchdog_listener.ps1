# WorldQuant BRAIN Pipeline - Persistent Listener Watchdog
$workingDir = "C:\Users\HP\worldquant-pipeline"
Set-Location $workingDir

$listener = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*main.py listen*" }

if (-not $listener) {
    "$(Get-Date) - Listener died. Restarting..." | Out-File -FilePath (Join-Path $workingDir "listener_watchdog.log") -Append -Encoding utf8
    cscript.exe //nologo run_listener.vbs
}
