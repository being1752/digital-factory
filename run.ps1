param(
    [switch]$BackendOnly,
    [switch]$SkipFrontendInstall
)

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

$frontendProcess = $null
$frontendReused = $false
if (-not $BackendOnly) {
    $frontendRoot = Join-Path $projectRoot 'frontend'
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $nodeCommand -or -not $npmCommand) {
        Write-Error 'Node.js and npm are required for the frontend. Use -BackendOnly to start only the API.'
        exit 1
    }

    $uniCli = Join-Path $frontendRoot 'node_modules\@dcloudio\vite-plugin-uni\bin\uni.js'
    if (-not (Test-Path -LiteralPath $uniCli -PathType Leaf)) {
        if ($SkipFrontendInstall) {
            Write-Error 'Frontend dependencies are missing. Run: cd frontend; npm install'
            exit 1
        }
        Write-Host 'Installing frontend dependencies...'
        & $npmCommand.Source install --prefix $frontendRoot
        if ($LASTEXITCODE -ne 0) {
            Write-Error 'Frontend dependency installation failed.'
            exit $LASTEXITCODE
        }
    }

    $frontendPort = 5173
    $listener = Get-NetTCPConnection -LocalPort $frontendPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
        $commandLine = [string]$owner.CommandLine
        $expectedRoot = [System.IO.Path]::GetFullPath($frontendRoot)
        $isProjectFrontend = $commandLine.IndexOf($expectedRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        if ($isProjectFrontend) {
            try {
                $response = Invoke-WebRequest -Uri "http://127.0.0.1:$frontendPort" -UseBasicParsing -TimeoutSec 5
                $frontendReused = $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
            } catch {
                $frontendReused = $false
            }
        }
        if (-not $frontendReused) {
            $ownerName = if ($owner.Name) { $owner.Name } else { 'unknown process' }
            Write-Error "Frontend port $frontendPort is occupied by PID $($listener.OwningProcess) ($ownerName). Stop that process or free the port, then retry."
            exit 1
        }
    }

    if ($frontendReused) {
        Write-Host "Digital Factory frontend already running: http://127.0.0.1:$frontendPort"
    } else {
        $logDirectory = Join-Path $projectRoot 'data\logs'
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $frontendOutput = Join-Path $logDirectory 'frontend-dev.log'
        $frontendError = Join-Path $logDirectory 'frontend-dev-error.log'
        $frontendStart = @{
            FilePath = $nodeCommand.Source
            ArgumentList = @($uniCli)
            WorkingDirectory = $frontendRoot
            WindowStyle = 'Hidden'
            RedirectStandardOutput = $frontendOutput
            RedirectStandardError = $frontendError
            PassThru = $true
        }
        $frontendProcess = Start-Process @frontendStart
        Start-Sleep -Seconds 2
        if ($frontendProcess.HasExited) {
            Write-Error "Frontend failed to start. See $frontendError"
            exit 1
        }
        Write-Host "Digital Factory frontend: http://127.0.0.1:$frontendPort"
    }
}

Write-Host 'Digital Factory backend:  http://127.0.0.1:8000'
try {
    & $pythonExecutable -m uvicorn app.main:app --host 0.0.0.0 --port 8000
} finally {
    if ($frontendProcess -and -not $frontendProcess.HasExited) {
        Stop-Process -Id $frontendProcess.Id
    }
}
