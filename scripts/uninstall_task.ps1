# Removes the desk tasks from Windows Task Scheduler. A cycle already running finishes; only the panel is stopped.
foreach ($name in "PolymarketDesk", "PolymarketDeskMonthly", "PolymarketDeskMemes", "PolymarketDeskMemesExits", "PolymarketDeskDashboard") {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        if ($name -eq "PolymarketDeskDashboard") { Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue }
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removida: $name"
    }
}
$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Painel dos robos.url"
if (Test-Path $shortcut) { Remove-Item $shortcut }
