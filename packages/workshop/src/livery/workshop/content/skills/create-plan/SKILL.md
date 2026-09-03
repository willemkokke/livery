---
name: create-plan
description: Write an implementation plan as a dated note in notes/, in the house format with ground-truth contracts and per-phase acceptance.
---
<!-- Shipped as livery.workshop layer content, delivered to the workspace
     by the sync verb. Edit this copy in the layer and release it;
     an edited delivered copy is a local override, kept and named.
-->

Plans live at `notes/YYYYMMDD-<slug>.md`. The exemplar is
`notes/20260830-forge-bootstrap.md`. Workflows come from
`notes/20260830-development-workflows.md`.

The format:

1. `# Title`, then a `Status:` line, updated as phases land.
2. **Ground-truth contracts (do not violate)**: the invariants no
   phase may break. A phase that would break one stops for the human.
3. **Phases**, each small enough to land in a day or two, gate-green
   and mergeable alone. Each phase states its deliverables as concrete
   files and behaviours, and an **Acceptance** list verifiable by
   command.
4. **Temporary, replaced by**: a table for anything scaffolded.
5. **Decision record**: dated lines, appended, never rewritten.
6. **Open**: numbered questions with owners.

Rules: current state only, churned history is consolidated away.
Quote the user's rulings by intent. Convert relative dates to
absolute. Every acceptance item names the command that proves it.
Write in the voice of `.claude/guidance/`.
