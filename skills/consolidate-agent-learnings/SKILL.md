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

1. Prepare the run and inspect the returned JSON:

   ```bash
   repo="/path/to/agent-learning-system"
   python3 "${repo}/scripts/agent_learning.py" prepare-run
   ```

1. Process `inbox` notes.
   - Promote clear, safe, reusable lessons automatically.
   - Move uncertain notes to `needs-review/` with the review checkbox block.
   - Move too-local or already-covered notes to `processed/YYYY/MM/` with a
     no-op rationale.

1. Process `needs-review` notes only when one checkbox is selected.
   - `Approve`: promote the proposed rule as written.
   - `Retry`: promote after considering the user's edited text.
   - `Reject`: archive as rejected without promotion.
   - No checkbox: leave pending.
   - Multiple checkboxes: leave pending and report ambiguity.

1. Promotion targets are bounded:
   - `$HOME/AGENTS.md` for truly general behavior.
   - The smallest relevant reusable review or remediation skill.
   - The touched project's `AGENTS.md` when the lesson is project-local.
   - The existing `update-agents-file-templates` workflow after central or
     project instruction changes when template promotion is useful.

1. Finalize each note with the helper:

   ```bash
   repo="/path/to/agent-learning-system"
   python3 "${repo}/scripts/agent_learning.py" finalize-note \
     --file "/path/to/note.md" \
     --status processed \
     --rationale "Promoted to docs atom draft and docs release review skill." \
     --lesson-family "markdown-validation-contract" \
     --scope atom \
     --prevention-target "atom:docs" \
     --detection-target "skill:pre-pr-review-docs-release" \
     --template-upstream-status draft-created \
     --routing-rationale "Documentation agents load the docs atom before editing Markdown." \
     --recurrence-check new \
     --enforce-routing
   ```

   Use `--status needs-review` for pending review and `--status rejected` for a
   reviewed rejection. For detection-only lessons, use
   `--scope skill-detection --detection-target ... --enforce-routing`. Omit
   `--enforce-routing` only for no-op, rejected, or pending-review outcomes.

1. When a reusable prevention rule belongs in the template repository, create
   an ignored draft handoff. Do not edit curated templates from this skill
   unless the user explicitly asks for template apply:

   ```bash
   repo="/path/to/agent-learning-system"
   python3 "${repo}/scripts/agent_learning.py" create-template-draft \
     --template-repo "/path/to/agents-file-templates-and-skills" \
     --lesson-family "markdown-validation-contract" \
     --source-note "/path/to/note.md" \
     --proposed-template "docs" \
     --candidate-rule "Run markdownlint before completing Markdown documentation changes." \
     --prevention-target "atom:docs" \
     --privacy-verdict clean \
     --enforce-routing
   ```

   The template repository owns reviewed apply and generated-project refresh
   reports through its `update-agents-file-templates` workflow.

1. Write a report:

   ```bash
   repo="/path/to/agent-learning-system"
   python3 "${repo}/scripts/agent_learning.py" write-report \
     --run-id "$RUN_ID" \
     --summary "Processed 3 notes; promoted 1; moved 2 to review."
   ```

1. Summarize recurrence and routing gaps:

   ```bash
   repo="/path/to/agent-learning-system"
   python3 "${repo}/scripts/agent_learning.py" summarize-runs \
     --since "YYYY-MM-DD" \
     --format markdown
   ```

1. Validate every changed Markdown file with:

   ```bash
   markdownlint --config ~/.markdownlint.json <changed-md-files>
   ```

   Also run `shellcheck --enable=all` for changed shell files.

1. Run a conflict/duplication audit on the promotion targets:

   ```bash
   repo="/path/to/agent-learning-system"
   python3 "${repo}/scripts/agent_learning.py" audit-rules
   ```

## Report Requirements

Each consolidation report must include:

- run id and timestamp;
- inbox notes processed;
- review notes processed or left pending;
- files changed;
- validation commands run;
- no-op and needs-review rationales;
- any failed promotion with next action.
