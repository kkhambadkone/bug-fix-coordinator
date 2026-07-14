# Bug-Fix Coordinator Agent

A small, runnable reference implementation of a **coordinator agent** that
resolves a multi-bug failure across several iterations — routing to
**skills defined as individual `.md` files**, with explicit, two-tier
**context management** so verbose tool output never bloats the
coordinator's working context, and a real, swappable **LLM backend**
(Llama 3 or any other model via Ollama) for the one step that actually
needs judgment.

<img src="architecture_diagram.png" width="800">

No API keys required for the default mode. It runs entirely locally
against a tiny seeded-bug Python project and pytest, with an optional
mode that calls a real local model.

## What it demonstrates

- **Skills as individual files** — each skill is a self-contained
  `skills/<name>.md` file with frontmatter (`name`, `description`,
  `triggers`) plus instructions. Metadata for every skill is loaded
  eagerly; the full instruction body is only loaded into context the
  moment a skill is actually selected.
- **Routing** — the coordinator matches a "need" (e.g. *diagnose a
  failure*) against each skill's trigger keywords and picks the best
  match, standing in for embedding- or LLM-based skill routing.
- **An iteration loop** — reproduce → diagnose → locate → propose →
  patch → verify, repeating for as many bugs as it takes.
- **Two-tier context management** — a small in-memory `working_context`
  of distilled, one-line summaries (what a real LLM prompt window would
  see), backed by a durable on-disk `scratchpad.md`, with raw/verbose
  output (full pytest logs) archived to separate log files and never
  loaded into context at all.
- **Context compaction** — once `working_context` passes a threshold,
  older entries fold into a rolling summary string instead of being kept
  verbatim or dropped.
- **A pluggable reasoning backend** — `propose_fix()` is either a
  deterministic lookup table (`--llm table`, default) or a real call to
  a local model via Ollama (`--llm ollama`), with the model's response
  validated (`compile()`, function-signature check) and retried before
  it's ever trusted.
- **Fair scheduling across competing failures** — target selection is
  round-robin by attempt count, not "always the alphabetically-first
  failing test," so one stubborn bug can't silently consume the entire
  iteration budget while others go untried.
- **Resilience to a bad model response** — a single unparsable or
  unusable answer for one function is logged and skipped, not fatal to
  the whole run.

## Quick start

```bash
git clone <this-repo-url>
cd bug-fix-agent-demo
pip install pytest --break-system-packages   # omit the flag on most local setups
python3 coordinator.py
```

The demo seeds four independent bugs into `sample_project/shopping_cart.py`.
The coordinator autonomously reproduces the failures, diagnoses each one,
patches the source file on disk, and re-verifies — normally resolving
all four bugs in 4 iterations with the default `table` backend (each
iteration targets one failing test, and each of these bugs lives in its
own function).

### Example output

```
=== Skill catalog (name + description only — lazy-loaded bodies) ===
 - analyze-failure: Parses a pytest traceback to identify the failing test...
 - apply-patch: Writes a proposed code change to disk...
 - propose-fix: Given a failing function's source and the expected-vs-actual evidence...
 - reproduce-bug: Runs the project's test suite to establish current pass/fail state...
 - search-codebase: Locates and returns the relevant source snippet...
 - verify-fix: Re-runs the test suite after a patch...

--- Iteration 0: establish baseline ---
[coordinator] need='reproduce baseline' -> routed to skill 'reproduce-bug'
  -> 0 passed, 5 failed: ['test_apply_discount', 'test_bulk_discount', 'test_calculate_tax', 'test_calculate_total', 'test_format_receipt_total']

--- Iteration 1 ---
...
  -> 2 passed, 3 failed. Resolved this round: ['test_apply_discount', 'test_bulk_discount']

--- Iteration 2 ---
...
  -> 3 passed, 2 failed. Resolved this round: ['test_calculate_tax']

--- Iteration 3 ---
...
  -> 4 passed, 1 failed. Resolved this round: ['test_calculate_total']

--- Iteration 4 ---
...
  -> 5 passed, 0 failed. Resolved this round: ['test_format_receipt_total']

=== Final state ===
All tests passing after 4 iteration(s).
```

