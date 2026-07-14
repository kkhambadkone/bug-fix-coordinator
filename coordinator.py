"""
Coordinator agent that resolves a multi-bug failure across several
iterations, routing to skills defined in individual skills/*.md files and
managing context via context_manager.ContextManager.

Run it with the deterministic (offline, reproducible) fix table:
    python3 coordinator.py

Run it with a real Llama 3 model via Ollama for the propose-fix step:
    ollama pull llama3          # once
    ollama serve                # if not already running
    python3 coordinator.py --llm ollama

What to watch for in the output:
  [skills]      — a skill's full body is lazily loaded only when selected
  [coordinator] — the routing decision: which "need" mapped to which skill
  [context]     — compaction events and the current reasoning context
  [llm]         — prompt/response activity when --llm ollama is used
  scratchpad.md — durable memory of the whole run (run_output/scratchpad.md)
  logs/         — raw pytest output per step, kept OUT of context (run_output/logs/)
"""

import argparse
import ast
import json
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request
from collections import defaultdict

from skill_registry import SkillRegistry
from context_manager import ContextManager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, "sample_project")
SKILLS_DIR = os.path.join(BASE_DIR, "skills")
RUN_DIR = os.path.join(BASE_DIR, "run_output")
MAX_ITERATIONS = 24  # this is a SHARED budget across every failing test, not
                      # per-bug — with 4 independent seeded bugs, a real
                      # (imperfect) model needs room to retry more than one of
                      # them a few times each, not just 2 clean passes total
                      # (which is all the deterministic 'table' backend ever
                      # needs, one per bug)
MAX_LLM_ATTEMPTS = 2  # retries if the model returns unparsable/invalid Python

# Maps each test to the source function it exercises. A real system would
# get this mapping from analyze-failure parsing the traceback; here it's
# hardcoded for a reliable, dependency-free demo.
TEST_TO_FUNCTION = {
    "test_apply_discount": "apply_discount",
    "test_calculate_total": "calculate_total",
    "test_bulk_discount": "apply_bulk_discount",
    "test_calculate_tax": "calculate_tax",
    "test_format_receipt_total": "format_receipt_total",
}


# --------------------------------------------------------------------------
# Skill implementations (what actually runs when a skill is "invoked")
# --------------------------------------------------------------------------

