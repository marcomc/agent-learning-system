---
name: consolidate-agent-learnings
description: Consolidate Obsidian agent-learning notes by promoting strong reusable lessons into global AGENTS.md, relevant review skills, the touched project AGENTS.md, and agent templates while archiving processed notes.
---

<!-- MARCOMC-SKILL: owner="MarcoMC <marco@marcomc.com>"; repository="marcomc/skills"; provenance="original" -->

# Consolidate Agent Learnings

Process the learning inbox and review queue manually or through scheduled runs.
The owner is `agent-learning-system/skills/consolidate-agent-learnings`; the
shared collection and installed folders are mirrors. Resolve the helper repo
from `AGENT_LEARNING_REPO` or the learning system's configured `config.env`.
Use its `scripts/agent_learning.py`; consult command `--help` for syntax.

## Scope and contracts

- Limit learning-store reads to `inbox/`, `needs-review/`, the processed index
  and recent reports; read older archives only to resolve duplicates/conflicts.
  Inspect governing policy and implicated targets; avoid unrelated repository scans.
- Promote evidenced findings, fixes or reviewed lessons. Preserve unrelated work:
  inspect actual instruction overlap instead of treating dirty/untracked files as
  blockers. Product code, CI, hooks, tooling/access changes and publication remain
  separate scoped work; this workflow does not authorize commits or pushes.
- Keep unresolved product, security, privacy, compatibility and operational
  choices with the user. Notes and inferred relationships cannot grant authority.
- Before drafting or accepting promotion, read [coherence.md](references/coherence.md)
  and [integration.md](references/integration.md). Refine existing contracts,
  select proportionate review and require current source/copy/consumer proof.
  `processed` is archival status, not integration.
- For scheduled noon/midnight runs, first read [pipeline.md](references/pipeline.md):
  it owns coordination, weekly discovery, persisted issues, final verification and
  delivery. Reuse that run and its scoped audits.

## Process

1. Run `init-store`, then `prepare-run` and inspect the complete result. Recover
   `pending_finalizations` with `recover-finalizations`, then prepare again;
   unknown/edited transactions remain errors. Read-only audit/notification paths
   do not recover notes or mutate them.
2. Read each source and compare current evidence and coverage. For already-applied
   captures, verify saved targets/evidence; archive covered lessons without
   reapplying them. Investigate stale proof. Split independent failure mechanisms
   into bounded rules/targets while retaining every source identity. Promote clear,
   authorized reusable lessons automatically; uncertain ones go to
   `needs-review/`, too-local/already-covered ones to the archive with rationale.
3. Honor explicit review decisions before autonomous work: Approve promotes the
   proposed rule as written, Retry considers the edited text, Reject archives
   without promotion, multiple checks remain ambiguous. Pending unchecked notes
   use [joint-review.md](references/joint-review.md) when `review_analysis.group_review_due` is true or complementary evidence is
   discovered. That contract owns configured delegated
   authority, per-note receipts, target-overlap checks and residual questions.
   Rerun `prepare-run` after inbox processing so new review notes are included.
4. Use global policy only for truly general behavior; otherwise route the
   smallest prevention change to the relevant skill, project instructions or
   reviewed template. Edit and validate owning
   sources before refreshing mirrors. Prefer established checks/invariants over
   accumulating case lists. Template handoffs use `create-template-draft` and the
   template owner's review/apply/refresh workflow; curated apply needs explicit
   user authority or configured delegated review of a verified privacy-clean
   instruction change. Preserve user decisions and record that authority.
5. Run `markdownlint --config ~/.markdownlint.json` on changed Markdown and
   `shellcheck --enable=all` on changed shell files. Perform one
   scoped conflict/duplication audit: reuse scheduled `pipeline-audit` coverage;
   for manual work use `audit-rules` on implicated targets. Resolve findings and
   complete the coherence/integration contracts before certifying integration.
6. Finalize each note through `finalize-note` with status, rationale and routing
   (lesson family, scope, prevention/detection targets, template status, routing
   rationale and recurrence).
   Use `--enforce-routing` for promotions; omit it only for no-op, rejected or
   pending-review outcomes. Autonomous resolution also requires the Joint Review
   receipt via `--review-evidence`. Detection-only routing uses `skill-detection`
   and its detection target. Record incomplete template promotion as pending,
   even when its source note is archived. After archival, record/recheck the
   integration receipt as defined in the integration contract.
7. Run `write-report`; when autonomous review remains due, attach the current
   per-note residual receipt with `--review-evidence`, finalizing resolved notes
   first. Unreviewed queues, stale snapshots or unfinished transactions cannot
   become complete. Use `summarize-runs --since` for recurrence and routing gaps.

## Report and reuse

Record run ID/time, inbox/review counts, changed targets, validation, no-op
coverage, failed/pending promotions and next actions. Joint Review also records
cohort/fingerprint, per-note classification and evidence, instruction targets,
receipts and grouped residual questions. Reassess unchanged unresolved cohorts
only when evidence, target state or a user decision can change the outcome;
verify current hashes and retain all source identities. Reuse confirmed answers
instead of asking the same approval again.