(Exact resolution order can vary — it always targets whichever failing
function has had the fewest attempts so far, so re-ordering the test file
or bugs changes the sequence, not the outcome. With `--llm ollama`, the
total iteration count is inherently non-deterministic too, since a real
model doesn't always get it right on the first try.)

Every step is also written to `run_output/scratchpad.md` (durable
narrative) and `run_output/logs/` (raw pytest output per step).

## Using a real LLM (Llama 3, or any other Ollama model)

By default `propose_fix()` uses a small deterministic rule table so the
demo runs offline and reproducibly. To have the coordinator actually
reason about the fix with a real model instead of a lookup table:

```bash
# once
ollama pull llama3

# make sure the server is running (usually auto-starts after install)
ollama serve

# run the coordinator against it
python3 coordinator.py --llm ollama
```

Any Ollama model works — swap it with `--model`, e.g. a coding-tuned
model tends to do noticeably better on precise, arithmetic-style fixes
than a general chat model:

```bash
ollama pull qwen2.5-coder:7b
python3 coordinator.py --llm ollama --model qwen2.5-coder:7b
```

Optional flags:

- `--model <name>` — use a different Ollama model (default `llama3`)
- `--ollama-host <url>` — point at a non-default Ollama server (default
  `http://localhost:11434`)
- `--llm-timeout <seconds>` — how long to wait for a response (default
  `600`). The *first* call after starting `ollama serve` is often slow
  because the model has to load into memory before it can generate
  anything; on CPU-only machines generation itself can also take a while.
  Ollama also unloads an idle model after a few minutes by default, so a
  later call in the same run can pay that load cost again — set
  `OLLAMA_KEEP_ALIVE=30m` (or `-1` to never unload) when starting
  `ollama serve` to avoid that. If you still hit a timeout error, either
  raise `--llm-timeout` further or warm the model up first in another
  terminal with `ollama run llama3 "hi"`.

With `--llm ollama`, the model only ever sees one function's exact source
(fetched via the AST, see below) plus pytest's evaluated failure detail
for that function — never the whole file, and never a raw traceback. Its
response is validated (`compile()` and a function-name check) before
being applied, with a retry — and, after two failed attempts on the same
function, an escalating prompt that asks it to state the actual-vs-
expected relationship in one sentence before writing code, rather than
just repeating the same instructions and hoping.

Because a real model's first guess isn't guaranteed to be right, run
count and behavior become non-deterministic in this mode — that's
expected, and part of what distinguishes it from the `table` backend.

## Requirements

- Python 3.8+
- `pytest` (installed automatically if missing — see Quick start)

No other dependencies.

## Project structure

```
bug-fix-agent-demo/
├── coordinator.py               # main iteration loop: reproduce → diagnose → locate → propose → apply → verify
├── skill_registry.py            # loads skills/*.md, lazy-loads bodies, routes by trigger keywords
├── context_manager.py           # two-tier context: working_context + compaction + durable scratchpad
├── skills/
│   ├── reproduce-bug.md          # run tests, establish baseline
│   ├── analyze-failure.md        # traceback -> responsible function
│   ├── search-codebase.md        # fetch just the offending function, not the whole file
│   ├── propose-fix.md            # generate a candidate patch (table or real LLM backend)
│   ├── apply-patch.md            # write the patch to disk
│   └── verify-fix.md             # re-run tests, check what got resolved
├── sample_project/
│   ├── shopping_cart.py            # tiny module with four seeded, independent bugs
│   └── test_shopping_cart.py       # pytest suite that exposes all four bugs
├── _offline_llm_simulation.py    # offline harness: scripts a fake model's messy responses
│                                  # to verify the resilience logic without a live Ollama server
└── run_output/                   # generated at runtime: scratchpad.md + logs/ (gitignored)
```

## How the pieces fit together

**Skills as individual files.** Each skill is a self-contained `.md` file
with YAML-ish frontmatter (`name`, `description`, `triggers`) followed by
instructions and an output contract. `SkillRegistry` parses the frontmatter
for every file at startup — so the coordinator always knows what skills
exist and what each is for — but does **not** read the body (the actual
instructions) until that skill is selected. Watch the `[skills]` log lines:
each one only appears the moment that skill is actually used. This is the
same lazy-loading principle used for any large tool/skill library: keep
the default context cheap, expand only what's needed, when it's needed.

**Routing.** `SkillRegistry.select()` scores each skill's `triggers`
against the coordinator's current "need" (e.g. `{"verify", "confirm",
"re-test"}` after a patch is applied) and picks the best match. This is a
simplified stand-in for what a real system would do with embedding
similarity or an LLM call over skill descriptions — the mechanism (match a
need against a library of described capabilities) is the same.

**The iteration loop, and fair scheduling within it.** Each iteration is
the cycle you'd use for debugging by hand: reproduce → diagnose → locate
→ hypothesize → patch → verify, up to `MAX_ITERATIONS` (24 — a shared
budget across *every* failing test, not per-bug, giving a real model room
to retry more than one stubborn function). Which failing test gets
targeted each round is **not** "whichever one is alphabetically first" —
it's whichever failing function has had the *fewest attempts so far*.
That distinction matters: with a naive `failing_tests[0]` pick, once the
first bug is fixed, the next-alphabetically-first bug becomes `[0]` and
**stays** `[0]` for as long as it keeps failing, silently absorbing the
entire remaining iteration budget while other, possibly easy, bugs never
get a single attempt. Round-robin by attempt count fixes that: every
function gets a turn before any function gets a second one.

**Context management.** `ContextManager` keeps two tiers:

1. `working_context` — a small, in-memory list of short, distilled entries
   (one line per skill invocation: what was tried, what happened). This is
   the stand-in for a real coordinator's prompt window.
2. `run_output/scratchpad.md` — a durable, append-only file with the full
   narrative of the run. It survives independent of the context window
   and is what a resumed or restarted coordinator would re-read instead of
   depending on conversation history.

Raw, verbose tool output (full pytest logs) is **never** put in
`working_context` — it's archived to `run_output/logs/iterN_skill.log`
instead. Only a one-line summary crosses into context. Once
`working_context` exceeds `MAX_RAW_ENTRIES` (4), the oldest entries fold
into `compacted_summary`, a single rolling string — watch for the
`[context] compacted N older entries` lines. `get_context_for_reasoning()`
shows exactly what would be handed to a routing/reasoning step at any
point in the run.

**Extracting one function's exact source.** `search_codebase()` uses
`ast.get_source_segment()`, not a regex. A regex like `(?:    .*\n?)+`
(matching only lines that start with 4+ spaces of indentation) looks
reasonable but silently breaks on something as ordinary as a blank line
between a guard clause and a return statement — it truncates the captured
function mid-body, handing the model an incomplete function to "fix." The
AST-based version is correct regardless of blank lines, comments,
decorators, or formatting style, because it works off Python's actual
parse tree instead of guessing from indentation.

**Giving the model the right evidence, not just correct-looking
evidence.** `extract_assertion_evidence()` pulls pytest's *evaluated*
failure lines (`E    assert 15 == 35`), not the assert statement's source
line (`assert calculate_total(items) == 35`). The source line only shows
what the *expected* value is; the evaluated lines are the only place the
function's actual (wrong) output appears. A model reading only the source
line has to guess what's currently broken instead of being told.

**Feeding real history back, not just a pass/fail note.** `fix_history`
records the actual code from every failed attempt on a function, plus
what it still got wrong — not just "attempt 3: failed." A model told only
that it failed before has no way to know it already tried a specific
wrong approach (like guessing a dict key that doesn't exist) and will
often just repeat it.

**One bad step doesn't take down the whole run.** The propose → apply
sequence is wrapped in error handling: if the model returns unparsable
text twice in a row, or a patch can't be applied cleanly, that attempt is
logged as failed and the loop moves on to the next iteration — it does
**not** propagate out and abort the entire coordinator, which would
otherwise abandon bugs that hadn't even been attempted yet. `apply_patch`
also re-compiles the *whole file* after every patch (not just the
isolated new function) and rolls back with a clear error if that fails,
catching the case where a patch only covered part of the real function.

**Escalating when a function is stubborn.** After 2 failed attempts on
the same function, the prompt adds an explicit instruction to state the
actual-vs-expected relationship in one sentence before writing code —
pushing toward slower, more literal reasoning instead of just repeating
the same ask.

**Verifying all of this without a live model.** `_offline_llm_simulation.py`
monkeypatches `call_ollama_chat` with a scripted fake that reproduces real
failure patterns we hit along the way: a syntactically-valid-but-wrong
guess (a hallucinated dict key), a response with no code at all (twice, to
exhaust the retry budget and exercise the non-fatal error handling), and a
function that only gets fixed after the escalating hint kicks in. It
confirms the full pipeline resolves all four bugs with zero unhandled
exceptions — using the exact same `propose_fix_llm`/`apply_patch`/
scheduling code that a real Ollama run uses, just with the network call
swapped for a script.

## Extending this

- Add a new skill by dropping another `<name>.md` file into `skills/`
  with its own `triggers` — no registry code changes needed.
- Point `call_ollama_chat()` at a different provider (Groq, Together AI,
  a self-hosted vLLM server, etc.) by changing the request/response
  shape — the rest of `propose_fix_llm()`'s validation, retry, and
  escalation logic doesn't need to change.
- Point `PROJECT_DIR` at a real codebase and swap `TEST_TO_FUNCTION` for
  actual traceback parsing (`analyze_failure()`) to generalize routing
  beyond the seeded sample bugs.
- Extend `_offline_llm_simulation.py`'s `RESPONSES` dict with new scripted
  failure patterns to stress-test the pipeline further before spending
  real time (or GPU/CPU cycles) on a live model.

## License

Not yet licensed. Add a `LICENSE` file (MIT is a common default for
demo/reference code like this) before accepting external contributions
or reuse.
