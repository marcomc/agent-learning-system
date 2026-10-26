# Weekly Agent Learning Rule Audit

Run once a week (audit-only). Do not modify learning notes. Do not commit or
push.

## Goal

Catch long-term drift: duplicate or potentially conflicting rules that have
accumulated across `$HOME/AGENTS.md` and installed `$HOME/.agents/skills`.
Also report repeated lessons after a prevention target exists.

## Steps

Run the audit:

```bash
repo="/path/to/agent-learning-system"
python3 "${repo}/scripts/agent_learning.py" audit-rules \
  --path "$HOME/AGENTS.md" \
  --path "$HOME/.agents/skills" \
  > /tmp/agent-learning-audit.json
```

Summarize recurrence:

```bash
repo="/path/to/agent-learning-system"
python3 "${repo}/scripts/agent_learning.py" summarize-runs \
  --format markdown \
  > /tmp/agent-learning-recurrence.md
```

If `potential_conflicts`, `near_duplicates`, missing routing, or duplicate
after-prevention counts are non-empty, write a short Obsidian note under
`AI Agent Learnings/reports/` summarizing:

- counts for `exact_duplicates`, `near_duplicates`, `potential_conflicts`;
- recurrence counts and missing routing count;
- the top 3 highest-ratio pairs with file paths + line numbers;
- the recommended action: merge/clarify scope or move to `needs-review`.

If the audit and recurrence summary are clean, do nothing.
