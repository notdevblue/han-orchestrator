## Answering

**EXPLAIN IN PLACE, NOT BY LABEL** — in chat, never refer to items only by labels (D5, I3); say what they mean right there. When asking the user to choose, give the background, options, and their difference in the same answer.

## Working Style

**RUN IT YOURSELF** — run what the user would otherwise run by hand (build, local server, sync/resolve, reading CI or tool config). Submit, push, deletes, and guard bypasses still need an in-the-moment ask; a past one-off approval doesn't carry over.
**SPLIT OUT-OF-SCOPE FIXES** — a fix found outside the unit in progress goes to its own CL or commit and its own agent.
**REPORT LONG WAITS** — an exception to not narrating: past ~10 minutes of waiting, post one line on what runs and what you expect; if nothing moves, check it yourself.
**RESTATE A DRIFTING PLAN** — after the direction changed more than once, lay out the agreed design in chat before going on, and before asking for plan approval.
**CLEAN-SLATE REQUESTS** — only when told to ignore or tear down the current design, show the user's direction and your own clean-slate best side by side; otherwise move-first applies.
**NAME THE LAYER BEFORE EDITING RULES** — say which layer a rule goes in (global, project shared, personal local, one skill) and why; prefer the narrowest and tighten instead of growing.
**AFTER A REVIEW** — fix clear defects and bring only judgment calls to the user, unless they asked to see the findings first.

## Plan Output

Applies ONLY to an implementation plan submitted for approval, not internal execution plans.

**REORDER, NEVER REMOVE** — make it readable by order and layering, not by cutting; paths, targets, and edge cases go below the summary.
**LEAD WITH THE CONCLUSION** — open with 3-5 lines on what changes and why, then 1-2 lines on the judgment calls and which way they went.
**KEEP IT SHALLOW** — for a narrow terminal box: bullets at most 2 levels, headings at most 1, no tables or code blocks.
**GROUP STEPS BY INTENT, NOT BY FILE** — each step is one unit of meaning ("replace X with Y") naming the files it touches.

## Subagent Dispatch

**ROUTE BY SIZE** — multi-milestone work gated by verification or human checks (several files or layers, PoC, approvals) goes to the `han-mission` skill; a one- or two-file fix or a single clear task is dispatched or done directly; repeat-until-green goes to a loop, not a mission.
**DELEGATE BY ROLE** — research `han_researcher`, small implementation `han_developer_small`, medium `han_developer_medium`, verification and Unity Play `han_verifier`. Prefer registered agents; otherwise give a fresh agent (`fork_turns: "none"`) its file — `codex/agents/han-researcher.toml`, `han-developer-small.toml`, `han-developer-medium.toml`, or `han-verifier.toml` — with that file's `model` and `model_reasoning_effort`, nested medium agents included. Research and review inherit the current model unless the user or project sets another.
**NO SILENT SUBSTITUTES** — never swap a role or switch models automatically; return failures, missing evidence, and unexpected states to the main session.
**SEPARATE AND SPLIT** — keep implementation, verification, and independent review separate; split implementation only into disjoint units. The main session schedules shared Unity use, runs one verifier after related units return, and judges completion.
