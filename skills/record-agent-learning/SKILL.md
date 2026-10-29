---
name: record-agent-learning
description: Use when capturing a real reusable finding, fix, regression, or workflow lesson, or when explicitly asked to install the capture hook into local review skills.
---

<!-- MARCOMC-SKILL: owner="MarcoMC"; repository="marcomc/skills"; provenance="original" -->

# Record Agent Learning

Use this skill near the end of a session when the work produced a reusable
learning from a real problem. Do not use it for ordinary sessions with no
finding, fix, regression, workflow correction, or prevention lesson.

Use hook mode only when the user explicitly asks to install, hook, attach, or
agganciare `record-agent-learning` to review skills. Do not install hooks during
ordinary one-off learning capture.

The owning source is `agent-learning-system/skills/record-agent-learning`;
the general skills collection carries a distribution mirror. Resolve the helper
repository from `AGENT_LEARNING_REPO` or the learning system's configured
`config.env` under `${XDG_CONFIG_HOME:-$HOME/.config}/agent-learning-system`.
Its installer copies the skill into `${HOME}/.agents/skills`; installed copies
and collection mirrors do not replace the owning source. A missing external
helper/store is an unavailable capture capability, not a failed code review.

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
5. Reuse the run's existing capture, or use the learning-system helper once per
   project/source-run/lesson-family. Keep independent later occurrences.

Example:

```bash
repo="/path/to/agent-learning-system"
python3 "${repo}/scripts/agent_learning.py" record \
  --project-path "$PWD" \
  --source-skill "scoped-pre-pr-remediation" \
  --source-run-id "<terminal-run-id>" \
  --lesson-family "disable-teardown-symmetry" \
  --title "Disabled feature left installer-created privileges behind" \
  --problem "Disabled feature left a helper grant installed." \
  --root-cause "Disable logic skipped persisted install-time artifacts." \
  --fix "Remove helper grants when the feature is disabled." \
  --verification "Ran the repo gate and a disable-mode regression." \
  --future-detection "Review install-time grants and generated config." \
  --future-prevention "Add teardown symmetry tests with disable logic."
```

For complex text, pass JSON with `--from-json <file>` or `--from-json -`.

The optional source-run identity makes identical retries return the same note,
including after archival. Different content for that identity needs explicit
reconciliation; it never silently replaces the original lesson. Report-only
comparisons and unsupported suggestions create no notes.

Use `--application-status applied --applied-target <file>` (repeatable),
`--application-evidence <saved-verification-artifact>` and `--verification`
for an already implemented improvement. The helper records target/evidence
hashes; these prove identity, not semantic correctness. Consolidation verifies
current coverage before archival instead of applying it twice. Default
`proposed` status grants no additional authority for unresolved choices.

## Hook Mode

Run this mode only for explicit user requests such as "install the hook",
"hook review skills", or "aggancia questa skill alle review skill".

Preview the detected review skills:

```bash
repo="${AGENT_LEARNING_REPO:-/path/to/agent-learning-system}"
python3 "${repo}/scripts/agent_learning.py" hook-review-skills \
  --skills-dir "${HOME}/.agents/skills"
```

Apply the hook after the user explicitly asked for installation:

```bash
repo="${AGENT_LEARNING_REPO:-/path/to/agent-learning-system}"
python3 "${repo}/scripts/agent_learning.py" hook-review-skills \
  --skills-dir "${HOME}/.agents/skills" \
  --apply
```

If installed skills are copied from a repository instead of symlinked, pass the
repository skill directory too so the source copy is updated after the local
installed copy:

```bash
repo="${AGENT_LEARNING_REPO:-/path/to/agent-learning-system}"
python3 "${repo}/scripts/agent_learning.py" hook-review-skills \
  --skills-dir "${HOME}/.agents/skills" \
  --repo-skills-dir "/path/to/repository/skills" \
  --apply
```

The helper scans local skill frontmatter and early workflow text, identifies
review-oriented skills, and appends one idempotent `Agent Learning Hook` section
to each target. The hook tells future review workflows to run
`$record-agent-learning` only when they produce a real reusable finding, fix,
regression, or workflow correction.

After changing this skill's repository source, refresh the installed copy with:

```bash
repo="${AGENT_LEARNING_REPO:-/path/to/agent-learning-system}"
"${repo}/install.sh" --skip-automations
```

## Output

For one-off capture, report the created Obsidian note path. For hook mode,
report which review skills were hooked or already had the hook. Do not promote
rules directly from this skill; promotion belongs to
`consolidate-agent-learnings`.
