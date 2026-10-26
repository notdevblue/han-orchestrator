## Answering

**EXPLAIN IN PLACE, NOT BY LABEL** — in chat, never refer to items only by labels (D5, I3); say what they mean right there. When asking the user to choose, give the background, options, and their difference in the same answer.

## Working Style

**RUN IT YOURSELF** — run what the user would otherwise run by hand (build, local server, sync/resolve, reading CI or tool config). Submit, push, deletes, and guard bypasses still need an in-the-moment ask; a past one-off approval doesn't carry over.
**SPLIT OUT-OF-SCOPE FIXES** — a fix found outside the unit in progress goes to its own CL or commit and its own agent.
**REPORT LONG WAITS** — an exception to not narrating: past ~10 minutes of waiting, post one line on what runs and what you expect; if nothing moves, check it yourself.
**RESTATE A DRIFTING PLAN** — after the direction changed more than once, lay out the agreed design in chat before going on; put decisions in chat before the `ExitPlanMode` box, which hides it.
**CLEAN-SLATE REQUESTS** — only when told to ignore or tear down the current design, show the user's direction and your own clean-slate best side by side; otherwise move-first applies.
**NAME THE LAYER BEFORE EDITING RULES** — say which layer a rule goes in (global, project shared, personal local, one skill) and why; prefer the narrowest and tighten instead of growing.
**AFTER A REVIEW** — fix clear defects and bring only judgment calls to the user, unless they asked to see the findings first.

## Plan Output

Applies ONLY to the `ExitPlanMode` plan, not internal execution plans.

**REORDER, NEVER REMOVE** — make it readable by order and layering, not by cutting; paths, targets, and edge cases go below the summary.
**LEAD WITH THE CONCLUSION** — open with 3-5 lines on what changes and why, then 1-2 lines on the judgment calls and which way they went.
**KEEP IT SHALLOW** — for a narrow terminal box: bullets at most 2 levels, headings at most 1, no tables or code blocks.
**GROUP STEPS BY INTENT, NOT BY FILE** — each step is one unit of meaning ("replace X with Y") naming the files it touches.

## Subagent Dispatch

**ROUTE BY SIZE** — multi-milestone work gated by verification or human checks (several files or layers, PoC, approvals) goes to `/han:mission`; a one- or two-file fix or a single clear task is dispatched or done directly; repeat-until-green goes to a loop, not a mission.
**PICK THE AGENT BY JOB** — research (code, docs, web): `han:researcher`, not `Explore` or `general-purpose`. With the direction fixed, approved documents and small specified edits: `han:dev-small`; multi-layer or complex work: `han:dev-medium` (outside missions, tiny edits may stay in the main session). Approved tests and Unity Play: `han:verifier`. Review, planning, design, and root-cause diagnosis stay with `code-reviewer`, `planner`, or the main session. Models live in each agent's definition.
**PARALLELIZE DISJOINT UNITS** — one `han:dev-medium` per disjoint-file unit, launched in one message; units sharing a file or needing another's output run in sequence. Briefs leave Unity compile, EditMode, and Play to the verifier; reuse a valid result for an unchanged state. After related units return, the main session runs one verifier and judges completion.
**STOP ONLY WORK THAT STILL NEEDS CANCELLATION** — a final report alone is no reason for `TaskStop`; call it only for running work to cancel or confirmed leftover background work. If it returns `not running`, record the terminal status without retrying and keep a reported `failed` as failed. Treat `No task found` as finished only with a completion record, otherwise recheck the task ID and session; no other error counts as success. Don't send cleanup or farewell `SendMessage`s; they can resume the task.
