---
name: han-outer-status
description: Unregistered draft. Read han mission status, slots, and adapter health in the current workspace. Does not open missions or wake bots; execution is a later han-mission-brief step.
---

# han outer status (read-only)

This file is not a registered Grok skill. Registered reads are `han-status` and `han-doctor`.
Mission state stays in `<stateDir>`. This draft only reads it. Waking a main or slot executor is a separate step in `han-mission-brief`.

## Setup (PowerShell, one Shell call)

```powershell
[Console]::OutputEncoding=[Text.Encoding]::UTF8; $env:PYTHONUTF8='1'
$env:HAN_ADAPTERS_DIR='<repo>/adapters'
$han=(Get-Content "$env:USERPROFILE\.claude\plugins\installed_plugins.json" -Raw | ConvertFrom-Json).plugins.'han@han-orchestrator'[0].installPath
```

Prepend this to every command below. Use the installed copy (`$han`), not the repo working tree.

## Commands (all read-only)

- Health: `python "$han/skills/doctor/scripts/doctor.py"` (`--json` for structured). Lines are `OK|WARN|FAIL <area>: <text>`.
- Active mission of a workspace: `python "$han/skills/mission/scripts/mission_state.py" --project <folder> active`
- Mission summary: `python "$han/skills/mission/scripts/mission_state.py" --project <folder> --mission <id> status`
  (also `context`, `transcripts`, `document list`)
- Slots: `python "$han/skills/slots/scripts/slots.py" --project <project> status`
- Guard regression: `python "$han/skills/adapter-init/scripts/check_guard.py" <adapter>`

Use the main project folder `<project>` or a registered slot folder `<slot-path>`.

## Reporting

- Summarize FAIL/WARN first, grouped by area, with the next step. Times in KST.
- Stale (`끊김 의심`) ACTIVE: show the mission id, last activity, and the `close-stale` preview command doctor printed.
  Propose closing; do not run `close-stale`, and never `--force-close-stale`. Closing happens only after the user names
  the mission and approves, in the woken bot or an optional Claude/Codex session.
- Version WARN: installed plugin older than repo; suggest the repo's update procedure (AGENTS.md), do not run it unless asked.

## Never

- Write ACTIVE, `mission.md`, `*.tsv`, `allow-once`; run `echo han-mission ...` markers; `init`/`unit`/`verify`/`complete`.
- `slots.py create|refresh|run|remove`, p4 write commands.
- Keep a Grok-side copy of mission state or a separate state machine.
