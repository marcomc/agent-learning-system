# Weekly Agent Learning Release PR

Run every Monday after the integrated weekly learning cycle. Work only in the configured
Agent Learning System repository.

Inspect matching open release PRs before checking for new uncommitted changes.
Resume an existing release's unfinished review or notification lifecycle even
when its working tree is clean. If it is already ready for user approval, report
that state and suppress its email only when a saved provider receipt confirms
delivery for this same PR/version. Otherwise resume the notification step;
reconcile an uncertain prior submission with the provider before any retry.
Save the submission intent and resulting receipt in the release report/chat.
Only when no
matching release exists and no eligible changes remain should this run stop.

For a new release:

Run `python3 scripts/agent_learning.py pipeline-release-status` first. It must
confirm this week's discovery and a verified completed consolidation, including
post-verification of changed targets. Preserve failure/unknown results as
pending work. Unrelated unresolved instruction choices do not establish a defect
in this repository's release; assess its own scoped pre-PR evidence.

1. Reuse a verified existing release branch where appropriate. Use
   `$update-release` to derive the next version and scope from the
   authoritative changelog order, current target branch, and all uncommitted
   changes. Update `CHANGELOG.md` with that same version; do not publish a
   release or merge a pull request.
2. Create `release/vX.Y.Z` from the verified target with `--no-track`, where
   `vX.Y.Z` is the derived changelog version. Include the scoped release delta
   only; stop on a branch, version, target, or dirty-scope ambiguity.
3. Run `$scoped-pre-pr-remediation` on the complete proposed commit, including
   untracked files. Continue only from `READY` with the required privacy and
   security evidence.
4. Commit the validated release delta, push the branch with an explicit
   source-to-destination refspec, and create a GitHub pull request titled
   `Release vX.Y.Z`. Use `$pr` for the body.
5. Start `$codex-pr-review-remediation-loop` for that exact pull request and
   head. It may perform its bounded remediation lifecycle, but must never
   merge, publish a GitHub release, deploy, or change unrelated work.
6. Send one email with the PR URL, version, current review state, and a clear
   statement that the user must approve and merge it. Prefer Gmail; if Gmail is
   unavailable, write the same plain-text body to a temporary file and use one
   `python3 scripts/agent_learning.py send-email --subject "Release vX.Y.Z" \
   --body-file <file>` fallback.

Stop and report any unavailable credential, review, validation, branch, or mail
condition. Do not infer a sent email, a clean remote review, an approved PR, or
a completed release.
