---
name: propose-fix
description: Given a failing function's source and the expected-vs-actual evidence, proposes a concrete code patch to correct the behavior.
triggers: propose, patch, hypothesis, fix-candidate
---

# Propose Fix

## When to use
After search-codebase returns the offending function's source. This is the
reasoning-heavy step: the coordinator hands the function source, the
failing assertion, and prior attempts on this function (read back from the
scratchpad/history, not the token-budget context) to a model and asks for
a patch.

Two backends are wired up in coordinator.py, selected with `--llm`:

- `table` (default) — a small deterministic rule table. Fully offline,
  reproducible, no dependencies. Good for demoing the architecture without
  external services.
- `ollama` — a real call to a local Llama 3 model via
  [Ollama](https://ollama.com) (`ollama pull llama3`, `ollama serve`, then
  `python3 coordinator.py --llm ollama`). The model only ever sees one
  function's source plus the specific failing assertion — never the whole
  file — and its response is validated (`compile()`, function-name check)
  before being handed to apply-patch, with one retry if it returns
  unparsable code.

## Steps
1. Read the function source and the expected/actual mismatch.
2. Generate a corrected version of the function.
3. Record the hypothesis in the scratchpad BEFORE applying it, so if this
   attempt fails, the next iteration's context (read from disk, not
   carried in-window) knows not to repeat it.

## Output contract
Return `{"old_source": str, "new_source": str}`