def run_tests():
    result = subprocess.run(
        ["python3", "-m", "pytest", "-q", "--no-header"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )
    raw = result.stdout + result.stderr
    failing_tests = sorted(set(re.findall(r"FAILED \S+::(\w+)", raw)))
    passed_m = re.search(r"(\d+) passed", raw)
    failed_m = re.search(r"(\d+) failed", raw)
    passed = int(passed_m.group(1)) if passed_m else 0
    failed = int(failed_m.group(1)) if failed_m else 0
    return {"passed": passed, "failed": failed, "raw_output": raw, "failing_tests": failing_tests}


def analyze_failure(failing_test):
    function_name = TEST_TO_FUNCTION.get(failing_test)
    return {"test": failing_test, "function": function_name}


def search_codebase(function_name):
    """Fetch just one function's exact source text via the AST, not a
    hand-rolled regex. A regex like `(?:    .*\\n?)+` requires EVERY body
    line to start with 4+ spaces — a totally normal blank line between a
    guard clause and a return statement breaks that, silently truncating
    the captured function mid-body. That's a real, reproducible failure
    mode (not a model-capability issue): the model gets handed an
    incomplete function as 'current source', and apply-patch then only
    replaces that incomplete prefix, leaving stale code behind it. Using
    ast.get_source_segment() instead is robust to blank lines, comments,
    decorators, and multi-line signatures, because it works off Python's
    actual parse tree rather than guessing from indentation patterns."""
    file_path = os.path.join(PROJECT_DIR, "shopping_cart.py")
    src = open(file_path).read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            segment = ast.get_source_segment(src, node)
            return {"function": function_name, "file": file_path, "source": segment}
    return {"function": function_name, "file": file_path, "source": None}


def extract_assertion_evidence(raw_output, test_name):
    """Pull pytest's EVALUATED failure detail for this test — the 'E ...'
    lines — not just the source line of the assert statement.

    pytest's failure blocks look like:
        >       assert calculate_total(items) == 35
        E       AssertionError: assert 15 == 35
        E        +  where 15 = calculate_total([...])

    The '>' line is just the assert *statement* (it names the expected
    value, 35, but never shows what the code actually produced). The 'E'
    lines are pytest's evaluated output and are the only place the actual
    wrong value (15) appears. A model — or a person — reading only the '>'
    line has to guess what's currently wrong instead of being told; earlier
    versions of this function grabbed the first 'assert...' text after the
    test name, which matched the unhelpful '>' line, not the 'E' lines."""
    idx = raw_output.find(test_name)
    if idx == -1:
        return "no assertion detail found"

    e_lines = []
    started = False
    for line in raw_output[idx:].splitlines():
        stripped = line.strip()
        if stripped.startswith("E "):
            e_lines.append(stripped[2:].strip())
            started = True
        elif started:
            break  # left the contiguous block of E-lines
    if e_lines:
        return "\n".join(e_lines)

    # Fallback: no E-lines found (unexpected pytest output format) — grab
    # the raw assert source line rather than returning nothing.
    m = re.search(r"assert[^\n]+", raw_output[idx:])
    return m.group(0).strip() if m else "no assertion detail found"


# --------------------------------------------------------------------------
# propose-fix: deterministic table (default) or a real Llama 3 call via Ollama
# --------------------------------------------------------------------------

# Deterministic "reasoning" table — the default, offline, fully reproducible
# path. Keyed by function name, value is the corrected source.
KNOWN_FIXES = {
    "apply_discount": 'def apply_discount(price, discount_percent):\n    return price * (1 - discount_percent / 100)\n',
    "calculate_total": 'def calculate_total(items):\n    total = 0\n    for item in items:\n        total = total + item["price"] * item["qty"]\n    return total\n',
    "calculate_tax": 'def calculate_tax(subtotal, tax_rate_percent):\n    return subtotal * tax_rate_percent / 100\n',
    "format_receipt_total": 'def format_receipt_total(subtotal, tax, shipping):\n    return round(subtotal + tax + shipping, 2)\n',
}


def propose_fix_table(function_name, source):
    return {"old_source": source, "new_source": KNOWN_FIXES[function_name]}


def call_ollama_chat(system, user, model, host, timeout=600):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/chat", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data["message"]["content"]
    except (socket.timeout, TimeoutError):
        # NOTE: on Python < 3.10, socket.timeout is its own class, distinct
        # from TimeoutError and NOT a subclass of urllib.error.URLError, so
        # it must be caught explicitly and before the URLError branch below
        # (a slow/loading model can time out mid-read, after the connection
        # already succeeded, which raises this rather than URLError).
        raise RuntimeError(
            f"Ollama at {host} accepted the request but didn't respond within "
            f"{timeout}s. This is usually the model loading into memory on its "
            f"first call, or slow CPU-only generation. Try: (1) warm the model up "
            f"first with 'ollama run {model} \"hi\"' in another terminal so it's "
            f"already loaded, then re-run this script, or (2) pass a longer "
            f"--llm-timeout, e.g. --llm-timeout 600."
        )
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach Ollama at {host} ({e}). Is it running? "
            f"Start it with 'ollama serve' and make sure the model is pulled: "
            f"'ollama pull {model}'."
        )


def extract_code(text):
    """Pull a Python function definition out of a model response. Models
    don't reliably follow 'only output a code fence' instructions, so this
    tries several strategies in order rather than assuming a clean fence:

    1. A fenced ```python ... ``` (or bare ``` ... ```) block, with or
       without a newline right after the opening fence.
    2. If no fence is found (e.g. the model just wrote prose + code with no
       markdown), scan for the first 'def ...' line and take it plus every
       following line that's blank or indented — i.e. the function body —
       ignoring any explanation before or after it.
    3. As a last resort, return the raw text stripped, so compile() at the
       call site produces a clear error instead of this function silently
       swallowing something wrong.
    """
    for pattern in (r"```(?:python)?\s*\n(.*?)```", r"```(?:python)?(.*?)```"):
        m = re.search(pattern, text, re.DOTALL)
        if m and m.group(1).strip():
            return _with_trailing_newline(m.group(1))

    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip().startswith("def ")), None)
    if start is not None:
        block = [lines[start]]
        for line in lines[start + 1:]:
            if line.strip() == "" or line[:1] in (" ", "\t"):
                block.append(line)
            else:
                break
        return _with_trailing_newline("\n".join(block))

    return _with_trailing_newline(text)


def _with_trailing_newline(code):
    # .strip() (not just stripping newlines) matters here: models sometimes
    # put code on the same line as the opening fence, e.g. "```python def
    # foo():", which leaves a stray leading space that would otherwise turn
    # into a bogus "unexpected indent" at the top of the file.
    code = code.strip()
    return code + "\n" if code else code


