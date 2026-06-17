$ErrorActionPreference = "Stop"

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$python = "C:\Users\rog\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$pythonArgs = @("server.py")
if (-not (Test-Path $python)) {
  $python = "py"
  $pythonArgs = @("-3", "server.py")
}

if (-not (Test-Path ".\.env")) {
  Write-Host "No .env found. Copy .env.example to .env and edit it for production settings."
}

Write-Host "Daisy AIGC starting. Config source: .env plus current environment variables."
& $python @pythonArgs
