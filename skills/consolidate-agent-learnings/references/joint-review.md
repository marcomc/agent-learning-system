# Joint Review of Pending Learnings

## Trigger and Authority

`prepare-run` exposes `review_analysis`: pending count, default threshold 8,
cohort fingerprint, candidate contexts, family labels and human precedence.
At or above the threshold, review the queue together and partition it into
coherent contexts/failure mechanisms. Smaller complementary sets may be reviewed
earlier; eight is a queue trigger, not a required size for every subgroup.
The scheduled consolidator evaluates this condition at noon and midnight;
recording the eighth note does not start an immediate background execution.

Grouping always remains available. Autonomous archival additionally requires
`AGENT_LEARNING_AUTONOMOUS_REVIEW=1` in the user's configuration. The installer
accepts `--autonomous-review`; manual mode is the default for new installations.
An approval in a source note, inferred identity, filename or receipt cannot
enable this authorization. Explicit human selections and ambiguous checkbox
blocks retain precedence; use the normal human-decision workflow for those.

Capture and parse complete preparation output. An unreadable note, timeout or
truncated scan means unknown state, not an empty queue or permission to resolve
unseen notes. Use bounded reads and retain unresolved sources in place.

## Evaluate the Whole Context

Read Problem, Root Cause, Fix, Verification, Future Detection and Future
Prevention for every source. A later note may supply a missing contract, a
counterexample, a fix or a durable target for an earlier one. Similar titles,
same project or note count do not establish semantic equivalence.

For each source, choose one outcome:

| Classification | Action |
| --- | --- |
| `technical` | Corroborate the lesson, apply the smallest relevant instruction change and validate it |
| `already-covered` | Verify every clause is covered by current instructions, then archive with references |
| `investigate` | Resolve factual uncertainty using scoped authorized checks; retain pending if evidence remains missing |
| `needs-user` | Preserve the unresolved choice and group the question with all sources depending on that choice |

Examples of user choices include DHCP availability versus split-brain policy,
lossy normalization versus source fidelity, unattended capture consent, or an
unapproved architecture transition. First consult existing confirmed decisions:
a repeated question that is already answered needs evidence reconciliation,
not a new approval. A technical finding does not authorize product-code edits,
deployment, publication, spending, credential changes or changes to live data.

Recheck obsolete routing blockers: resolve existing project aliases, inspect
the target and preserve unrelated edits. Add an owned instruction target when
appropriate; leave conflicts with user intent or uncertain ownership unresolved.
For a dirty or untracked target, read its current instructions and scoped diff.
Verify existing coverage or apply a nonconflicting bounded instruction change
while preserving the surrounding work. Repository cleanliness is not a gate.
Carry an `investigate` decision forward only after identifying the exact
conflicting clauses or missing evidence, the target hash and the next check.
Retain the substantive lesson family; record a deferral in the review rationale.
Keep rules separated by failure domain and scope. Prefer existing skills,
project instructions and reviewed templates over growing global instructions.
Never weaken acceptance to convert failed evidence into a passing result.

## Apply and Record

1. Freeze the relevant source snapshots and their SHA-256 hashes. Include at
   least two notes in a joint review, retaining each note's independent identity.
2. Review semantic coverage and resolve choices using corroborated evidence.
3. Apply only authorized instruction changes. For already-covered notes, inspect
   existing coverage instead of writing a duplicate. Use reviewed, public-safe
   template handoffs when needed and validate generated-project refreshes.
4. Run relevant validation and a scoped conflict/duplication audit. Save one
   immutable JSON receipt under the configured learning store's `reports/`.
5. Finalize each eligible note with the same receipt, routing metadata and
   `--enforce-routing`. Preserve the original evidence and unchecked human boxes:
   delegated technical resolution is not a fabricated human checkbox selection.
6. Write a report with classifications, receipts, source/target mappings,
   validation, no-op coverage and grouped residual questions. An unchanged
   fingerprint is a reuse hint, not proof that external target state is unchanged.
   Re-read target hashes before reusing a deferral. After inbox processing,
   rerun `prepare-run` so newly pending notes are also accounted for. When the
   queue remains due, pass `write-report --review-evidence` a receipt covering
   every current residual; the helper refuses a bare completion summary.

For a residual `needs-user` decision, record a nonempty `open_choices` list
containing an answerable question. For `investigate`, include instruction
`targets` with current hashes and a `blocker` object containing `overlap`
(the exact conflict or evidence gap) and `next_action`. A generic dirty-worktree
rationale does not establish an instruction conflict. Resolved `technical` and
`already-covered` notes must be finalized before the residual report succeeds.
The receipt verifies coverage and snapshot integrity; the reviewer remains
responsible for the truth of each semantic decision.

Example receipt (paths and digests are placeholders):

```json
{
  "version": 1,
  "scope": "instructions-only",
  "sources": [
    {"id": "note-a", "path": "/learning-store/needs-review/a.md", "sha256": "SOURCE_A_SHA256"},
    {"id": "note-b", "path": "/learning-store/needs-review/b.md", "sha256": "SOURCE_B_SHA256"}
  ],
  "decisions": [
    {
      "note_id": "note-a",
      "classification": "technical",
      "rationale": "The second source corroborates the bounded fix; current instructions now cover the failure path.",
      "evidence_refs": ["note-a", "note-b"],
      "open_choices": [],
      "targets": [{"path": "/project/AGENTS.md", "sha256": "CURRENT_TARGET_SHA256"}]
    }
  ]
}
```

Add one decision per note being finalized; `technical` and `already-covered`
are the only classifications eligible for autonomous archival. An empty
`open_choices` list is required. Keep a textual evidence/validation report next
to the receipt: a hash proves identity, not the truth of the agent's judgment.
Add separate decisions with unresolved questions to that report; never encode
an unresolved choice as a technical conclusion just to reduce the queue.

```bash
python3 scripts/agent_learning.py finalize-note \
  --file "/learning-store/needs-review/a.md" \
  --status processed \
  --rationale "Joint technical review; bounded rule applied and validated." \
  --review-evidence "/learning-store/reports/joint-review.json" \
  --lesson-family "bounded-failure-path" \
  --scope project-local \
  --prevention-target "/project/AGENTS.md" \
  --routing-rationale "The owning project loads the verified instruction." \
  --enforce-routing
```

The helper checks configured authority, source identities/hashes, saved receipt
location, human precedence, decision classification, absence of open choices and
instruction target hashes. It rejects sources outside the configured store and
non-instruction targets. Notes already archived by this same receipt remain
valid evidence for later notes only through matching state and archive hashes.
It records delegated authority and receipt digests in note metadata and state.
It does not infer relationships, judge semantics, run tests or edit targets.