def propose_fix_llm(function_name, source, evidence, history, model, host, timeout=600, prior_attempts=0):
    system = (
        "You are a careful Python bug-fixing assistant. You are given exactly "
        "one function's source code and evidence of a failing test. Make the "
        "smallest change that fixes the described failure — do not rewrite "
        "the function's approach, add unrelated validation, or change its "
        "signature. Use ONLY dictionary keys, variable names, and values that "
        "literally appear in the source or the evidence below; never guess, "
        "rename, or invent a key that isn't shown verbatim (e.g. if the "
        "evidence shows a dict with key 'qty', use 'qty' — do not assume a "
        "more common-sounding name like 'quantity'). Respond with ONE markdown "
        "code fence (```python ... ```) containing only the corrected "
        "function — same name and signature. Output nothing before the "
        "opening fence and nothing after the closing fence: no greeting, no "
        "explanation, no summary of what you changed."
    )
    history_block = history if history else "(none yet)"
    user = (
        f"Function to fix: {function_name}\n\n"
        f"Current source:\n```python\n{source}```\n\n"
        f"Failing test evidence (this shows the ACTUAL data the function was "
        f"called with — read any dict keys or values here literally, do not "
        f"substitute your own names for them):\n{evidence}\n\n"
        f"Prior attempts on this function in this run — do not repeat these, "
        f"they did not work:\n{history_block}\n\n"
        f"Reply with only the code fence, nothing else."
    )
    if prior_attempts >= 2:
        # Escalating hint: a plain "try again" hasn't worked twice already,
        # so push toward slower, more literal reasoning instead of just
        # repeating the same instructions a third time and hoping.
        user += (
            f"\n\nThis function has failed {prior_attempts} times already. Before "
            f"writing code: state, in one short sentence, the exact arithmetic or "
            f"logical relationship between the actual value and the expected value "
            f"shown in the evidence above. Then write the function so that "
            f"relationship holds — the smallest possible change, no added "
            f"validation, error handling, or rounding unless the evidence "
            f"requires it. Put that one-sentence reasoning as a Python comment "
            f"on the first line inside the function, then the code fence."
        )

    last_error = None
    for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
        print(f"  [llm] asking {model} to fix '{function_name}' (attempt {attempt}/{MAX_LLM_ATTEMPTS})")
        if last_error:
            user += (
                f"\n\nYour previous answer could not be used ({last_error}). "
                f"Reply with ONLY a ```python code fence containing the function — "
                f"no words before or after it."
            )
        raw = call_ollama_chat(system, user, model, host, timeout=timeout)
        new_source = extract_code(raw)
        try:
            compile(new_source, "<llm-patch>", "exec")
        except SyntaxError as e:
            last_error = f"invalid Python: {e}"
            print(f"  [llm] response failed to compile: {e}")
            print(f"  [llm] raw response was:\n{'-' * 40}\n{raw}\n{'-' * 40}")
            continue
        if f"def {function_name}" not in new_source:
            last_error = f"response did not define '{function_name}'"
            print(f"  [llm] {last_error}")
            print(f"  [llm] raw response was:\n{'-' * 40}\n{raw}\n{'-' * 40}")
            continue
        return {"old_source": source, "new_source": new_source}

    raise RuntimeError(
        f"Ollama model '{model}' did not return valid Python for '{function_name}' "
        f"after {MAX_LLM_ATTEMPTS} attempts. Last error: {last_error}"
    )


def apply_patch(old_source, new_source):
    file_path = os.path.join(PROJECT_DIR, "shopping_cart.py")
    original = open(file_path).read()
    if old_source not in original:
        raise ValueError("Patch target not found verbatim — refusing to guess")
    # old_source comes from ast.get_source_segment(), which never includes a
    # trailing newline. new_source (table lookup or an LLM response) always
    # does. Without normalizing, every patch inserts one extra blank line
    # that wasn't there before, since the file's original blank-line
    # spacing after the function is untouched and now gets a newline added
    # on top of it.
    patched = original.replace(old_source, new_source.rstrip("\n"))

    # Safety net: confirm the WHOLE file still compiles after the patch, not
    # just the isolated new_source snippet (that was already checked before
    # this point). This catches a class of bug where old_source only ever
    # covered part of the real function — e.g. from a fragile regex-based
    # extractor stopping at a blank line — so the replace leaves orphaned
    # code sitting after the new function body. Fail loudly and roll back
    # rather than writing a subtly-broken file and limping on.
    try:
        compile(patched, file_path, "exec")
    except SyntaxError as e:
        raise RuntimeError(
            f"Refusing to apply patch: the resulting file would not compile "
            f"({e}). This usually means old_source only captured part of the "
            f"real function. File left unchanged."
        )

    with open(file_path, "w") as f:
        f.write(patched)
    return {"file": file_path, "applied": True}


