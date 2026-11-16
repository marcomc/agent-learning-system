# Historical integration targets across worktrees

An archived note keeps its original concrete routing target. To review an
unchanged target that moved between worktrees of the same Git repository, add an
explicit entry to the integration manifest's `relocations` array. This is a
coverage proof, not a change to the note or a publication claim.

Each entry contains `note_id`, absolute `source` and `target`, absolute
`source_root`, `target_root` and `common_dir`, exact `source_head` and
`target_head`, and `note`, `snapshot` and `prior_receipt` objects containing
absolute `path` and SHA-256 `sha256` fields. The note and prior receipt must be
the ones recorded in the learning store. The snapshot must independently match
the old target's hash in that receipt and the candidate's current bytes.

Both paths must have the same repository-relative name, be tracked in registered
worktrees with the same Git common directory, and remain within their worktrees.
The candidate must be an explicit reviewed coherence target. The verifier rejects
missing or changed evidence, circular snapshots, untracked or escaped paths,
changed Git identities, and ambiguous mappings. It rechecks these conditions
while recording and during subsequent integration audits. If historical proof is
missing, retain the issue for separate review.

When the successor differs, an explicit version 2 mapping may instead describe
the reviewed change. Add `schema_version: 2`, `relation: semantic-successor`,
the current `target_sha256`, and `note_impacts` covering every note in the
historical receipt. Each impact identifies the archived note and its hash,
recorded prevention and detection targets, a `preserved` or `updated`
disposition, and a rationale. Include all those note IDs in the new manifest.
Use a separate proposal for each historical receipt and its complete note group.
The coherence review must use council review, with the target marked `refine`
and `before` referencing the exact historical snapshot. Every mapping for the
same receipt must agree on impacts. Changes to any note or its routing invalidate
the group receipt until reviewed again. Add a mapping for each note that routes
to the shared historical target. Retain unresolved impacts for review.

Use `coherence-proposal`, obtain the required independent review for the exact
proposal, then run `integration-check`, `integration-record`, and a scoped
`integration-audit --require-verified`. Retain the old receipt as provenance.

When a previously recorded relocation later needs a semantic successor, retain
the original source, snapshot, prior receipt, note and worktree identities in
the successor mapping. The current receipt must contain exactly one matching
earlier mapping, a coherent reviewed proposal, current independent review
reports and the corresponding verified target hash. Shared review reports are
checked once per receipt and re-read after the group. A changed or ambiguous
lineage is rejected; review and record the
new successor normally, including all notes in the original receipt group.
