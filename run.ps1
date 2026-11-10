$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "Virtual environment not found: $pythonExecutable"
}
Push-Location -LiteralPath $projectDirectory
try {
    & $pythonExecutable -m src.app @args
    if ($LASTEXITCODE -ne 0) {
        throw "AutoBloodWeb exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