# --------------------------------------------------------------------------
# Coordinator loop
# --------------------------------------------------------------------------

def route(registry, need_tags, need_label):
    skill = registry.select(need_tags)
    print(f"[coordinator] need='{need_label}' -> routed to skill '{skill.name}' "
          f"(matched on {sorted(set(skill.triggers) & need_tags)})")
    registry.load_body(skill.name)
    return skill


def main():
    parser = argparse.ArgumentParser(description="Bug-fix coordinator agent demo.")
    parser.add_argument("--llm", choices=["table", "ollama"], default="table",
                         help="propose-fix backend: 'table' (deterministic, default) or 'ollama' (real Llama 3 call)")
    parser.add_argument("--model", default="llama3", help="Ollama model name (default: llama3)")
    parser.add_argument("--ollama-host", default="http://localhost:11434", help="Ollama server URL")
    parser.add_argument("--llm-timeout", type=int, default=600,
                         help="seconds to wait for an Ollama response (default: 600). "
                              "Increase this further if the model is still loading, running "
                              "on CPU, or was recently unloaded due to Ollama's idle timeout "
                              "(see OLLAMA_KEEP_ALIVE to keep it resident between calls).")
    args = parser.parse_args()

    registry = SkillRegistry(SKILLS_DIR)
    ctx = ContextManager(
        os.path.join(RUN_DIR, "scratchpad.md"),
        os.path.join(RUN_DIR, "logs"),
    )
    fix_history = defaultdict(list)  # function_name -> list of prior attempt notes
    attempts_per_function = defaultdict(int)  # function_name -> attempts spent on it so far

    print("=== Skill catalog (name + description only — lazy-loaded bodies) ===")
    for name, desc in registry.catalog().items():
        print(f" - {name}: {desc}")
    print(f"\n[coordinator] propose-fix backend: {args.llm}"
          + (f" (model={args.model}, host={args.ollama_host})" if args.llm == "ollama" else ""))

    iteration = 0

    print("\n--- Iteration 0: establish baseline ---")
    skill = route(registry, {"reproduce"}, "reproduce baseline")
    result = run_tests()
    ctx.record(
        iteration, skill.name,
        f"{result['passed']} passed, {result['failed']} failed. Failing: {result['failing_tests']}",
        full_output=result["raw_output"],
    )
    print(f"  -> {result['passed']} passed, {result['failed']} failed: {result['failing_tests']}")

    while result["failed"] > 0 and iteration < MAX_ITERATIONS:
        iteration += 1
        print(f"\n--- Iteration {iteration} ---")

        # Round-robin across failing tests by how many attempts their function
        # has already had, rather than always picking failing_tests[0]. With
        # a plain [0] pick, once the alphabetically-first bug is fixed, the
        # NEXT-alphabetically-first remaining bug becomes [0] and STAYS [0]
        # every iteration for as long as it keeps failing — so a single
        # stubborn function silently absorbs the entire remaining iteration
        # budget while other, possibly easy, bugs never get a single attempt.
        target_test = min(
            result["failing_tests"],
            key=lambda t: attempts_per_function[TEST_TO_FUNCTION.get(t, t)],
        )

        skill = route(registry, {"analyze", "root-cause"}, "diagnose failure")
        diag = analyze_failure(target_test)
        if diag["function"] is None:
            # Defensive guard: every test in this demo is in TEST_TO_FUNCTION,
            # so this shouldn't happen, but if the coordinator were pointed at
            # a real codebase with incomplete traceback parsing, silently
            # proceeding with function=None would cause a confusing failure
            # several steps later instead of a clear one right here.
            print(f"  [coordinator] no known function mapping for '{target_test}' — cannot proceed on it")
            ctx.record(iteration, skill.name, f"no function mapping for '{target_test}', skipped")
            attempts_per_function[target_test] += 1  # still counts, so round-robin moves on
            continue
        attempts_per_function[diag["function"]] += 1
        ctx.record(
            iteration, skill.name,
            f"{target_test} traced to function '{diag['function']}' "
            f"(attempt #{attempts_per_function[diag['function']]} on this function)",
        )

        skill = route(registry, {"search", "locate"}, "locate source")
        snippet = search_codebase(diag["function"])
        if snippet["source"] is None:
            print(f"  [coordinator] could not locate source for '{diag['function']}' — skipping this attempt")
            ctx.record(iteration, skill.name, f"source lookup failed for '{diag['function']}'")
            continue
        ctx.record(
            iteration, skill.name,
            f"fetched {len(snippet['source'] or '')} chars for '{diag['function']}' (not whole file)",
        )

        # The propose -> apply sequence is wrapped so that ONE bad step here
        # (the model returns unparsable text twice in a row, or a patch
        # can't be applied cleanly) counts as a failed attempt on this
        # function and moves on to the next iteration, instead of raising
        # all the way out to main() and aborting the ENTIRE run — including
        # bugs that hadn't even been attempted yet. A flaky response for one
        # function shouldn't be fatal to the other three.
        skill = route(registry, {"propose", "hypothesis"}, "propose fix")
        try:
            if args.llm == "ollama":
                evidence = extract_assertion_evidence(result["raw_output"], target_test)
                print(f"  [llm] evidence handed to the model:\n    {evidence.replace(chr(10), chr(10) + '    ')}")
                history = "\n".join(fix_history[diag["function"]])
                fix = propose_fix_llm(diag["function"], snippet["source"], evidence, history, args.model,
                                       args.ollama_host, timeout=args.llm_timeout,
                                       prior_attempts=len(fix_history[diag["function"]]))
                print(f"  [llm] proposed source for '{diag['function']}':\n"
                      f"  {'-' * 40}\n"
                      + "".join(f"  {line}\n" for line in fix["new_source"].splitlines())
                      + f"  {'-' * 40}")
            else:
                fix = propose_fix_table(diag["function"], snippet["source"])
            ctx.record(iteration, skill.name, f"hypothesis recorded: rewrite '{diag['function']}' (backend={args.llm})")

            skill = route(registry, {"apply", "write"}, "apply patch")
            applied = apply_patch(fix["old_source"], fix["new_source"])
            ctx.record(iteration, skill.name, f"patched '{diag['function']}' in {os.path.basename(applied['file'])}")
        except (RuntimeError, ValueError) as e:
            print(f"  [coordinator] this attempt on '{diag['function']}' failed before verification: {e}")
            fix_history[diag["function"]].append(
                f"attempt {iteration}: failed before verification, no patch applied ({e})"
            )
            ctx.record(iteration, "propose-fix", f"attempt on '{diag['function']}' failed: {e}")
            continue

        skill = route(registry, {"verify", "confirm", "re-test"}, "verify fix")
        prev_failing = set(result["failing_tests"])
        result = run_tests()
        resolved = sorted(prev_failing - set(result["failing_tests"]))
        if target_test in resolved:
            fix_history[diag["function"]].append(f"attempt {iteration}: resolved the target failure")
        else:
            # Record the ACTUAL code that was tried and what it still got
            # wrong, not just a pass/fail note — otherwise the model has no
            # way to know it already guessed a specific (wrong) approach and
            # keeps repeating the same mistake across iterations.
            new_evidence = extract_assertion_evidence(result["raw_output"], target_test) \
                if args.llm == "ollama" and target_test in result["failing_tests"] else "still failing"
            fix_history[diag["function"]].append(
                f"attempt {iteration}: tried this and it did NOT fix the failure "
                f"(new result: {new_evidence}):\n```python\n{fix['new_source']}```"
            )
        ctx.record(
            iteration, skill.name,
            f"{result['passed']} passed, {result['failed']} failed. Resolved: {resolved}",
            full_output=result["raw_output"],
        )
        print(f"  -> {result['passed']} passed, {result['failed']} failed. Resolved this round: {resolved}")

        print("\n  [context] current reasoning context (this is all a routing/LLM step would see):")
        for line in ctx.get_context_for_reasoning().splitlines():
            print(f"    {line}")

    print("\n=== Final state ===")
    if result["failed"] == 0:
        print(f"All tests passing after {iteration} iteration(s).")
    else:
        print(f"Still failing after {iteration} iterations: {result['failing_tests']}")

    print(f"\nDurable scratchpad written to: {os.path.relpath(ctx.scratchpad_path, BASE_DIR)}")
    print(f"Raw per-step logs written to: {os.path.relpath(ctx.log_dir, BASE_DIR)}/")


if __name__ == "__main__":
    import sys
    try:
        main()
    except RuntimeError as e:
        print(f"\n[error] {e}")
        sys.exit(1)
