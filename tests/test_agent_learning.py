from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agent_learning.py"


class AgentLearningTests(unittest.TestCase):
    def run_helper(
        self,
        config: Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--config", str(config), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def write_config(self, directory: Path) -> Path:
        vault = directory / "vault"
        vault.mkdir()
        config = directory / "config.env"
        config.write_text(
            "\n".join(
                [
                    f'AGENT_LEARNING_VAULT="{vault}"',
                    'AGENT_LEARNING_DIR="AI Agent Learnings"',
                    'AGENT_LEARNING_EMAIL="user@example.test"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return config

    def test_record_creates_inbox_note(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            self.run_helper(config, "init-store")
            result = self.run_helper(
                config,
                "record",
                "--project-path",
                "/tmp/project",
                "--source-skill",
                "test-skill",
                "--title",
                "Config drift broke validation",
                "--problem",
                "Validation checked a different config key than rendering.",
                "--root-cause",
                "The new setting was only wired into one path.",
                "--fix",
                "Use the same source field for validation and rendering.",
                "--verification",
                "Unit test covered both paths.",
                "--future-detection",
                "Compare validation and render inputs.",
                "--future-prevention",
                "Add config keys across load, validate, render, examples, and tests together.",
            )
            note = Path(result.stdout.strip())
            self.assertTrue(note.exists())
            self.assertEqual(note.parent.name, "inbox")
            text = note.read_text(encoding="utf-8")
            self.assertIn("status: \"inbox\"", text)
            self.assertIn("## Future Prevention", text)

    def test_prepare_run_reads_checkbox_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = root / "needs-review" / "review.md"
            note.write_text(
                """---
id: "abc"
status: "needs-review"
project_path: "/tmp/project"
---
# Review me

## Review Decision

<!-- BEGIN AGENT LEARNING REVIEW -->
- [x] Approve: promote this rule
- [ ] Retry: I edited the rule, reprocess it
- [ ] Reject: do not promote this rule
<!-- END AGENT LEARNING REVIEW -->
""",
                encoding="utf-8",
            )
            result = self.run_helper(config, "prepare-run")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["needs_review"][0]["review_decision"], "approved")

    def test_finalize_note_is_idempotent_in_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = root / "inbox" / "done.md"
            note.write_text(
                """---
id: "abc"
status: "inbox"
---
# Done
""",
                encoding="utf-8",
            )
            result = self.run_helper(
                config,
                "finalize-note",
                "--file",
                str(note),
                "--status",
                "processed",
                "--rationale",
                "Already covered.",
            )
            moved = Path(result.stdout.strip())
            self.assertTrue(moved.exists())
            state = json.loads((root / "state" / "processed.json").read_text(encoding="utf-8"))
            self.assertEqual(state["processed"]["abc"]["status"], "processed")
            self.assertIn("/processed/", state["processed"]["abc"]["path"])

    def test_finalize_note_rejects_file_outside_learning_store(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_config(directory)
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = directory / "outside.md"
            original = """---
id: "outside"
status: "inbox"
---
# Outside
"""
            note.write_text(original, encoding="utf-8")
            result = self.run_helper(
                config,
                "finalize-note",
                "--file",
                str(note),
                "--status",
                "processed",
                "--rationale",
                "Out of scope.",
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("outside learning store", result.stderr)
            self.assertEqual(note.read_text(encoding="utf-8"), original)
            state = json.loads((root / "state" / "processed.json").read_text(encoding="utf-8"))
            self.assertNotIn("outside", state["processed"])

    def test_finalize_note_can_keep_review_note_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = root / "needs-review" / "review.md"
            note.write_text(
                """---
id: "review"
status: "needs-review"
---
# Review
""",
                encoding="utf-8",
            )
            result = self.run_helper(
                config,
                "finalize-note",
                "--file",
                str(note),
                "--status",
                "needs-review",
                "--rationale",
                "Still needs review.",
            )
            self.assertEqual(Path(result.stdout.strip()), note)
            self.assertTrue(note.exists())
            self.assertFalse((root / "needs-review" / "review-2.md").exists())


if __name__ == "__main__":
    unittest.main()
