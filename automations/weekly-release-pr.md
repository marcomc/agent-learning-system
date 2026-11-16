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

From the release candidate checkout, run `python3 scripts/agent_learning.py
pipeline-release-status` before committing. If an explicitly authorized
post-release check is bound to this candidate, pass `--release-version VERSION`
using the exact version in its candidate changelog. For audit findings or review
issues that may qualify for a user-authorized exception, first complete candidate
preparation and review. Use the scoped assessment for eligible unrelated
findings and bound candidate integration checks. Then
fetch `origin/main` with `git fetch origin refs/heads/main:refs/remotes/origin/main`
and save `pipeline-release-assessment` outside the checkout. Use `pipeline-status`
for issue details; fill the blank evidence hashes and obtain independent semantic
review of each issue and cohort against the final candidate and its dependencies.
Record the assessment as reconciliation for this candidate only; issues stay open.
Pass `--release-assessment <ASSESSMENT_PATH>` only for the reviewed current
result. Keep unknown or relevant findings blocking under the applicable checks.
Use the candidate scope review for eligible findings independently shown to be
unrelated. For a deferral that requires an exception, obtain direct user
authorization and independent review. A saved receipt alone does not establish
authority. Follow `docs/scoped-release-assessment.md` for eligibility and the
review and receipt schemas. Closed
cohorts require current verified evidence. Reassess after changes and compare
`git ls-remote --heads origin refs/heads/main` to the assessed target before
pushing. If the target changed, stop, refresh it and reassess the candidate.
Readiness lists post-release checks; keep them open for verification after
publication. The command must confirm this week's discovery and a verified completed consolidation, including
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
   security evidence. Re-run release readiness for the final candidate version
   and checkout after remediation; refresh any changed post-release binding.
4. With explicit user authorization, commit the validated release delta. After
   committing, regenerate and re-review any scoped assessment for the new HEAD,
   refresh candidate-bound follow-ups, and recheck readiness. Push the branch
   with an explicit source-to-destination refspec, and create a pull request titled
   `Release vX.Y.Z`. Use `$pr` for the body.
5. Start `$codex-pr-review-remediation-loop` for that exact pull request and
   head. It may perform its bounded remediation lifecycle, but must never
   merge, publish a GitHub release, deploy, or change unrelated work.
   After the loop, if the PR head or candidate changed, regenerate and
   re-review any scoped assessment before rechecking readiness. Refresh the
   follow-up binding only while the user's authorization still covers that
   candidate; otherwise report the pending revalidation.
6. Send one email with the PR URL, version, current review state, and a clear
   statement that the user must approve and merge it. Prefer Gmail; if Gmail is
   unavailable, write the same plain-text body to a temporary file and use one
   `python3 scripts/agent_learning.py send-email --subject "Release vX.Y.Z" \
   --body-file <file>` fallback.

Stop and report any unavailable credential, review, validation, branch, or mail
condition. Do not infer a sent email, a clean remote review, an approved PR, or
a completed release.
