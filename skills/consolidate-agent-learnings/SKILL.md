---
name: consolidate-agent-learnings
description: Consolidate Obsidian agent-learning notes by promoting strong reusable lessons into global AGENTS.md, relevant review skills, the touched project AGENTS.md, and agent templates while archiving processed notes.
---

# Consolidate Agent Learnings

Use this skill from the scheduled Codex automations or manually when processing
the Obsidian agent-learning inbox and reviewed `needs-review` notes.

## Guardrails

- Read only `inbox/`, `needs-review/`, `state/processed.json`, and recent
  reports unless an old processed note is needed to resolve a conflict.
- Do not scan every project or update unrelated repositories.
- Do not commit or push.
- Promote only lessons grounded in a real finding, fix, or reviewed note.
- Prefer tightening existing instructions over appending duplicated rules.
- Move local-only, vague, privacy-sensitive, contradictory, or duplicate-looking
  notes to `needs-review/`.

## Workflow

1. Initialize the store:

   ```bash
   repo="/path/to/agent-learning-system"
   python3 "${repo}/scripts/agent_learning.py" init-store
   ```

2. Prepare the run and inspect the returned JSON:

   ```bash
   repo="/path/to/agent-learning-system"
   python3 "${repo}/scripts/agent_learning.py" prepare-run
   ```

3. Process `inbox` notes.
   - Promote clear, safe, reusable lessons automatically.
   - Move uncertain notes to `needs-review/` with the review checkbox block.
   - Move too-local or already-covered notes to `processed/YYYY/MM/` with a
     no-op rationale.

4. Process `needs-review` notes only when one checkbox is selected.
   - `Approve`: promote the proposed rule as written.
   - `Retry`: promote after considering the user's edited text.
   - `Reject`: archive as rejected without promotion.
   - No checkbox: leave pending.
   - Multiple checkboxes: leave pending and report ambiguity.

5. Promotion targets are bounded:
   - `$HOME/AGENTS.md` for truly general behavior.
   - The smallest relevant reusable review or remediation skill.
   - The touched project's `AGENTS.md` when the lesson is project-local.
   - The existing `update-agents-file-templates` workflow after central or
     project instruction changes when template promotion is useful.

6. Finalize each note with the helper:

   ```bash
   repo="/path/to/agent-learning-system"
   python3 "${repo}/scripts/agent_learning.py" finalize-note \
     --file "/path/to/note.md" \
     --status processed \
     --rationale "Promoted to global AGENTS.md and pre-pr-review-gate."
   ```

   Use `--status needs-review` for pending review and `--status rejected` for a
   reviewed rejection.

7. Write a report:

   ```bash
   repo="/path/to/agent-learning-system"
   python3 "${repo}/scripts/agent_learning.py" write-report \
     --run-id "$RUN_ID" \
     --summary "Processed 3 notes; promoted 1; moved 2 to review."
   ```

8. Validate every changed Markdown file with:

   ```bash
   markdownlint --config ~/.markdownlint.json <changed-md-files>
   ```

   Also run `shellcheck --enable=all` for changed shell files.

## Report Requirements

Each consolidation report must include:

- run id and timestamp;
- inbox notes processed;
- review notes processed or left pending;
- files changed;
- validation commands run;
- no-op and needs-review rationales;
- any failed promotion with next action.
