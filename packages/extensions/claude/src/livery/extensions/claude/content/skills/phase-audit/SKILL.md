---
name: phase-audit
description: Audit a completed plan phase - re-run acceptance, check contracts intact, record the audit with evidence in the plan.
---
<!-- Shipped as livery.extensions.claude extension content, delivered to the workspace
     by the sync verb. Edit this copy in the extension and release it;
     an edited delivered copy is a local override, kept and named.
-->

After a phase is declared done:

1. Re-run every acceptance item's command fresh, not from memory of
   an earlier run. Collect the outputs.
2. Check each ground-truth contract still holds. Run the layering
   check: `fm layering.graph`.
3. Sweep for undeclared temporaries: anything scaffolded that is
   missing from the plan's temporary table gets added to it.
4. Append a dated audit block to the plan's decision record: items
   verified, evidence, drift found.
5. Drift is reported, never silently fixed. The fix is its own change
   with its own gate run.
