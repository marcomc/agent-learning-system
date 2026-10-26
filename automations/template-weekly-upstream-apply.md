# Agent Template Weekly Upstream Apply

Use the local agent-template repository in
`${HOME}/Development/agents-file-templates-and-skills`.

Run the reviewed template upstream workflow:

1. Inspect pending reusable-template handoffs:

   ```bash
   python3 skills/update-agents-file-templates/scripts/update_agents_templates.py \
     --template-repo . \
     --learning-upstream-summary
   ```

2. Inspect `.work/learning-upstream/*.md`.

   Apply only drafts whose handoff explicitly says:

   - `Review status: approved`
   - `Privacy verdict: clean`

3. For each approved clean draft, run a dry run first:

   ```bash
   python3 skills/update-agents-file-templates/scripts/update_agents_templates.py \
     --template-repo . \
     --apply-learning-draft "<draft-path>"
   ```

4. Apply only after the dry run is correct:

   ```bash
   python3 skills/update-agents-file-templates/scripts/update_agents_templates.py \
     --template-repo . \
     --apply-learning-draft "<draft-path>" \
     --apply
   ```

5. Skip draft, needs-scrub, blocked, ambiguous, private-looking,
   duplicate-looking, or unreviewed handoffs. Report each skipped draft with
   the reason.

6. Validate the template workflow:

   ```bash
   markdownlint --config "${HOME}/.markdownlint.json" \
     README.md CHANGELOG.md TODO.md docs/*.md templates/**/*.md skills/**/*.md
   python3 scripts/privacy_scan.py .
   python3 -m py_compile \
     scripts/privacy_scan.py \
     skills/init-agents-file/scripts/init_agents_file.py \
     skills/update-agents-file-templates/scripts/update_agents_templates.py \
     tests/test_template_workflows.py
   python3 -m unittest discover -s tests
   ```

7. Run the out-of-sync scan:

   ```bash
   python3 skills/update-agents-file-templates/scripts/update_agents_templates.py \
     --template-repo . \
     --scan-root "${HOME}/Development" \
     --out-of-sync-report
   ```

Final response must include approved drafts applied, skipped drafts and reasons,
validation results, out-of-sync summary, and any remaining manual action.

Do not stage, commit, push, open pull requests, or edit unrelated files.
