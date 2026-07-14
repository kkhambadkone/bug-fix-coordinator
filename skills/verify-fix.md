---
name: verify-fix
description: Re-runs the test suite after a patch to confirm whether the targeted failure (and no new failure) is resolved.
triggers: verify, confirm, re-test, regression-check
---

# Verify Fix

## When to use
After apply-patch. Reuses the same test-running mechanism as
reproduce-bug, but is listed as a separate skill because the coordinator's
*intent* differs (confirming a hypothesis vs. establishing a baseline) —
this distinction matters for routing and for the scratchpad narrative
("did iteration N's patch work?").

## Steps
1. Run `pytest -q` again.
2. Compare against the previous failing_tests list.
3. Report resolved tests, still-failing tests, and any regressions.

## Output contract
Return `{"passed": int, "failed": int, "raw_output": str,
"failing_tests": [str], "resolved": [str]}`
