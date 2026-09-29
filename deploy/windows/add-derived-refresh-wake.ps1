# Preserve the existing host-only task; add wake slots for derived calculations.
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ((Get-TimeZone).Id -ne 'Eastern Standard Time') { throw 'Eastern Windows timezone required.' }
$taskName = 'QuantData-WSLHost'
$task = Get-ScheduledTask -TaskPath '\' -TaskName $taskName
if (@($task.Actions).Count -ne 1 -or
    $task.Actions[0].Arguments -notlike '*-d Ubuntu -u volatility --exec /usr/bin/true*') {
    throw 'Unexpected host task action; no update performed.'
}
$before = Export-ScheduledTask -TaskPath '\' -TaskName $taskName
[xml]$document = $before
$namespace = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
$ns = [System.Xml.XmlNamespaceManager]::new($document.NameTable)
$ns.AddNamespace('t', $namespace)
$triggers = $document.SelectSingleNode('/t:Task/t:Triggers', $ns)
if ($null -eq $triggers) { throw 'Existing triggers are unavailable.' }
$slots = @(
    @{ Id='DerivedNightWake'; Time='23:28:00'; Schedule='<ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/></DaysOfWeek></ScheduleByWeek>' },
    @{ Id='DerivedMorningWake'; Time='06:28:00'; Schedule='<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>' }
)
$added = 0
foreach ($slot in $slots) {
    $found = $document.SelectSingleNode("/t:Task/t:Triggers/t:CalendarTrigger[@id='$($slot.Id)']", $ns)
    [xml]$fragment = "<CalendarTrigger xmlns='$namespace' id='$($slot.Id)'><StartBoundary>2026-09-21T$($slot.Time)</StartBoundary><Enabled>true</Enabled>$($slot.Schedule)</CalendarTrigger>"
    if ($null -ne $found) {
        if ($found.OuterXml -cne $document.ImportNode($fragment.DocumentElement, $true).OuterXml) {
            throw 'Existing derived wake trigger differs.'
        }
        continue
    }
    [void]$triggers.AppendChild($document.ImportNode($fragment.DocumentElement, $true))
    $added++
}
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$evidence = Join-Path $root '.local/derived-refresh-implementation-20260921'
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
$backup = Join-Path $evidence 'host-task-before.xml'
if (-not (Test-Path -LiteralPath $backup)) { $before | Set-Content -LiteralPath $backup -Encoding Unicode }
$proposed = Join-Path $evidence 'host-task-proposed.xml'
$document.OuterXml | Set-Content -LiteralPath $proposed -Encoding Unicode
# Match all non-trigger sections and each retained trigger, including order.
[xml]$original = $before
foreach ($name in @('Actions','Principals','Settings')) {
    if ($document.SelectSingleNode("/t:Task/t:$name",$ns).OuterXml -cne
        $original.SelectSingleNode("/t:Task/t:$name",$ns).OuterXml) { throw 'Unrelated task settings changed.' }
}
$originalTriggers = @($original.SelectSingleNode('/t:Task/t:Triggers',$ns).ChildNodes)
for ($i=0; $i -lt $originalTriggers.Count; $i++) {
    if ($originalTriggers[$i].OuterXml -cne $triggers.ChildNodes[$i].OuterXml) { throw 'Existing trigger changed.' }
}
if ($Apply -and $added -gt 0) {
    Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Xml $document.OuterXml -Force | Out-Null
}
[pscustomobject]@{Applied=[bool]$Apply; AddedSlots=$added; ExistingTriggersPreserved=$originalTriggers.Count; TaskName=$taskName; ProposedXml=$proposed} | ConvertTo-Json -Compress
