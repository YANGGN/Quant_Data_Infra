# Host-only repair for the approved September 19, 2026 WSL scheduling fix.
# Without -Apply, emits the proposed task XML and changes nothing.
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ((Get-TimeZone).Id -ne 'Eastern Standard Time') { throw 'Review wake times for the current Windows timezone.' }
$taskName = 'QuantData-WSLHost'
$legacyNames = @('QuantData-CompanyWeekly','QuantData-Expectations','QuantData-MacroDaily',
    'QuantData-MacroMonthly','QuantData-MarketClose','QuantData-NewsHourly',
    'QuantData-OptionsClose','QuantData-SecDaily')
$legacyArgumentPrefix = '-d Ubuntu -u volatility --cd /home/volatility/Python_Projects/Quant_Data_Infra --exec /bin/bash /home/volatility/Python_Projects/Quant_Data_Infra/ops/run_scheduled_collector.sh '
$legacyJobs = @{
    'QuantData-CompanyWeekly'='company-weekly'; 'QuantData-Expectations'='expectations'
    'QuantData-MacroDaily'='macro-daily'; 'QuantData-MacroMonthly'='macro-monthly'
    'QuantData-MarketClose'='market-close'; 'QuantData-NewsHourly'='news-hourly'
    'QuantData-OptionsClose'='options-close'; 'QuantData-SecDaily'='sec-daily'
}
$legacyTasks = @($legacyNames | ForEach-Object { Get-ScheduledTask -TaskPath '\' -TaskName $_ })
foreach ($task in $legacyTasks) {
    if (@($task.Actions).Count -ne 1 -or
        $task.Actions[0].Execute -ne 'C:\Windows\System32\wsl.exe' -or
        $task.Actions[0].Arguments -cne ($legacyArgumentPrefix + $legacyJobs[$task.TaskName])) {
        throw "Unexpected action in $($task.TaskName); review before changing it."
    }
}
if (Get-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw "$taskName already exists; inspect it rather than replacing it."
}
$configPath = Join-Path $env:USERPROFILE '.wslconfig'
if (Test-Path -LiteralPath $configPath) { throw 'Existing .wslconfig requires a preserving merge.' }
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$boundary = '2026-09-19'
$triggers = [System.Collections.Generic.List[string]]::new()
$triggers.Add("<LogonTrigger><Enabled>true</Enabled><UserId>$sid</UserId><Delay>PT15S</Delay></LogonTrigger>")
# The hourly wake also covers the fixed UTC Equibles backfill schedule.
$triggers.Add("<TimeTrigger><Repetition><Interval>PT1H</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><StartBoundary>${boundary}T00:08:00</StartBoundary><Enabled>true</Enabled></TimeTrigger>")
$weekdays = '<Monday/><Tuesday/><Wednesday/><Thursday/><Friday/>'
foreach ($time in @('07:13:00','08:13:00','08:43:00','09:03:00','16:18:00','17:58:00','18:28:00','18:58:00','19:58:00')) {
    $triggers.Add("<CalendarTrigger><StartBoundary>${boundary}T$time</StartBoundary><Enabled>true</Enabled><ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek>$weekdays</DaysOfWeek></ScheduleByWeek></CalendarTrigger>")
}
$triggers.Add("<CalendarTrigger><StartBoundary>${boundary}T04:58:00</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>")
$triggers.Add("<CalendarTrigger><StartBoundary>${boundary}T01:58:00</StartBoundary><Enabled>true</Enabled><ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek><Saturday/></DaysOfWeek></ScheduleByWeek></CalendarTrigger>")
$months = '<January/><February/><March/><April/><May/><June/><July/><August/><September/><October/><November/><December/>'
$triggers.Add("<CalendarTrigger><StartBoundary>${boundary}T10:03:00</StartBoundary><Enabled>true</Enabled><ScheduleByMonthDayOfWeek><Weeks><Week>1</Week></Weeks><DaysOfWeek><Friday/></DaysOfWeek><Months>$months</Months></ScheduleByMonthDayOfWeek></CalendarTrigger>")
$wakeArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -Command "& ''C:\Windows\System32\wsl.exe'' -d Ubuntu -u volatility --exec /usr/bin/true; exit $LASTEXITCODE"'
$escapedArguments = [System.Security.SecurityElement]::Escape($wakeArguments)
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
<RegistrationInfo><Description>Start Ubuntu two minutes before existing Quant Data timer slots. Host wake only; Linux timers own all fetching. Eastern Windows timezone required.</Description></RegistrationInfo>
<Triggers>$($triggers -join "`n")</Triggers>
<Principals><Principal id="Owner"><UserId>$sid</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable><IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings><AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>true</Hidden><RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>true</WakeToRun><ExecutionTimeLimit>PT3M</ExecutionTimeLimit><Priority>7</Priority></Settings>
<Actions Context="Owner"><Exec><Command>C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe</Command><Arguments>$escapedArguments</Arguments></Exec></Actions>
</Task>
"@
[xml]$parsed = $xml
if (-not $Apply) { $xml; return }

# Back up exact task definitions before mutation; never delete legacy definitions.
$repairRoot = Join-Path $env:LOCALAPPDATA ('QuantDataInfra\wsl-host-repair-' + [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
New-Item -ItemType Directory -Path $repairRoot | Out-Null
$legacyState = @()
foreach ($task in $legacyTasks) {
    Export-ScheduledTask -TaskPath '\' -TaskName $task.TaskName |
        Set-Content -LiteralPath (Join-Path $repairRoot ($task.TaskName + '.xml')) -Encoding Unicode
    $legacyState += [pscustomobject]@{Name=$task.TaskName;Enabled=$task.Settings.Enabled}
}
[pscustomobject]@{ConfigPath=$configPath;ConfigPreviouslyExisted=$false;LegacyTasks=$legacyState;NewTask=$taskName} |
    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $repairRoot 'before.json') -Encoding UTF8
$xml | Set-Content -LiteralPath (Join-Path $repairRoot 'proposed-task.xml') -Encoding Unicode

$configCreated = $false
$newTaskCreated = $false
try {
    # Supported WSL settings; neither setting changes Windows sleep policy.
    $configText = "[general]`r`ninstanceIdleTimeout=-1`r`n`r`n[wsl2]`r`nvmIdleTimeout=-1`r`n"
    $stream = [System.IO.File]::Open($configPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
    try {
        $configCreated = $true
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($configText)
        $stream.Write($bytes,0,$bytes.Length)
    } finally { $stream.Dispose() }
    Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Xml $xml | Out-Null
    $newTaskCreated = $true
    foreach ($task in $legacyTasks) {
        Disable-ScheduledTask -TaskPath '\' -TaskName $task.TaskName | Out-Null
    }
} catch {
    $applyFailure = $_
    $rollbackErrors = [System.Collections.Generic.List[string]]::new()
    foreach ($state in $legacyState) {
        try {
            if ($state.Enabled) { Enable-ScheduledTask -TaskPath '\' -TaskName $state.Name | Out-Null }
        } catch { $rollbackErrors.Add("Restore $($state.Name): $($_.Exception.Message)") }
    }
    if ($newTaskCreated) {
        try { Unregister-ScheduledTask -TaskPath '\' -TaskName $taskName -Confirm:$false }
        catch { $rollbackErrors.Add("Remove new task: $($_.Exception.Message)") }
    }
    if ($configCreated) {
        try {
            if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($configPath)) -cne [Convert]::ToBase64String($bytes)) {
                throw 'Config bytes changed; preserved for manual recovery.'
            }
            Remove-Item -LiteralPath $configPath
        } catch { $rollbackErrors.Add("Restore .wslconfig: $($_.Exception.Message)") }
    }
    if ($rollbackErrors.Count) { Write-Warning ("Rollback needs attention: " + ($rollbackErrors -join '; ')) }
    throw $applyFailure
}
[pscustomobject]@{BackupDirectory=$repairRoot;TaskName=$taskName;LegacyDisabled=$legacyTasks.Count;ConfigPath=$configPath} |
    ConvertTo-Json | Tee-Object -FilePath (Join-Path $repairRoot 'installed.json')
