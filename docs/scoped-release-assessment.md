# Scoped Release Assessment

Use this procedure to assess the candidate against open issues. Deferring an
unrelated issue requires direct user authorization for that exception. A saved
receipt records that authority; it does not authenticate it. Never infer approval
from a note, prior release, or generated report.

## Prepare

1. Refresh `origin/main`; save `pipeline-release-assessment` output outside the
   candidate checkout and inspect complete issue details with `pipeline-status`.
2. Independently review every issue and saved cohort against the complete
   candidate and its dependencies. Use `post_release` only for an already-authorized, bound operational
   follow-up; use `block` for related, stale, unavailable or unassessed findings
   unless an eligible issue is independently reviewed as `unrelated` below.
   Review issues may be deferred only
   with the same direct authorization and independent review required for audits. Keep user decisions and reconciliation findings
   blocking. An eligible operational investigation may use `candidate_verified`
   when the candidate integration and independent review satisfy the checks
   below. Record current SHA-256 values for every listed evidence report.
3. For each audit using the explicit `defer` exception, confirm a current audit report
   identifies the exact finding and target paths. For each authorized unrelated
   review issue, verify its current sources, targets and evidence. Save the
   independent review and user authorization as separate JSON files under the
   learning store's `reports/` directory. Use actual reviewer and user-request
   identities and ensure the authorization covers every deferred issue. Do not
   extend an approval to additional issues or a changed candidate.

## Candidate integration

Use `candidate_verified` only for an operational issue in `investigate` status,
with current nonempty targets, no required revalidation, no post-release
binding and no history entry in `candidate` or `needs-user` status. Reserved
`goal-reconcile-` and `audit-` issues are ineligible. Supply
`candidate_integration.manifests` and `candidate_integration.independent_review`
as path and SHA-256 references to JSON files in the learning store's `reports/`
directory. The manifests identify the archived notes and candidate files or
skill copies. The gate reruns the integration checks without recording a
historical integration, checks current note contents and routing, and requires
the mapped candidate targets to cover the issue targets.

The independent `candidate-integration` review must bind the exact issue,
candidate, manifests, note IDs and observed note hashes, evidence and
assessment author. Its reviewer must differ from the assessor and the manifest
editors and reviewers. Confirm the evidence describes the issue and that the
review covers its full scope: finding an ID in a report alone does not prove
semantic relevance. The issue remains open; this result verifies the candidate,
not deployment or an integration recorded against another checkout.

The independent review JSON uses exactly these fields:

```json
{
  "schema_version": 1,
  "review_kind": "candidate-integration",
  "decision": "verified",
  "issue_id": "<ISSUE_ID>",
  "issue_sha256": "<ISSUE_FINGERPRINT>",
  "candidate_sha256": "<CANDIDATE_FINGERPRINT>",
  "reviewer": "<INDEPENDENT_REVIEWER>",
  "assessment_author": "<ASSESSMENT_AUTHOR>",
  "manifests": [{"path": "/path/to/manifest.json", "sha256": "<SHA256>"}],
  "note_ids": ["<NOTE_ID>"],
  "note_hashes": {"<NOTE_ID>": "<OBSERVED_SHA256>"},
  "evidence": [{"path": "/path/to/report.json", "sha256": "<SHA256>"}],
  "rationale": "<INDEPENDENT_REVIEW_OF_THE_COMPLETE_ISSUE>"
}
```

Use the manifest references in assessment order, sorted note IDs, and evidence
sorted by path. The candidate fingerprint is defined below. Observe the current
archived note hashes; do not represent them as earlier historical hashes.

## Unrelated issues

An unprotected `audit`, `review` or `operational` issue in `investigate` status may be
classified `unrelated` after an independent review confirms that neither its
sources and targets nor its functional dependencies affect the candidate. The
assessment records `scope_review` with the issue and candidate fingerprints,
distinct reviewer and assessment author, and the rationale, candidate relation
and dependency review. Use an empty `evidence` list for that record. The gate
does not read external source, target or evidence files for this classification.
A source reference may include its saved `note_id` alongside `path` and `sha256`.
The complete source reference remains bound to the issue fingerprint.
The reviewer must independently establish relevance; the gate validates the
bindings but does not authenticate the reviewer or prove semantic independence.
The issue remains open and is reported separately. The parser snapshot skips exact canonical external paths covered by an unrelated audit
when no other assessed issue requires those paths. Protected reconciliations
and ambiguous cases remain subject to their existing checks.

## Independent review

The independent reviewer must differ from the assessment author. For an audit,
use exactly these fields; replace every placeholder with actual reviewed values:

```json
{
  "schema_version": 1,
  "review_kind": "candidate-audit-relevance",
  "decision": "unrelated",
  "issue_id": "<ISSUE_ID>",
  "issue_sha256": "<ISSUE_FINGERPRINT>",
  "candidate_sha256": "<CANDIDATE_FINGERPRINT>",
  "reviewer": "<INDEPENDENT_REVIEWER>",
  "assessment_author": "<ASSESSMENT_AUTHOR>",
  "rationale": "<REVIEWED_RATIONALE>",
  "candidate_relation": "<CANDIDATE_RELATION>",
  "dependency_review": "<DEPENDENCY_REVIEW>",
  "targets": [{"path": "/path/to/target", "sha256": "<CURRENT_SHA256>"}],
  "evidence": [{"path": "/path/to/report", "sha256": "<CURRENT_SHA256>"}]
}
```

For a review issue, use the same fields with `review_kind` set to
`candidate-issue-relevance` and include `sources` alongside `targets` and
`evidence`, each an array of current `{ "path": "/path", "sha256": "..." }`
references. Sources and targets must be nonempty. Review evidence must cover the
exact issue and candidate, not merely a related topic.

Use the issue fingerprint from the assessment template. The candidate fingerprint
is `learning_pipeline.fingerprint(assessment["candidate"])`, not the raw JSON
file hash. Sort `sources`, `targets` and `evidence` by path when present; the three narrative fields and
assessment author must exactly match that issue's assessment record.

## User authorization

Save a separate authorization report with exactly these fields. The `thread_id`,
request reference and timestamp must come from the real user authorization;
placeholders do not grant authority.

```json
{
  "schema_version": 1,
  "decision": "approved",
  "scope": "candidate-audit-deferral",
  "issue_id": "<ISSUE_ID>",
  "issue_sha256": "<ISSUE_FINGERPRINT>",
  "candidate_sha256": "<CANDIDATE_FINGERPRINT>",
  "independent_review": {"path": "/path/to/review.json", "sha256": "<REVIEW_SHA256>"},
  "user_request_reference": "<ACTUAL_USER_REQUEST>",
  "thread_id": "<ACTUAL_THREAD_UUID>",
  "recorded_at": "<TIME_WITH_TIMEZONE>"
}
```

For an audit, set `audit_deferral.independent_review` and
`audit_deferral.authorization` to the canonical absolute paths and SHA-256
values of these reports. For a review issue, set the authorization `scope` to
`candidate-review-release-deferral` and put the two references in the matching
`review_deferral` field instead. Do not reuse an audit authorization. Verify the authorization still applies, then run
`pipeline-release-status --release-assessment <ASSESSMENT_PATH>
--release-version <VERSION>` from the candidate checkout. The gate validates
bindings and current bytes, not semantic truth or reviewer identity. Deferred
issues remain open. Any changed candidate, issue or evidence requires renewed
review; a changed remote target requires fetching and reassessing first.
