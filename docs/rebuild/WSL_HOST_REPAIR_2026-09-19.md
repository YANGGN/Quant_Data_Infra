# WSL host scheduling repair - September 19, 2026

The user requested fixing repeated WSL shutdowns after reviewing the host diagnosis.
This authorizes retiring the eight stale Windows launch tasks, repairing host startup
and retention, and one quiet restart with verification. No manual provider fetch,
replay, new population, collector schedule change, credential change, or deployment
is part of this operation. Existing normal clock execution remains authorized.

## Observed failure

All eight enabled Windows QuantData tasks referenced the absent
`ops/run_scheduled_collector.sh` and returned 127. Overnight Ubuntu boots at
hourly :10 lasted about 35 seconds, with login/startup timeouts and orderly shutdown.
The current cold boot required 124 seconds: cloud-init waited for
systemd-networkd-wait-online, despite WSL-owned networking and empty netplan/networkd
configuration. Linger was already enabled. No .wslconfig existed.

## Concrete correction

- Create .wslconfig using Microsoft's supported general.instanceIdleTimeout=-1
  and wsl2.vmIdleTimeout=-1. These settings apply to this Windows user's WSL
  distributions. They do not prevent Windows sleep or a deliberate shutdown.
- Add Ubuntu's supported /etc/cloud/cloud-init.disabled flag. No unit file,
  networking configuration, or package is removed.
- Replace the eight obsolete Windows launchers with QuantData-WSLHost. Export
  exact original task XML before disabling them. Its only Linux action is
  /usr/bin/true. It never starts a collector service.
- Wake two minutes before every currently installed fetch slot: hourly :08,
  weekday 07:13/08:13/08:43/09:03/16:18/17:58/18:28/18:58/19:58, daily 04:58,
  Saturday 01:58, and first Friday monthly 10:03, plus current-user logon.
  Hourly :08 covers UTC news and Equibles minute-10 slots in Eastern time.
- Use the existing interactive Windows identity, least privilege, hidden launch,
  wake-to-run, and ignore overlapping host launches. No new credential is stored.

Windows must remain signed in. Wake depends on Windows/hardware wake support;
this change does not promise execution while powered off, hibernated, or signed out.
Changes to Windows timezone or collector calendars require reviewing wake times.
The unactivated Equibles incremental refresh is excluded.

## Validation and rollback

Impact: host lifecycle and wake timing only. Collector/store/access semantics,
network exposure, schema, and Linux unit bytes remain unchanged. Required checks:
PowerShell parsing, native Task Scheduler validation, one-year wake coverage across
both DST transitions, exact old-action guards, readback of retired/new tasks,
quiet restart, boot timing/user-manager/timer recovery, and boot identity preserved
during an idle interval longer than the old timeouts. Independent review is required.
No application full-suite trigger applies because security/host-access semantics
and all application algorithms remain unchanged. No manual provider smoke is included.

Windows backups go under %LOCALAPPDATA%/QuantDataInfra/wsl-host-repair-<UTC stamp>.
The installation script prints proposed XML without -Apply. Applying does not
restart WSL or manually run any collector. Failures roll back its Windows mutations.

For rollback, inspect before.json and the exported task definitions first; disable
and remove only QuantData-WSLHost, restore the recorded enabled states of the eight
original tasks, and remove .wslconfig only if it still matches the created bytes.
Remove the new cloud-init.disabled flag only if its recorded bytes are unchanged.
Apply restored startup settings during a separately coordinated quiet restart.
Never re-enable old collectors for a trial fetch.

Activation and observed verification evidence follow below.

## Activation and observed results - September 19, 2026

The reviewed script SHA-256 is
`f6de45458d68e6850ffd5950908800abc6c836311edb204b1c7422c9827355cf`.
A fresh configured verifier reviewed the stable source and preactivation evidence.
Its two action-guard/rollback findings were corrected before activation; the final
preactivation review passed.

At 15:39 UTC, all eight original task definitions were exported to
`C:\Users\gaina\AppData\Local\QuantDataInfra\wsl-host-repair-20260919T153908Z`
before disabling them. The new host task is enabled, Interactive/Limited, with
14 triggers and one hidden host-only action. All original task definitions remain.
The new .wslconfig and cloud-init disabled flag were applied with no existing file
overwritten. The cloud flag's exact bytes are retained for guarded rollback.

No fetch or model workers were running before shutdown; next fetch was 12:10 EDT.
Ubuntu completed graceful service power-off at 11:40:37 EDT, but the WSL instance
remained registered and was still reported as running. After confirming its shutdown
journal, the VM was unloaded at 11:42:12 EDT. The new host task was started at
11:42:14 EDT and returned 0. No collector service was manually started.
Only Ubuntu had been running; the Docker distributions were stopped.

Fresh Ubuntu startup completed in **3.296 seconds**, compared with **124.242 seconds**
before the repair. All four cloud-init stages and the old network wait remained
inactive under the opt-out condition. All **13 installed timers** were enabled,
active/waiting, and byte-identical to the preflight hashes. No new fetch-run receipt
was created during activation or restart.

A Windows-side idle observation from 11:44:45 to 11:46:16 EDT lasted **90.181 seconds**
with zero wsl.exe clients initially and no intervening Linux command from either
the primary or reviewer. Ubuntu remained running, the host task had not rerun, and
the subsequent boot ID matched the post-restart boot:
`4d4840b0-10bb-4ed7-ba98-b0a1e8f65964`.

Both the proposed and actual exported Windows schedules cover **11,929 fetch slots
over 366 days**, including both DST transitions. PowerShell syntax and native
Task Scheduler validate-only checks passed. Actual registration omitted the default
LeastPrivilege XML element; the strict first XML assertion caught that normalization,
then direct native readback verified Limited/Interactive and the normalized installed
definition passed coverage. No scheduler behavior was changed for that validation.

Private evidence is under `.local/wsl-host-repair-20260919/`: preflight.json,
native-validation.json, validate_wake_schedule.py, proposed-task.xml,
windows-postcheck.json, linux-postcheck.json, installed-task.xml,
installed-wake-coverage.json, idle-check.json, and the exact cloud flag preimage.
Windows originals and installation/restart/idle receipts remain in the backup folder.

This proves fast startup, successful host-task launch, restored timers, and observed
idle retention. Windows sleep/resume and a later unattended logon were not exercised.
No provider success or repair of earlier data gaps is claimed. The next host wake
was 12:08 EDT and next ordinary news fetch 12:10 EDT at inspection time.
