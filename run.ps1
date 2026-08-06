$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

function Test-AppPython([string]$candidate) {
    if (-not $candidate) { return $false }
    if ([System.IO.Path]::IsPathRooted($candidate) -and -not (Test-Path -LiteralPath $candidate)) {
        return $false
    }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & $candidate -c "import fastapi, uvicorn" 2>&1 | Out-Null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

$venvPython = Join-Path $projectRoot 'Scripts\python.exe'
$candidates = @($venvPython)
$systemPythons = Get-ChildItem -Path "$env:ProgramFiles\Python\Python*\python.exe" -File -ErrorAction SilentlyContinue
$candidates += $systemPythons.FullName
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) { $candidates += $pythonCommand.Source }

$pythonExecutable = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (Test-AppPython $candidate) {
        $pythonExecutable = $candidate
        break
    }
}

if (-not $pythonExecutable) {
    Write-Error 'No Python environment with FastAPI and Uvicorn was found.'
    Write-Host 'Install dependencies with: .\Scripts\python.exe -m pip install -r requirements.txt'
    exit 1
}

if ($pythonExecutable -ne $venvPython) {
    Write-Warning "Local venv dependencies are missing; using $pythonExecutable"
}

Write-Host "Digital Factory starting: http://127.0.0.1:8000"
& $pythonExecutable -m uvicorn app.main:app --host 0.0.0.0 --port 8000
