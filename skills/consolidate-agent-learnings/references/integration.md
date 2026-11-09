# Integration Verification

Read this contract before reporting a learning as integrated or completing a
scheduled run. `processed` records archival; `integration_status: verified`
requires a current integration receipt. Missing or stale proof remains pending.
The mandatory [coherence contract](coherence.md) governs semantic review before
promotion; distribution parity alone does not establish integration. Attach its
`coherence` object to the manifest example below before verification.
The state index owns current integration and template-promotion status; archived
frontmatter retains the original archival decision.

## Resolve and apply

1. Resolve each instruction or skill's owning source and installation mode.
   Compare concurrent edits and reconcile bounded differences in the source.
   Review local overrides explicitly rather than overwriting personalized data.
2. Validate the owning source, then synchronize its complete eligible payload
   into the shared skill collection and every configured installation. Include
   references, helpers, tests and metadata. Preserve installation-only config.
3. Review pending template handoffs under configured delegated instruction-review
   authority or explicit user authority. Decide each draft: promote, defer with
   concrete evidence and next action, reject, or already covered. Revalidate
   obsolete template IDs. A clean draft without approval is still pending.
4. Apply approved clean handoffs, discover generated consumers in the configured
   roots, refresh affected managed blocks preserving project-local rules and
   concurrent work, and verify fresh generator output. Record any unrefreshed
   consumer as pending; an out-of-sync report alone is incomplete distribution.
5. Verify already-applied handoffs too. Mark their handoff `applied` only after
   source and consumer verification, retaining authority and receipt references.

## Receipt manifest

Save a private JSON manifest under the configured learning store's `reports/`.
Use one coherent group of note IDs; record semantic review and validation in the
rationale or linked report. Hashes prove identity, not rule correctness.

```json
{
  "version": 1,
  "note_ids": ["<archived-note-id>"],
  "rationale": "Reviewed the failure, owner, rule coverage and validation in the saved run report.",
  "files": [{
    "path": "/source/skill/SKILL.md",
    "sha256": "<current-sha256>",
    "contains": ["<reviewed rule text>"]
  }],
  "skills": [{
    "source": "/source/skill",
    "copies": ["/shared/collection/skill", "/canonical/skills/skill", "/runtime/skills/skill"]
  }]
}
```

List every configured copy, including the shared collection mirror. The verifier
also discovers matching installations in conventional agent roots and rejects
omitted copies; list additional custom roots in `skill_roots`. Only
`config/template_repo_path.txt` may be declared in a skill's `local_files`
array as installation-only configuration. Verify that each existing binding
resolves to the owning template repository and include its hash and reviewed
value in `files`; preserving a config file alone does not validate its binding.
Cache directories are excluded;
unexpected payload files fail parity. Known intentional private overrides need
a separate disposition and cannot be called complete source parity.

For template promotion, set `template_status` to `promoted`, include the curated
template in `files`, and add `template_consumers`:

```json
{
  "repository": "/source/templates-repository",
  "roots": ["/configured/project-root"],
  "max_depth": 2,
  "template_ids": ["python", "generic-development"]
}
```

`template_consumers` is an array of these objects. The verifier discovers and
checks current generated consumers using the owning generator, requiring its
PyYAML dependency. For a global template, use `global_projections` containing
explicit active global instruction paths, each covered in `files`. Record the
configured scan roots and depth; unavailable roots are a verification failure.

## Verify, record and recheck

```bash
python3 scripts/agent_learning.py integration-check --manifest <manifest.json>
python3 scripts/agent_learning.py integration-record --manifest <manifest.json>
python3 scripts/agent_learning.py integration-audit \
  --note-id <archived-note-id> --require-verified
```

`integration-check` reads only. `integration-record` accepts only processed notes
inside the configured store, saves an immutable receipt under `reports/`, and
updates the state index. It does not copy files or approve drafts. Audit rereads
receipt hashes, current source files, complete skill trees and generated
consumers; drift makes the result pending even if the index says verified.

Run `integration-audit` without note filters for historical traceability debt;
missing historical receipts are unknown, not proof that rules are absent.
Resolve scoped newly discovered drift during consolidation. Technical work stays
with the agent; genuine choices and installation conflicts retain explicit
pending dispositions and next actions.

Finish scheduled consolidation with `pipeline-finish --require-integration`.
`AGENT_LEARNING_REQUIRE_INTEGRATION=1` also enforces this gate when the flag is
omitted and revalidates the completed cycle before release preparation.
Every note archived as processed during that run needs current receipt proof,
including already-covered outcomes. Unfinished handoffs remain pending and the
run remains partial. Report local application, reusable-source promotion and
installed distribution separately. Never label draft creation as integration.
