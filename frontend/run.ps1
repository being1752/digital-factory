$ErrorActionPreference = 'Stop'

$frontendRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $frontendRoot

$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $nodeCommand -or -not $npmCommand) {
    Write-Error 'Node.js and npm are required. Install Node.js 20 LTS or newer first.'
    exit 1
}

$uniCli = Join-Path $frontendRoot 'node_modules\@dcloudio\vite-plugin-uni\bin\uni.js'
if (-not (Test-Path -LiteralPath $uniCli -PathType Leaf)) {
    Write-Host 'Installing frontend dependencies...'
    & $npmCommand.Source install
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'Frontend dependency installation failed.'
        exit $LASTEXITCODE
    }
}

Write-Host 'Digital Factory frontend starting: http://127.0.0.1:5173'
& $nodeCommand.Source $uniCli
