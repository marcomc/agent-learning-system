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

Run the ordinary skill workflow for notes and explicit user decisions. Read
open audit issues and their implicated current instruction contexts. Classify
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
gate; a failed finalization leaves a partial run. Use the user's language for
decision titles, descriptions, questions and options.

## Verify and finish

After instruction changes, run one `pipeline-audit --run-id ID --target FILE`
with repeated `--target` arguments for every changed instruction file. It
compares current context with the saved baseline and retains unresolved issues.
Semantically verify affected findings and classify new candidates. File hashes
prove input identity, not correctness. Reuse unchanged proof rather than running
a second full independent audit or processing archived history.

Validate files, finalize eligible notes and save the consolidation report. Run
`pipeline-finish --run-id ID --status completed --summary TEXT --report REPORT`,
with every changed instruction file repeated as `--target`. Completion requires
this week's discovery, classified audit candidates, a report, no unfinished
note transaction and current post-audit coverage for every declared target.
Runs with no changed instructions need no post scan. Use `partial` or `failed`
with a concrete reason when required evidence is unavailable.

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

For Gmail, run `notify --prepare`, retain its digest ID, then immediately run
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
