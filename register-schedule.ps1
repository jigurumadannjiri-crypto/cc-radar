<#
  cc-radar  毎朝の自動実行をWindowsタスクスケジューラに登録する。
  既定: 毎朝 07:30 に run.bat を実行。

  使い方（PowerShellで）:
      powershell -ExecutionPolicy Bypass -File .\register-schedule.ps1
      powershell -ExecutionPolicy Bypass -File .\register-schedule.ps1 -Time "08:00"
      powershell -ExecutionPolicy Bypass -File .\register-schedule.ps1 -Unregister
#>
param(
  [string]$Time = "07:30",
  [string]$TaskName = "cc-radar-daily",
  [switch]$Unregister
)

$here   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$runBat = Join-Path $here "run.bat"

if ($Unregister) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "[cc-radar] タスク '$TaskName' を削除しました。"
  return
}

if (-not (Test-Path $runBat)) {
  Write-Error "run.bat が見つかりません: $runBat"
  return
}

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$runBat`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Settings $settings -Description "Claude Code 最新情報を毎朝収集 (cc-radar)" -Force | Out-Null

Write-Host "[cc-radar] 毎朝 $Time に '$TaskName' を実行するよう登録しました。"
Write-Host "  確認: タスクスケジューラ → タスク スケジューラ ライブラリ → $TaskName"
Write-Host "  解除: powershell -ExecutionPolicy Bypass -File .\register-schedule.ps1 -Unregister"
