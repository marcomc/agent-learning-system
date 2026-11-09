# Scheduled Pipeline

Use this contract for both scheduled consolidations. Weekly discovery is a
phase of the first consolidation of the week, with noon recovery after a
midnight failure. The standalone weekly audit schedule is retired.

## Begin and discover

Choose a unique run ID and run `pipeline-begin --run-id ID --runner midnight`
or `--runner noon`. Read `pipeline-status`: persisted issues, source/target
hashes, weekly cycle and unfinished runs. Reconcile unfinished ownership against
actual chat/process state and artifacts before finishing that run as failed;
elapsed time alone does not authorize takeover.

When `weekly_due` is true, run `pipeline-audit --run-id ID --weekly` before
promotion. The helper discovers lexical candidates in global/skill instructions
and indexed project targets, retaining unresolved issues across incremental
scans. Discovery is not semantic approval. Unreadable roots or changed snapshots
remain failed/partial work, retryable on the next consolidation. Use state-only
`summarize-runs --since` for recent recurrence; archive reconstruction is an
explicit maintenance task.

## Consolidate and classify

Capture the initial inbox inventory and prioritize the oldest notes. Use
`prepare-run.inbox_analysis.candidate_cohorts`, `candidate_contexts` and shared skill,
source-run and review contexts to investigate related sources together. These
are hints, not proof of equivalence or authority: read each note and retain its
own decision and evidence. Reuse shared target and source checks within a cohort.
Consider inbox and review notes together when they address the same mechanism;
the eight-note threshold does not limit grouping inbox notes.

`prepare-run` bounds individual note reads. If `inventory_complete` is false,
retain `unavailable_sources` as unknown, retry them, and process independent
readable cohorts. Never report the partial inventory as empty or complete. A run
that began with unreadable inbox sources cannot finish `completed`; retry those
sources in a new run after access recovers.

Continue in bounded batches until each initial note is finalized or has a
specific unresolved blocker and next action. A technical
blocker on one note does not defer independent notes. Recheck the inventory and
include counts, the oldest remaining note and per-note blockers in the report.
Then process explicit user decisions and open audit issues and their implicated
current instruction contexts. Classify
each candidate as `investigate`, `needs-user`, `resolved` or `justified`:

- apply bounded verified instruction improvements under configured authority;
- justify intentional repetition with retained coverage and saved evidence;
- investigate factual uncertainty with a concrete next action;
- keep real user choices as one question per family with meaningful described
  options and an evidence-based recommendation where appropriate.

Use `pipeline-issue --from-json FILE` for the handoff below. Retain pending note
IDs and current hashes in `sources`. Group related decisions while preserving
each source. A candidate can be evaluated alone: the eight-note trigger does
not constrain audit dispositions. Honor human checkboxes and explicit chat
answers. Quote direct chat-answer evidence in the report and follow the normal
human-decision/routing workflow; never fabricate checkbox selections.

This workflow edits or adds bounded instruction content. Removing an entire
managed instruction file or uninstalling a skill is a separate operation;
unexpected disappearance remains a partial/unverifiable cycle. Merging or
removing redundant clauses within an existing file is supported.

```json
{
  "id": "retention-policy",
  "kind": "review",
  "status": "needs-user",
  "title": "Choose source retention",
  "description": "Both policies are supported; storage and reversibility differ.",
  "question": "Which policy should future processing use?",
  "options": [
    {"id": "A", "label": "Retain", "description": "Allows later reconciliation.", "recommended": true},
    {"id": "B", "label": "Discard after verification", "description": "Uses less storage but prevents source recovery."}
  ],
  "sources": [{"note_id": "NOTE_ID", "path": "/learning-store/needs-review/note.md", "sha256": "CURRENT_SHA256"}],
  "targets": [{"path": "/project/AGENTS.md", "sha256": "CURRENT_SHA256"}]
}
```

`kind` is `audit`, `review` or `operational`. Reuse audit-generated IDs. Updates
may omit unchanged fields. `resolved` and `justified` need a `resolution` and
`evidence` containing report paths in this store. Close note issues while their
sources remain available, then finalize through the normal evidence/routing
gate; a failed finalization leaves a partial run. Use the configured digest
language for decision titles, descriptions, questions, options and run
summaries. Preserve the meaning of existing decisions when revalidating them;
do not invent choices or translate quoted source evidence.

## Verify and finish

Follow [integration.md](integration.md) for source ownership, full installed-tree
parity, template review, generated-consumer refresh and integration receipts.
Audit previously verified receipts for drift; process pending handoffs rather
than repeatedly skipping their review. Keep historical missing-proof debt
separate from new failed or incomplete promotions.

After instruction changes, run one `pipeline-audit --run-id ID --target FILE`
with repeated `--target` arguments for every changed instruction file. It
compares current context with the saved baseline and retains unresolved issues.
Semantically verify affected findings and classify new candidates. File hashes
prove input identity, not correctness. Reuse unchanged proof rather than running
a second full independent audit or processing archived history.

Validate files, finalize eligible notes and save the consolidation report. Run
`pipeline-finish --run-id ID --status completed --summary TEXT --report REPORT --require-integration`,
with every changed instruction file repeated as `--target`. Completion requires
recorded finalization of the initial inbox, this week's discovery, classified audit candidates, a report, no unfinished
note transaction and current post-audit coverage for every declared target.
Runs with no changed instructions need no post scan. Batch boundaries and
unrelated unresolved findings do not justify ending the turn: keep processing
actionable sources. Use `partial` or `failed` only for an actual interruption
or concrete blocker; report unassessed notes honestly and do not call them blocked.

## Delivery

Ordinary runners save results in the shared chat and store. The morning digest
owns ordinary email. `notify` previews current decisions, stale inputs needing
revalidation, failed/partial/overdue runs and unclassified notes. Technical
candidates remain consolidation input.

The digest emits HTML and plain text, described questions/options and a local
`codex://threads/UUID` link. Run `pipeline-configure --ordinary-thread UUID
--release-thread UUID` only after verifying project/chat ownership in Codex.
The desktop app must handle local chat links. IDs belong in local state, not
published prompts.

When `AGENT_LEARNING_EMAIL_FROM` is set, use `notify --send-msmtp`; the address
must already be verified as a sender by the mail provider. It is independent of
the recipient. Otherwise, for Gmail, run `notify --prepare`, retain its digest ID, then immediately run
`notify --check-digest ID`. Send its exact subject, HTML and plain text using a
connector supporting both. After provider confirmation run `notify-ack
--digest-id ID --receipt PROVIDER_ID`. With no suitable connector, use
`notify --send-msmtp` instead of reserving for Gmail. It sends MIME alternatives
and acknowledges only after success; the account's sender must be configured.

An unacknowledged outbox blocks another send. Investigate provider evidence:
acknowledge delivery, or clear with `notify-ack --not-delivered` and verified
non-delivery evidence. Never automatically replay an uncertain submission.
Unchanged items are suppressed for seven days; changed questions/failures can
be sent sooner. Revalidate before submission. `NO_ACTION_REQUIRED` means no mail.

Follow the consolidation skill's `references/coherence.md` before promotion:
review the complete affected contract with proportionate independent lenses and
retain current coherence proof. Weekly discovery must assess cumulative size,
repetition and rule interactions across promotions; lexical candidates and
budget warnings require semantic classification, not another appended rule.
