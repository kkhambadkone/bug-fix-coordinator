---
name: apply-patch
description: Writes a proposed code change to disk, replacing the old function body with the new one.
triggers: apply, write, edit-file
---

# Apply Patch

## When to use
Immediately after propose-fix produces new_source.

## Steps
1. Read the target file.
2. Replace the old function body with new_source via exact string match
   (fail loudly if the match isn't unique/found — never guess).
3. Write the file back.

## Output contract
Return `{"file": str, "applied": bool}`
