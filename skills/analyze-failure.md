---
name: analyze-failure
description: Parses a pytest traceback to identify the failing test and the source function responsible for it.
triggers: analyze, traceback, root-cause, diagnose
---

# Analyze Failure

## When to use
After reproduce-bug reports at least one failure. Turns a wall of
traceback text into a specific, actionable root-cause pointer.

## Steps
1. Take the raw_output and failing_tests list from reproduce-bug.
2. Map the failing test to the source function under test.
3. Return a compact structured pointer — this is what enters the
   coordinator's context, NOT the raw traceback text.

## Output contract
Return `{"test": str, "function": str}`
