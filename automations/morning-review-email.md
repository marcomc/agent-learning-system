# Morning Agent Learning Review Email

Check the configured Obsidian `AI Agent Learnings/needs-review/` directory.

If there are no pending review notes, do nothing and do not send email. If
there are pending notes, send one concise email to the configured recipient
with the count, note paths, project names, and review reasons.

Prefer the Gmail connector. If Gmail is unavailable, run:

```bash
repo="/path/to/agent-learning-system"
python3 "${repo}/scripts/agent_learning.py" notify --send-msmtp
```

Do not modify learning notes from this automation.
