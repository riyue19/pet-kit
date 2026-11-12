# 一键启动桌宠：后台启动后端（若尚未运行）+ 前端（npm run dev），无控制台窗口。
# 托盘「退出」关闭桌宠后，由本脚本启动的后端也会一并关闭；日志在 %LOCALAPPDATA%\pet-kit\logs\。
# 用法：双击桌面快捷方式（由 create-shortcut.ps1 生成），或
#   powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File launcher\start-pet.ps1

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $env:LOCALAPPDATA 'pet-kit'
$LogDir = Join-Path $RuntimeDir 'logs'
$EndpointFile = Join-Path $RuntimeDir 'backend.json'
$BackendWaitSeconds = 30

Add-Type -AssemblyName PresentationFramework
function Show-Error([string]$msg) {
    [void][System.Windows.MessageBox]::Show($msg, '桌宠启动器', 'OK', 'Error')
}

function Test-Backend {
    if (-not (Test-Path $EndpointFile)) { return $false }
    try {
        $info = Get-Content $EndpointFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $headers = @{ Authorization = "Bearer $($info.token)" }
        Invoke-RestMethod -Uri "http://127.0.0.1:$($info.port)/api/health" -Headers $headers -TimeoutSec 2 | Out-Null
        return $true
    } catch { return $false }
}

# Hidden child: cmd redirects both streams into one log file.
function Start-Hidden([string]$dir, [string]$command, [string]$log) {
    Start-Process -FilePath $env:ComSpec -ArgumentList '/d', '/c', "$command > `"$log`" 2>&1" `
        -WorkingDirectory $dir -WindowStyle Hidden -PassThru
}

# Only one launcher at a time: two backends racing would overwrite each other's backend.json.
$mutex = New-Object System.Threading.Mutex($false, 'Local\pet-kit-launcher')
if (-not $mutex.WaitOne(0)) { exit 0 }

$backend = $null
try {
    foreach ($tool in 'uv', 'npm') {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            Show-Error "找不到 $tool 命令，请确认已安装并加入 PATH。"
            exit 1
        }
    }
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $env:PYTHONIOENCODING = 'utf-8'
    $backendLog = Join-Path $LogDir 'backend.log'
    $frontendLog = Join-Path $LogDir 'frontend.log'

    # A backend started elsewhere (e.g. by hand in a terminal) is reused and left running on exit.
    if (-not (Test-Backend)) {
        $backend = Start-Hidden (Join-Path $Root 'backend') 'uv run python -m api' $backendLog
        $deadline = (Get-Date).AddSeconds($BackendWaitSeconds)
        while (-not (Test-Backend)) {
            if ($backend.HasExited -or (Get-Date) -gt $deadline) {
                # The pet still starts; it shows "后端未连接" and keeps retrying.
                Show-Error "后端启动失败，桌宠将以离线状态运行。`n日志：$backendLog"
                break
            }
            Start-Sleep -Milliseconds 300
        }
    }

    $frontend = Start-Hidden (Join-Path $Root 'frontend') 'npm run dev' $frontendLog
    $started = Get-Date
    $frontend.WaitForExit()
    # Electron single-instance lock makes a duplicate exit at once with code 0; only report real failures.
    if ($frontend.ExitCode -ne 0 -and ((Get-Date) - $started).TotalSeconds -lt 60) {
        Show-Error "前端启动失败（退出码 $($frontend.ExitCode)）。`n日志：$frontendLog"
    }
} finally {
    if ($backend -and -not $backend.HasExited) {
        # Kill the whole tree (cmd -> uv -> python).
        & taskkill.exe /T /F /PID $backend.Id 2>&1 | Out-Null
    }
    # A force-killed backend can't clean up its endpoint file; drop it if its process is gone.
    if ($backend -and (Test-Path $EndpointFile)) {
        try {
            $info = Get-Content $EndpointFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if (-not (Get-Process -Id $info.pid -ErrorAction SilentlyContinue)) { Remove-Item $EndpointFile -Force }
        } catch { }
    }
    $mutex.ReleaseMutex()
}
