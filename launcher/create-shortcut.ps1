# 在桌面创建「桌宠」快捷方式，指向 start-pet.ps1。项目目录移动后重新运行一次即可。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File launcher\create-shortcut.ps1

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot 'start-pet.ps1'
$lnkPath = Join-Path ([Environment]::GetFolderPath('Desktop')) '桌宠.lnk'

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$lnk.WorkingDirectory = $Root
$lnk.WindowStyle = 7  # minimized, so the PowerShell host doesn't flash before hiding
$lnk.Description = '启动 Live2D 桌宠（后端 + 前端）'
$electron = Join-Path $Root 'frontend\node_modules\electron\dist\electron.exe'
if (Test-Path $electron) { $lnk.IconLocation = "$electron,0" }
$lnk.Save()
Write-Host "已创建：$lnkPath"
