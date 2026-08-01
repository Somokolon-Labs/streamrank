# StreamRank local stack (Windows / PowerShell).
#
#   .\scripts\dev.ps1 setup    # venv + deps + dataset + training
#   .\scripts\dev.ps1 up       # start the API on :8200
#   .\scripts\dev.ps1 down
#   .\scripts\dev.ps1 smoke    # 15-check end-to-end suite incl. the A/B run
#   .\scripts\dev.ps1 train    # retrain from the current interaction log
#
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'up', 'down', 'smoke', 'train', 'data', 'status')]
    [string]$Command = 'up',
    [int]$Users = 120,
    [int]$Steps = 5
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$api = 'http://127.0.0.1:8200'
$pidFile = Join-Path $root '.dev-pid'

switch ($Command) {
    'setup' {
        if (-not (Test-Path $python)) { python -m venv (Join-Path $root '.venv') }
        & $python -m pip install --upgrade pip
        & $python -m pip install -r (Join-Path $root 'requirements.txt')
        & $python (Join-Path $root 'ml\generate_data.py')
        & $python (Join-Path $root 'ml\train.py')
        if (-not (Test-Path (Join-Path $root '.env'))) { Copy-Item (Join-Path $root '.env.example') (Join-Path $root '.env') }
        Write-Host 'setup complete - next: .\scripts\dev.ps1 up' -ForegroundColor Green
    }

    'data' { & $python (Join-Path $root 'ml\generate_data.py') @args }

    'train' { & $python (Join-Path $root 'ml\train.py') @args }

    'up' {
        $process = Start-Process -FilePath $python -ArgumentList '-m uvicorn app.main:app --host 127.0.0.1 --port 8200' -WorkingDirectory $root -PassThru -WindowStyle Minimized
        $process.Id | Set-Content $pidFile
        Start-Sleep -Seconds 6
        Write-Host "api      $api/docs" -ForegroundColor Green
        Write-Host "console  cd web; npm run dev  ->  http://localhost:3100" -ForegroundColor Green
    }

    'down' {
        if (Test-Path $pidFile) {
            $processId = Get-Content $pidFile
            try { Stop-Process -Id $processId -Force; Write-Host "stopped $processId" } catch { Write-Host 'already stopped' -ForegroundColor DarkGray }
            Remove-Item $pidFile -Force
        }
    }

    'status' {
        try {
            $health = Invoke-RestMethod -Uri "$api/health/ready" -TimeoutSec 5
            $health | ConvertTo-Json -Depth 4
        }
        catch { Write-Host 'api DOWN' -ForegroundColor Red }
    }

    'smoke' { & $python (Join-Path $root 'scripts\smoke.py') --users $Users --steps $Steps }
}
