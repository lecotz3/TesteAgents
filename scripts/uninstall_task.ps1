# Removes the desk tasks from Windows Task Scheduler.
foreach ($name in "PolymarketDesk", "PolymarketDeskMonthly", "PolymarketDeskMemes", "PolymarketDeskMemesExits") {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removida: $name"
    }
}
