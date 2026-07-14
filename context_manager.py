"""
Two-tier memory model for a multi-iteration coordinator:

1. working_context  — a small, in-memory list of recent, DISTILLED entries.
   This is what actually gets "shown" to the routing/reasoning step, i.e.
   the analog of a real LLM coordinator's prompt context window.

2. scratchpad file   — a durable, on-disk running log of every step taken,
   every hypothesis tried, and every result. It survives compaction and is
   re-read instead of being carried token-for-token in the window.

Verbose/raw tool output (full pytest logs, full grep dumps) never enters
working_context at all — it's archived to a log file, on the assumption
that a human or a follow-up subagent can inspect it on demand, but it
would otherwise just burn context budget for no benefit turn after turn.

When working_context grows past MAX_RAW_ENTRIES, the oldest entries are
collapsed into a rolling compacted summary string, freeing room while
keeping the essential facts (what was tried, what happened).
"""

import os
from datetime import datetime


class ContextManager:
    MAX_RAW_ENTRIES = 4

    def __init__(self, scratchpad_path, log_dir):
        self.working_context = []
        self.compacted_summary = ""
        self.scratchpad_path = scratchpad_path
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        os.makedirs(os.path.dirname(scratchpad_path), exist_ok=True)
        self._init_scratchpad()

    def _init_scratchpad(self):
        with open(self.scratchpad_path, "w") as f:
            f.write("# Bug Fix Scratchpad\n\n")
            f.write(f"Started: {datetime.now().isoformat()}\n\n")

    def record(self, iteration, skill_name, summary, full_output=None):
        entry = {"iteration": iteration, "skill": skill_name, "summary": summary}
        self.working_context.append(entry)

        if full_output:
            log_path = os.path.join(self.log_dir, f"iter{iteration}_{skill_name}.log")
            with open(log_path, "w") as f:
                f.write(full_output)

        with open(self.scratchpad_path, "a") as f:
            f.write(f"## Iteration {iteration} — {skill_name}\n{summary}\n\n")

        self._maybe_compact()

    def _maybe_compact(self):
        if len(self.working_context) > self.MAX_RAW_ENTRIES:
            overflow = self.working_context[: -self.MAX_RAW_ENTRIES]
            self.working_context = self.working_context[-self.MAX_RAW_ENTRIES :]
            new_lines = [f"iter{e['iteration']}/{e['skill']}: {e['summary']}" for e in overflow]
            if self.compacted_summary:
                self.compacted_summary += " | " + " | ".join(new_lines)
            else:
                self.compacted_summary = " | ".join(new_lines)
            print(f"  [context] compacted {len(overflow)} older entries into rolling summary")

    def get_context_for_reasoning(self):
        parts = []
        if self.compacted_summary:
            parts.append(f"[compacted history] {self.compacted_summary}")
        for e in self.working_context:
            parts.append(f"[iter {e['iteration']}] {e['skill']}: {e['summary']}")
        return "\n".join(parts)
