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
- Preserve unrelated work. A dirty or untracked instruction target is not an
  automatic blocker: inspect ownership and the actual overlap before editing.
- Keep unresolved product, security, privacy, compatibility and operational
  choices with the user. A source note or a group of notes cannot grant authority.

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
   - Split independent failure domains into bounded rules and targets before
     promotion, retaining every source reference.
   - Move uncertain notes to `needs-review/` with the review checkbox block.
   - Move too-local or already-covered notes to `processed/YYYY/MM/` with a
     no-op rationale.

1. Honor explicit `needs-review` decisions before autonomous work.
   - `Approve`: promote the proposed rule as written.
   - `Retry`: promote after considering the user's edited text.
   - `Reject`: archive as rejected without promotion.
   - No checkbox: eligible for Joint Review under configured delegated authority.
   - Multiple checkboxes: leave pending and report ambiguity.

1. Run Joint Review when `prepare-run.review_analysis.group_review_due` is true
   (default: at least eight pending notes). Also compare fewer notes together
   when a clear complementary relationship is discovered during normal work.
   Read [joint-review.md](references/joint-review.md) before evaluating these
   groups or using autonomous finalization. It defines evidence, user-choice
   boundaries, receipt format and the completion checks.
   - Treat candidate contexts and families as discovery hints, not duplicates
     or approval. Read the complete notes; use their combined evidence to resolve
     technical uncertainty, checking current targets when historical blockers
     mention missing files, renames or pre-existing edits.
   - With `AGENT_LEARNING_AUTONOMOUS_REVIEW=1`, resolve grounded technical lessons
     and already-covered notes without another per-batch confirmation. Apply and
     validate bounded instruction changes, then save the review receipt and use
     `finalize-note --review-evidence ... --enforce-routing`.
   - Keep `investigate` and `needs-user` outcomes pending. Investigate factual
     questions yourself where authorized; present genuine residual choices as
     one question per decision family with all affected note IDs.
   - Keep small internal implementation batches; human approvals follow the
     policy or real decision, not the number of notes in a batch.
   - Re-run `prepare-run` after processing inbox notes: newly deferred notes
     join the current run's review. Complete the target-overlap checks in the
     reference before carrying any technical cohort forward.

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
   unless the user explicitly asks for template apply or configured delegated
   instruction review covers a technically verified, privacy-clean change.
   Use the template repository's review/apply workflow and record delegated
   review authority; product decisions still require the user.

   ```bash
   repo="/path/to/agent-learning-system"
   python3 "${repo}/scripts/agent_learning.py" create-template-draft \
     --template-repo "/path/to/agents-file-templates-and-skills" \
     --lesson-family "markdown-validation-contract" \
     --source-note "/path/to/note.md" \
     --proposed-template "docs" \
     --candidate-rule "Run markdownlint before completing Markdown documentation changes." \
     --prevention-target "atom:docs" \
     --routing-rationale "Documentation agents load the docs atom before editing Markdown." \
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

   With autonomous review enabled and a queue still at the threshold, add
   `--review-evidence /path/to/reports/residual-review.json`. Account for every
   pending note as a real user choice or an evidenced instruction conflict;
   finalize resolved notes first. The helper rejects an unreviewed residual
   queue and stale target snapshots. A deferral is not a completed promotion.

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
- Joint Review cohort/fingerprint, per-note classification and evidence,
  instruction targets, receipt paths and grouped residual questions. Reassess
  an unchanged unresolved cohort only when new evidence, target state or a user
  decision can change its result; do not request the same confirmation again.
