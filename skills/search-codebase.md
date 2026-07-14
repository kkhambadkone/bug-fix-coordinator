---
name: search-codebase
description: Locates and returns the relevant source snippet for a given function name, without dumping the entire file into context.
triggers: search, locate, find-code, grep
---

# Search Codebase

## When to use
After analyze-failure identifies a responsible function. Fetch just that
function's body rather than reading the whole file — this is the
"delegate exploration, return a distilled result" pattern: a subagent (or
this skill) does the noisy grepping, and only the relevant snippet crosses
back into the coordinator's context.

## Steps
1. Grep the project for `def <function_name>`.
2. Extract just that function's body.
3. Return the snippet plus its file location.

## Output contract
Return `{"function": str, "file": str, "source": str}`
