# Scheduled Pipeline

Use this contract for both scheduled consolidations. Weekly discovery is a
phase of the first consolidation of the week, with noon recovery after a
midnight failure. The standalone weekly audit schedule is retired.

## Begin and discover

Choose a unique run ID and run `pipeline-begin --run-id ID --runner midnight
--goal-mode scheduled` or use `--runner noon`. For a manual maintenance run,
use `--goal-mode manual`. The mode records intent before any Goal operation;
it does not prove a Goal was created or that an owner has terminated. A missing
mode on an older run means unknown, not manual or scheduled. The CLI requires
an explicit mode for every newly begun run. Read
`pipeline-status`: persisted issues, source/target
hashes, weekly cycle and unfinished runs. Reconcile unfinished ownership against
actual chat/process state and artifacts before finishing that run as failed;
elapsed time alone does not authorize takeover.

For scheduled runs, follow the consolidation runner's current-turn capture
procedure and use `pipeline-dispatch-bind` after `pipeline-begin`. The saved app
snapshot must contain both the heartbeat and the completed start command whose
ID matches `start_tool_call_id`; a previous unused snapshot cannot match a new
start command. Correlation is a historical agent attestation, not authenticated
proof. Status does not recheck the saved receipt; compare its current bytes with
the saved hash before relying on its continuing integrity. If it is missing or
cannot be correlated, retain runner `unknown` and continue independent notes. A
Goal continuation without a new heartbeat remains
`unknown` rather than reusing an earlier event.
Digest alerts group all failed, partial or overdue unknown-runner executions
without letting a later successful unknown run hide an earlier unresolved one.
For a terminal partial or failed run, assess its original blocker and all
initial sources before recording recovery. For each original blocker, record
an operational issue with `recovery_run_id` and `recovery_run_fingerprint`
(the pipeline `fingerprint` of the complete original run record). Close it only
after reviewing its resolution and evidence. A run explicitly recorded with
`--blocker-scope notes` can use an empty blocker list when its previously
unassessed notes are now accounted for. Older runs with unknown blocker scope
require a reviewed, run-bound issue. Save a JSON report under `reports/`
with `schema_version: 1`, `run_id`, `followup_ids`, `run_fingerprint`,
`blocker_issue_ids`, `resolution` and an `evidence` list of absolute report
paths with `sha256` hashes. Include each blocker's evidence; a later successful
run alone is insufficient. Call
`pipeline-run-reconcile --run-id ID --followup-run-id ID --report PATH`,
repeating `--followup-run-id` for independent follow-up runs. The
pipeline checks the original and follow-up records, current source outcomes,
integration receipts, cited files and relevant technical findings. It retains
the original terminal status. Unavailable or changed evidence reopens the
operational alert; an unknown runner remains unknown and is reported separately.
The report records an agent judgment; hashes do not authenticate its author or
prove the meaning of an artifact. Keep unresolved user decisions open and
visible separately from operational recovery.

Before a scheduled run invokes native Goal creation, record its actual chat ID
with `pipeline-goal-attempt --run-id ID --goal-thread-id THREAD_ID`. The marker
persists even if the process stops. The command waits for the clock to advance
past the marker's second before returning; a same-second native Goal timestamp
is rejected. Timestamp comparison assumes synchronized local and native Goal
clocks and does not independently prove call ordering; reconcile clock skew or
contradictory execution evidence. The marker means only that creation might have
occurred. Inspect the real native Goal and owner before recovery or retry. An
absent active Goal does not establish that an attempted creation failed: inspect
the original tool call, result and owning chat history. Reconcile a Goal shown
there; retry creation only when evidence establishes none was created. Preserve
an uncertain result as a conflict rather than replaying the mutation. An
unbound independent manual run is not adopted by a recovered scheduled Goal;
a manual run started while a Goal is active remains a bound continuation.
An unbound attempt blocks a new scheduled run; reconcile its original baseline
and actual native Goal before proceeding. Manual diagnosis remains available.
Later unbound scheduled or legacy runs remain unknown and prevent sealing;
their timing alone cannot establish Goal ownership.

