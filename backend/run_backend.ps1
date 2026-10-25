# Start the local health API on http://127.0.0.1:8765 (this PC only).
#   powershell -ExecutionPolicy Bypass -File run_backend.ps1 [-Port 8765]
param([int]$Port = 8765)
Set-Location $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port $Port
