param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TestArgs
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$controlDir = Join-Path $repoRoot "control"
$pythonExe = Join-Path $controlDir ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found at '$pythonExe'. Create the control virtualenv first."
}

if (-not $env:DATABASE_URL) {
    $env:DATABASE_URL = "postgresql://ai_mesh_firewall:ai_mesh_firewall@127.0.0.1:5432/ai_mesh_firewall"
}
if (-not $env:CELERY_BROKER_URL) {
    $env:CELERY_BROKER_URL = "amqp://guest:guest@127.0.0.1:5772//"
}
if (-not $env:REDIS_URL) {
    $env:REDIS_URL = "redis://127.0.0.1:6379/0"
}

Push-Location $controlDir
try {
    if (-not $TestArgs -or $TestArgs.Count -eq 0) {
        $TestArgs = @("module2.tests")
    }
    & $pythonExe manage.py test @TestArgs
}
finally {
    Pop-Location
}
