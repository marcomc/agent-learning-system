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
        learning_dir = directory / "learning-base"
        learning_dir.mkdir()
        config = directory / "config.env"
        config.write_text(
            "\n".join(
                [
                    f'AGENT_LEARNING_DIR="{learning_dir}"',
                    'AGENT_LEARNING_STORE_NAME="AI Agent Learnings"',
                    'AGENT_LEARNING_EMAIL="user@example.test"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return config

    def write_legacy_config(self, directory: Path) -> Path:
        vault = directory / "vault"
        vault.mkdir()
        config = directory / "legacy-config.env"
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

    def test_new_config_uses_agent_learning_dir_as_base_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_config(directory)
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            self.assertEqual(root, directory / "learning-base" / "AI Agent Learnings")

    def test_legacy_vault_config_still_uses_dir_as_store_name(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_legacy_config(directory)
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            self.assertEqual(root, directory / "vault" / "AI Agent Learnings")

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

    def test_notify_refuses_placeholder_email_for_msmtp(self) -> None:
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

## Review Decision

<!-- BEGIN AGENT LEARNING REVIEW -->
- [ ] Approve: promote this rule
- [ ] Retry: I edited the rule, reprocess it
- [ ] Reject: do not promote this rule
<!-- END AGENT LEARNING REVIEW -->
""",
                encoding="utf-8",
            )
            result = self.run_helper(config, "notify", "--send-msmtp", check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn("AGENT_LEARNING_EMAIL is not configured", result.stderr)

    def test_hook_review_skills_only_updates_detected_review_skills(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_config(directory)
            skills = directory / "skills"
            review_skill = skills / "pre-pr-review-gate" / "SKILL.md"
            non_review_skill = skills / "doc-helper" / "SKILL.md"
            review_skill.parent.mkdir(parents=True)
            non_review_skill.parent.mkdir(parents=True)
            review_skill.write_text(
                """---
name: pre-pr-review-gate
description: Use when reviewing a branch before opening a PR.
---

# Pre PR Review Gate

Run the local review gate.
""",
                encoding="utf-8",
            )
            non_review_skill.write_text(
                """---
name: doc-helper
description: Use when editing documentation.
---

# Doc Helper

Review the final prose manually before publishing.
""",
                encoding="utf-8",
            )

            result = self.run_helper(
                config,
                "hook-review-skills",
                "--skills-dir",
                str(skills),
                "--apply",
            )

            self.assertIn("HOOKED pre-pr-review-gate", result.stdout)
            self.assertIn("BEGIN RECORD AGENT LEARNING HOOK", review_skill.read_text(encoding="utf-8"))
            self.assertNotIn(
                "BEGIN RECORD AGENT LEARNING HOOK",
                non_review_skill.read_text(encoding="utf-8"),
            )

    def test_hook_review_skills_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_config(directory)
            skills = directory / "skills"
            review_skill = skills / "max-findings-review" / "SKILL.md"
            review_skill.parent.mkdir(parents=True)
            review_skill.write_text(
                """---
name: max-findings-review
description: Use when performing a review that maximizes real findings.
---

# Max Findings Review
""",
                encoding="utf-8",
            )

            self.run_helper(config, "hook-review-skills", "--skills-dir", str(skills), "--apply")
            result = self.run_helper(config, "hook-review-skills", "--skills-dir", str(skills), "--apply")

            text = review_skill.read_text(encoding="utf-8")
            self.assertEqual(text.count("BEGIN RECORD AGENT LEARNING HOOK"), 1)
            self.assertIn("ALREADY-HOOKED max-findings-review", result.stdout)

    def test_hook_review_skills_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_config(directory)
            skills = directory / "skills"
            review_skill = skills / "branch-qa-remediation" / "SKILL.md"
            review_skill.parent.mkdir(parents=True)
            original = """---
name: branch-qa-remediation
description: Use when running PR-style QA review and remediation.
---

# Branch QA Remediation
"""
            review_skill.write_text(original, encoding="utf-8")

            result = self.run_helper(config, "hook-review-skills", "--skills-dir", str(skills))

            self.assertIn("WOULD-HOOK branch-qa-remediation", result.stdout)
            self.assertEqual(review_skill.read_text(encoding="utf-8"), original)

    def test_hook_review_skills_updates_matching_repository_copy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_config(directory)
            installed = directory / "installed"
            repository = directory / "repository" / "skills"
            installed_skill = installed / "pre-pr-review-gate" / "SKILL.md"
            repository_skill = repository / "pre-pr-review-gate" / "SKILL.md"
            installed_skill.parent.mkdir(parents=True)
            repository_skill.parent.mkdir(parents=True)
            skill_text = """---
name: pre-pr-review-gate
description: Use when reviewing a branch before opening a PR.
---

# Pre PR Review Gate
"""
            installed_skill.write_text(skill_text, encoding="utf-8")
            repository_skill.write_text(skill_text, encoding="utf-8")

            result = self.run_helper(
                config,
                "hook-review-skills",
                "--skills-dir",
                str(installed),
                "--repo-skills-dir",
                str(repository),
                "--apply",
            )

            self.assertIn("HOOKED pre-pr-review-gate", result.stdout)
            self.assertIn("REPO-HOOKED pre-pr-review-gate", result.stdout)
            self.assertIn("BEGIN RECORD AGENT LEARNING HOOK", installed_skill.read_text(encoding="utf-8"))
            self.assertIn("BEGIN RECORD AGENT LEARNING HOOK", repository_skill.read_text(encoding="utf-8"))

    def test_hook_review_skills_does_not_duplicate_symlinked_repository_copy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_config(directory)
            installed = directory / "installed"
            repository = directory / "repository" / "skills"
            repository_skill_dir = repository / "scoped-pre-pr-remediation"
            repository_skill = repository_skill_dir / "SKILL.md"
            installed.mkdir()
            repository_skill_dir.mkdir(parents=True)
            repository_skill.write_text(
                """---
name: scoped-pre-pr-remediation
description: Use when reviewing and fixing a branch before PR review.
---

# Scoped Pre PR Remediation
""",
                encoding="utf-8",
            )
            (installed / "scoped-pre-pr-remediation").symlink_to(repository_skill_dir)

            result = self.run_helper(
                config,
                "hook-review-skills",
                "--skills-dir",
                str(installed),
                "--repo-skills-dir",
                str(repository),
                "--apply",
            )

            self.assertIn("HOOKED scoped-pre-pr-remediation", result.stdout)
            self.assertNotIn("REPO-HOOKED scoped-pre-pr-remediation", result.stdout)
            text = repository_skill.read_text(encoding="utf-8")
            self.assertEqual(text.count("BEGIN RECORD AGENT LEARNING HOOK"), 1)

    def test_audit_rules_detects_duplicates_and_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_config(directory)
            rules_dir = directory / "rules"
            rules_dir.mkdir()
            file_a = rules_dir / "a.md"
            file_b = rules_dir / "b.md"
            file_a.write_text(
                "\n".join(
                    [
                        "# Rules",
                        "",
                        "- Always require a negative-path test when rejecting legacy inputs.",
                        "- Prefer wrapper commands for common flags.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            file_b.write_text(
                "\n".join(
                    [
                        "# More rules",
                        "",
                        "- Always require a negative path test when rejecting legacy inputs.",
                        "- Prefer wrapper command for common flags.",
                        "- Allow wrapper commands to be bypassed without documenting setup steps.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            result = self.run_helper(config, "audit-rules", "--path", str(rules_dir), "--all-md")
            payload = json.loads(result.stdout)
            self.assertGreaterEqual(len(payload["exact_duplicates"]), 1)
            self.assertGreaterEqual(len(payload["near_duplicates"]), 1)
            self.assertIsInstance(payload["potential_conflicts"], list)


if __name__ == "__main__":
    unittest.main()
