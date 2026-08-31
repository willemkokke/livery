---
name: execute-plan
description: Execute one phase of a plan note - worktree, gate-green, plan updated in the same change, acceptance proven by command.
---

1. Read the plan's ground-truth contracts and the target phase fully.
   Read the development workflows note for any forge-touching work.
2. Work in a worktree under `.claude/worktrees/`. Agent sessions
   always do.
3. One phase at a time. If a contract and the phase conflict, stop
   and surface it. Never resolve the conflict silently.
4. `uv run fm check` green before calling anything done. Run each
   acceptance item's command and keep the output as evidence.
5. Update the plan in the same change: the Status line, checkboxes if
   present, and a dated decision-record line for any deviation.
6. Report what landed, the acceptance evidence, and what is next.
   Never commit or push unless asked. Never add attribution trailers.
