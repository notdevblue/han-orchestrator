---
name: han-outer-brief
description: Unregistered draft. Tier a project request and point at han-mission-brief. Default execution wakes a main or slot executor; Claude and Codex paste strings are optional.
---

# han outer brief

This file is not a registered Grok skill. The wake flow is `plugins/han/grok/skills/han-mission-brief/SKILL.md`.
The coordinator reads state, then wakes the executor for the main workspace or selected slot. Do not duplicate the mission state machine here.

## 1. Tier the request

- **S** — one or two files, a single clear fix. No mission. The woken bot does it as a direct task.
- **M** — several files or one layer with at least one verification gate. Mission on the woken bot, `init --tier M`. Optional Claude uses `/han:mission`, optional Codex uses `han-mission`.
- **L** — several layers, PoC, human checks A/B/C. Mission with `--tier L` on the woken bot.
- Repeat-until-green work goes to a loop, not a mission.

han enforces this too: `init --tier S` is refused without `--force-tier`; a missing tier only warns.

## 2. Pick who wakes (read-only)

Run the setup and `slots.py ... status` from `han-outer-status`. Prefer an idle `ready` slot with no ACTIVE;
otherwise the main workspace. Never create, refresh, or run slots from this draft.

| place | executor | path |
| --- | --- | --- |
| main, non-slot | main workspace executor | `<project>` |
| selected slot | slot executor | `<slot-path>` |

Slot bots stay idle until a mission is attached.

## 3. Write the brief

Default handoff is Grok-standalone. Build it with `han_state.py brief --runtime grok` and send the `SendToAgent` block to that bot.
The block carries the goal, project path, constraints, and a write confirmation. Local state is read through han scripts; repository code follows the project execution rules.

```
대상: 메인 또는 슬롯 실행 봇
프로젝트 경로: <project 또는 slot-path>
목표: <한 문장>
제약: submit 금지, 범위 밖 수정 금지
쓰기 확인: 파일을 쓰기 전에 범위와 경로를 확인한다
```

Claude or Codex paste strings are optional, and only when that runtime is requested.
Codex is the reduced path. Do not replace the default wake block with `/han:mission` or `$han-codex:han-mission`.

## 4. Monitor

Afterwards track progress only through `han-outer-status` (status/active/doctor). Report blockers and
questions waiting in the dashboard. Answers and approvals stay in han's confirm/ask files. The coordinator does not write them.
