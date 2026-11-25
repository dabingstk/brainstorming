# 注册每小时采集任务（在“本机执行”的 Trae 会话或 PowerShell 里运行一次）
$root = 'C:\shop-ocr'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\run.ps1`""

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 60)

# 不指定 -User/密码 => 仅在当前用户登录时运行，保证 ego(lite) 的桌面会话可用
Register-ScheduledTask -TaskName 'ShopCollectorHourly' `
    -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