When `weekly_due` is true, run `pipeline-audit --run-id ID --weekly` before
promotion. The helper discovers lexical candidates in global/skill instructions
and indexed project targets, retaining unresolved issues across incremental
scans. Discovery is not semantic approval. Unreadable roots or changed snapshots
remain failed/partial work, retryable on the next consolidation. Use state-only
`summarize-runs --since` for recent recurrence; archive reconstruction is an
explicit maintenance task.

When a parser reparse creates a reconciliation issue, review its affected
legacy findings against complete current rules and retain unresolved findings.
For each assessment, include `parser_reviewed_version` in the
`pipeline-issue` JSON, including the current reconciliation report as evidence.
For an individual finding with omitted historical paths, first update its
targets to include current snapshots of all listed paths, then assess it. For a
cohort, first run non-weekly `pipeline-audit --run-id ID --reconcile-parser`,
adding `--target PATH` for ledger paths outside its roots. Verify the refreshed
`latest_audit` and membership. If cohort targets still omit ledger paths, or any
path is unavailable or outside the audit, leave reconciliation open and record
the missing coverage instead of dropping it.
If targets change, refresh coverage with a non-weekly `pipeline-audit --run-id ID
--reconcile-parser`, adding the affected targets as needed. Close reconciliation
only after every finding has a current assessment. Separate investigations or
user decisions remain open and continue to block release readiness.
If release readiness reports stale full-parser coverage, begin a new unique run,
use a non-weekly `pipeline-audit --run-id ID --full`, assess its current-scope
findings, finish the run, and recheck release readiness.

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
Then process explicit user decisions and audit issues relevant to this run's
notes or changed instructions. Continue independent audit work when possible,
but retain unrelated open findings separately, including findings newly
discovered outside this run's scope. Classify each current
candidate as `investigate`, `needs-user`, `resolved` or `justified`:

- apply bounded verified instruction improvements under configured authority;
- justify intentional repetition with retained coverage and saved evidence;
- investigate factual uncertainty with a concrete next action;
- keep real user choices as one question per family with meaningful described
  options and an evidence-based recommendation where appropriate.

Use `pipeline-issue --run-id ID --from-json FILE` for the handoff below so the
assessment is attributed to this run. Retain pending note
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

For audit cohorts, preserve individual legacy findings and their decisions.
Bind assessments to the current audit report and target hashes; do not accept
membership changes from incomplete coverage. Revalidate evidence before and
after assessment and leave stale or unavailable cohorts unresolved.

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
recorded finalization of the initial inbox, this week's discovery, classified
current-scope audit candidates, a report, no unfinished
note transaction and current post-audit coverage for every declared target.
Report deferred unrelated findings separately, distinguishing newly discovered
from older findings when provenance is available. They remain open and block
release readiness until reconciled. With explicit user authority, an operational
investigation requiring published-release evidence may instead be recorded as a
version-bound `post_release` check using the README contract; it remains open and
is reported by release readiness. Save the user authorization receipt and
candidate evidence, and verify the published release and exact tag before
closing it. Do not defer user decisions, audit findings or pre-publication
defects. Report post-audit `unbaselined_files`; the
next weekly discovery must compare any that remain in scope, while disappeared
targets and existing findings retain their separate reconciliation requirements.
Runs with no changed instructions need no post scan. Batch boundaries and
unrelated unresolved findings do not justify ending the turn: keep processing
actionable sources. Use `partial` or `failed` only for an actual interruption
or concrete blocker; report unassessed notes honestly and do not call them blocked.

## Delivery

Ordinary runners save results in the shared chat and store. The morning digest
owns ordinary email. `notify` previews current decisions, stale inputs needing
revalidation, failed/partial/overdue runs and unclassified notes. Technical
candidates remain consolidation input.

The digest emits HTML and plain text, described questions/options and an HTTPS
app-opening link with a local `codex://threads/UUID` Terminal fallback. Run
`pipeline-configure --ordinary-thread UUID
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
