# Registers the desk in Windows Task Scheduler for the current user (runs only while logged in,
# because `claude` needs your login). Re-running replaces the existing tasks.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Nao encontrei $pythonw. Rode 'uv sync' na raiz do repositorio primeiro." }

$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

$cycle = New-ScheduledTaskAction -Execute $pythonw -Argument "-m desk cycle" -WorkingDirectory $repo
$every10 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration ([TimeSpan]::FromDays(3650))
Register-ScheduledTask -TaskName "PolymarketDesk" -Action $cycle -Trigger $every10 -Settings $settings `
    -Description "Polymarket Desk: ciclo a cada 10 min" -Force | Out-Null

$monthly = New-ScheduledTaskAction -Execute $pythonw -Argument "-m desk monthly" -WorkingDirectory $repo
$daily = New-ScheduledTaskTrigger -Daily -At 9am
Register-ScheduledTask -TaskName "PolymarketDeskMonthly" -Action $monthly -Trigger $daily -Settings $settings `
    -Description "Polymarket Desk: relatorio mensal (roda todo dia, relata 1x por mes)" -Force | Out-Null

$memes = New-ScheduledTaskAction -Execute $pythonw -Argument "-m desk memes" -WorkingDirectory $repo
$every3 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 3) -RepetitionDuration ([TimeSpan]::FromDays(3650))
Register-ScheduledTask -TaskName "PolymarketDeskMemes" -Action $memes -Trigger $every3 -Settings $settings `
    -Description "Desk: agente de memecoins (Solana) a cada 3 min" -Force | Out-Null

$exits = New-ScheduledTaskAction -Execute $pythonw -Argument "-m desk memes-exits" -WorkingDirectory $repo
$every1 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration ([TimeSpan]::FromDays(3650))
Register-ScheduledTask -TaskName "PolymarketDeskMemesExits" -Action $exits -Trigger $every1 -Settings $settings `
    -Description "Desk: memecoins, so precos e regras de saida, a cada 1 min" -Force | Out-Null

Write-Host "Tarefas registradas: PolymarketDesk (a cada 10 min), PolymarketDeskMemes (memecoins, a cada 3 min),"
Write-Host "PolymarketDeskMemesExits (saidas das memecoins, a cada 1 min) e PolymarketDeskMonthly (diaria, relata 1x por mes)."
Write-Host "Para remover: powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1"
