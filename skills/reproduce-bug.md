---
name: reproduce-bug
description: Runs the project's test suite to establish current pass/fail state and capture concrete failure evidence (tracebacks, assertion diffs).
triggers: reproduce, run-tests, baseline, initial-state
---

# Reproduce Bug

## When to use
Use this first, before any hypothesis is formed, to establish a factual
baseline of what is currently broken.

## Steps
1. Run the test suite with `pytest -q`.
2. Capture stdout/stderr in full (do not summarize here — summarization is
   the coordinator's job, not this skill's).
3. Return pass count, fail count, and which tests failed.

## Output contract
Return a dict: `{"passed": int, "failed": int, "raw_output": str,
"failing_tests": [str]}`
