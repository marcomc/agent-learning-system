---
name: record-agent-learning
description: Capture a real reusable finding, fix, regression, or workflow lesson from the current Codex session into the configured Obsidian agent-learning inbox.
---

# Record Agent Learning

Use this skill near the end of a session when the work produced a reusable
learning from a real problem. Do not use it for ordinary sessions with no
finding, fix, regression, workflow correction, or prevention lesson.

## Capture Criteria

Record a learning when at least one is true:

- a bug, review finding, failed validation, or production/dev issue was fixed;
- a recurring failure class became clearer;
- the session corrected an agent workflow, skill, or project instruction;
- a new prevention rule would help future implementation or review.

Skip capture when the session only answered a question, made a routine edit, or
ended with no actionable lesson.

## Workflow

1. Summarize the lesson before final response.
2. Keep the note factual and compact; avoid long transcripts.
3. Prefer generic failure classes over one-line bug trivia.
4. Include the project path and source skill or workflow when known.
5. Use the bundled helper to write the note to Obsidian.

Example:

```bash
repo="/path/to/agent-learning-system"
python3 "${repo}/scripts/agent_learning.py" record \
  --project-path "$PWD" \
  --source-skill "scoped-pre-pr-remediation" \
  --title "Disabled feature left installer-created privileges behind" \
  --problem "Disabled feature left a helper grant installed." \
  --root-cause "Disable logic skipped persisted install-time artifacts." \
  --fix "Remove helper grants when the feature is disabled." \
  --verification "Ran the repo gate and a disable-mode regression." \
  --future-detection "Review install-time grants and generated config." \
  --future-prevention "Add teardown symmetry tests with disable logic."
```

For complex text, pass JSON with `--from-json <file>` or `--from-json -`.

## Output

Report the created Obsidian note path. Do not promote rules directly from this
skill; promotion belongs to `consolidate-agent-learnings`.
