# Noon Agent Learning Consolidation

Use `$consolidate-agent-learnings` in
`/path/to/agent-learning-system`.

Process new `inbox/` notes and honor explicit `needs-review/` decisions. Inspect
`prepare-run.review_analysis`: at the configured threshold (default eight
pending notes), reassess the queue jointly, partitioning related contexts and
using complementary evidence across full notes. Smaller related cohorts may be
reviewed earlier when discovered during normal work.
Re-run `prepare-run` after inbox processing. Follow the skill's exact target
overlap checks for dirty/untracked projects and its residual-receipt completion
gate; moving notes to review does not complete a due Joint Review.

When configured autonomous instruction review is enabled, apply grounded
technical lessons or verify existing coverage without per-batch confirmation.
Save source/target hashes and decisions under `reports/`, then finalize with
`--review-evidence` and `--enforce-routing`. Preserve explicit human decisions
and ambiguous boxes. Investigate factual doubts yourself; keep genuine product,
security and operational choices pending and ask one grouped question per
decision family. Do not reread processed history except for a specific duplicate
or conflict, or verify archives explicitly referenced by the same review receipt.

Do not commit or push. Write a consolidation report and validate every changed
Markdown or shell file. Finalize promoted notes with routing metadata and
`--enforce-routing`. For detection-only lessons, use
`--scope skill-detection --detection-target ... --enforce-routing`. Omit
enforcement only for rejected, no-op, or pending-review outcomes. Use
`create-template-draft` for reusable template candidates before any curated
template edit.

After promotions, summarize recurrence and run a rule audit against installed
promotion targets to catch duplicates or potential conflicts:

```bash
repo="/path/to/agent-learning-system"
python3 "${repo}/scripts/agent_learning.py" summarize-runs \
  --format markdown
```

```bash
repo="/path/to/agent-learning-system"
python3 "${repo}/scripts/agent_learning.py" audit-rules \
  --path "$HOME/AGENTS.md" \
  --path "$HOME/.agents/skills"
```
