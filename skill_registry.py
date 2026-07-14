"""
Loads skills from individual <name>.md files.

Key idea: metadata (name + description + trigger keywords) is parsed
eagerly for ALL skills at startup, but the full instruction body of a
skill is only read into "active context" the moment the coordinator
actually selects that skill. With a large skill library, this keeps the
coordinator's default context small (it only ever "sees" a one-line
description per skill) while still letting it discover and use any of
them.
"""

import os
import re


class Skill:
    def __init__(self, name, description, triggers, body, path):
        self.name = name
        self.description = description
        self.triggers = triggers
        self.body = body
        self.path = path

    def __repr__(self):
        return f"<Skill {self.name}>"


def _parse_frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        raise ValueError("Skill file missing --- frontmatter block")
    meta_block, body = m.group(1), m.group(2)
    meta = {}
    for line in meta_block.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        meta[key.strip()] = val.strip()
    triggers = [t.strip() for t in meta.get("triggers", "").split(",") if t.strip()]
    return meta.get("name", ""), meta.get("description", ""), triggers, body.strip()


class SkillRegistry:
    def __init__(self, skills_dir):
        self.skills_dir = skills_dir
        self.skills = {}
        self._load_metadata_only()

    def _load_metadata_only(self):
        for fname in sorted(os.listdir(self.skills_dir)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(self.skills_dir, fname)
            with open(path) as f:
                text = f.read()
            name, description, triggers, body = _parse_frontmatter(text)
            self.skills[name] = Skill(name, description, triggers, body, path)

    def catalog(self):
        """What the coordinator sees by default: name + description only,
        for every skill in the library."""
        return {name: s.description for name, s in self.skills.items()}

    def select(self, need_tags):
        """Score each skill's trigger keywords against the coordinator's
        current 'need' tags and return the best match. This stands in for
        semantic routing over skill descriptions (a real system would use
        embedding similarity or an LLM call instead of keyword overlap)."""
        best, best_score = None, -1
        for skill in self.skills.values():
            score = len(set(skill.triggers) & set(need_tags))
            if score > best_score:
                best, best_score = skill, score
        return best

    def load_body(self, skill_name):
        """Explicit lazy-load step: only now does the skill's full
        instruction body enter the coordinator's active context."""
        skill = self.skills[skill_name]
        print(
            f"  [skills] loading full instructions for '{skill_name}' "
            f"({len(skill.body)} chars) — was not in context until now"
        )
        return skill.body
