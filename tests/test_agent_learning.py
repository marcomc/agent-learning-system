from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agent_learning.py"


def load_agent_learning_module():
    spec = importlib.util.spec_from_file_location("agent_learning_under_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load agent_learning module for tests.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentLearningTests(unittest.TestCase):
    def run_helper(
        self,
        config: Path,
        *args: str,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_LEARNING_")}
        clean_env.update(env or {})
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--config", str(config), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
            env=clean_env,
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
            self.assertIn("lesson_family: \"config-drift-broke-validation\"", text)
            self.assertIn("prevention_targets: \"[]\"", text)
            self.assertIn("template_upstream_status: \"not-applicable\"", text)

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

    def test_finalize_note_records_routing_metadata(self) -> None:
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
                "Promoted to reusable template atom.",
                "--lesson-family",
                "markdown-validation-contract",
                "--scope",
                "atom",
                "--prevention-target",
                "atom:documentation-validation",
                "--detection-target",
                "skill:pre-pr-review-docs-release",
                "--template-upstream-status",
                "draft-created",
                "--routing-rationale",
                "Documentation agents load the atom before editing Markdown.",
                "--recurrence-check",
                "new",
                "--enforce-routing",
            )
            moved = Path(result.stdout.strip())
            text = moved.read_text(encoding="utf-8")
            self.assertIn("lesson_family: \"markdown-validation-contract\"", text)
            self.assertIn("prevention_targets: \"[\\\"atom:documentation-validation\\\"]\"", text)
            state = json.loads((root / "state" / "processed.json").read_text(encoding="utf-8"))
            self.assertEqual(state["processed"]["abc"]["scope"], "atom")
            self.assertEqual(state["processed"]["abc"]["template_upstream_status"], "draft-created")

    def test_finalize_note_enforces_prevention_targets_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = root / "inbox" / "done.md"
            original = """---
id: "abc"
status: "inbox"
---
# Done
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
                "Missing prevention target.",
                "--lesson-family",
                "missing-target",
                "--scope",
                "global",
                "--routing-rationale",
                "Global agents load this before acting.",
                "--enforce-routing",
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("requires --prevention-target", result.stderr)
            self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_finalize_note_treats_empty_json_prevention_targets_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = root / "inbox" / "empty-targets.md"
            original = """---
id: "empty-targets"
status: "inbox"
lesson_family: "empty-targets"
scope: "atom"
prevention_targets: "[]"
detection_targets: "[]"
routing_rationale: "Atom-scope lessons require a concrete prevention target."
---
# Empty Targets
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
                "Empty JSON list is not a target.",
                "--enforce-routing",
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("requires --prevention-target", result.stderr)
            self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_finalize_note_enforces_routing_rationale_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = root / "inbox" / "done.md"
            original = """---
id: "abc"
status: "inbox"
---
# Done
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
                "Missing routing rationale.",
                "--lesson-family",
                "missing-rationale",
                "--scope",
                "atom",
                "--prevention-target",
                "atom:docs",
                "--enforce-routing",
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("requires --routing-rationale", result.stderr)
            self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_finalize_note_enforces_detection_target_for_detection_scope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = root / "inbox" / "detect.md"
            note.write_text(
                """---
id: "detect"
status: "inbox"
---
# Detect
""",
                encoding="utf-8",
            )
            missing = self.run_helper(
                config,
                "finalize-note",
                "--file",
                str(note),
                "--status",
                "processed",
                "--rationale",
                "Detection-only route.",
                "--lesson-family",
                "detection-only",
                "--scope",
                "skill-detection",
                "--routing-rationale",
                "Review skill catches the issue before final response.",
                "--enforce-routing",
                check=False,
            )
            self.assertEqual(missing.returncode, 2)
            self.assertIn("requires --detection-target", missing.stderr)

            result = self.run_helper(
                config,
                "finalize-note",
                "--file",
                str(note),
                "--status",
                "processed",
                "--rationale",
                "Detection-only route.",
                "--lesson-family",
                "detection-only",
                "--scope",
                "skill-detection",
                "--detection-target",
                "skill:pre-pr-review-docs-release",
                "--routing-rationale",
                "Review skill catches the issue before final response.",
                "--enforce-routing",
            )
            moved = Path(result.stdout.strip())
            self.assertTrue(moved.exists())

    def test_concurrent_finalize_note_preserves_state_entries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            notes = []
            for note_id in ["one", "two"]:
                note = root / "inbox" / f"{note_id}.md"
                note.write_text(
                    f"""---
id: "{note_id}"
status: "inbox"
---
# {note_id}
""",
                    encoding="utf-8",
                )
                notes.append(note)

            clean_env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_LEARNING_")}
            processes = [
                subprocess.Popen(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--config",
                        str(config),
                        "finalize-note",
                        "--file",
                        str(note),
                        "--status",
                        "processed",
                        "--rationale",
                        f"Processed {note.stem}.",
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=clean_env,
                )
                for note in notes
            ]
            for process in processes:
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr or stdout)

            state = json.loads((root / "state" / "processed.json").read_text(encoding="utf-8"))
            self.assertIn("one", state["processed"])
            self.assertIn("two", state["processed"])

    def test_finalize_note_lock_failure_keeps_note_in_queue(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = root / "inbox" / "locked.md"
            original = """---
id: "locked"
status: "inbox"
---
# Locked
"""
            note.write_text(original, encoding="utf-8")
            (root / "state" / ".processed.lock").mkdir()

            result = self.run_helper(
                config,
                "finalize-note",
                "--file",
                str(note),
                "--status",
                "processed",
                "--rationale",
                "State lock is held.",
                check=False,
                env={"AGENT_LEARNING_STATE_LOCK_TIMEOUT_SECONDS": "0.05"},
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("Timed out waiting for state lock", result.stderr)
            self.assertEqual(note.read_text(encoding="utf-8"), original)
            self.assertFalse(list((root / "processed").glob("**/*.md")))

    def test_finalize_note_does_not_reclaim_owned_state_lock_by_age(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = root / "inbox" / "old-lock.md"
            original = """---
id: "old-lock"
status: "inbox"
---
# Old Lock
"""
            note.write_text(original, encoding="utf-8")
            lock_dir = root / "state" / ".processed.lock"
            lock_dir.write_text(json.dumps({"pid": os.getpid()}) + "\n", encoding="utf-8")
            old_time = time.time() - 3600
            os.utime(lock_dir, (old_time, old_time))

            result = self.run_helper(
                config,
                "finalize-note",
                "--file",
                str(note),
                "--status",
                "processed",
                "--rationale",
                "State lock is held.",
                check=False,
                env={"AGENT_LEARNING_STATE_LOCK_TIMEOUT_SECONDS": "0.05"},
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("Timed out waiting for state lock", result.stderr)
            self.assertTrue(lock_dir.exists())
            self.assertEqual(note.read_text(encoding="utf-8"), original)
            self.assertFalse(list((root / "processed").glob("**/*.md")))

    def test_finalize_note_reclaims_old_ownerless_state_lock(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = root / "inbox" / "ownerless.md"
            note.write_text(
                """---
id: "ownerless"
status: "inbox"
---
# Ownerless
""",
                encoding="utf-8",
            )
            lock_dir = root / "state" / ".processed.lock"
            lock_dir.mkdir()
            old_time = time.time() - 3600
            os.utime(lock_dir, (old_time, old_time))

            result = self.run_helper(
                config,
                "finalize-note",
                "--file",
                str(note),
                "--status",
                "processed",
                "--rationale",
                "Ownerless lock recovered.",
                "--lesson-family",
                "lock-recovery",
                "--scope",
                "project-local",
                "--prevention-target",
                "project-local:agent-learning-system",
                "--routing-rationale",
                "Project automation preserves queue state.",
                "--enforce-routing",
                env={"AGENT_LEARNING_STATE_LOCK_OWNERLESS_GRACE_SECONDS": "0.01"},
            )

            moved = Path(result.stdout.strip())
            self.assertTrue(moved.exists())
            self.assertFalse(lock_dir.exists())

    def test_state_lock_falls_back_when_hard_links_are_unavailable(self) -> None:
        agent_learning = load_agent_learning_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original_link = agent_learning.os.link

            def fail_link(_source: Path, _dest: Path) -> None:
                raise OSError("hard links unavailable")

            agent_learning.os.link = fail_link
            try:
                with agent_learning.state_update_lock(root):
                    lock_path = root / "state" / ".processed.lock"
                    self.assertTrue(lock_path.exists())
                    self.assertEqual(json.loads(lock_path.read_text(encoding="utf-8"))["pid"], os.getpid())
                self.assertFalse((root / "state" / ".processed.lock").exists())
            finally:
                agent_learning.os.link = original_link

    def test_finalize_note_malformed_state_keeps_note_in_queue(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            note = root / "inbox" / "bad-state.md"
            original = """---
id: "bad-state"
status: "inbox"
---
# Bad State
"""
            note.write_text(original, encoding="utf-8")
            state_path = root / "state" / "processed.json"
            state_path.write_text("{", encoding="utf-8")

            result = self.run_helper(
                config,
                "finalize-note",
                "--file",
                str(note),
                "--status",
                "processed",
                "--rationale",
                "Malformed state blocks processing.",
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("State file is malformed", result.stderr)
            self.assertEqual(note.read_text(encoding="utf-8"), original)
            self.assertFalse(list((root / "processed").glob("**/*.md")))

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

    def test_write_report_can_include_routing_decision(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self.write_config(Path(raw))
            root = Path(self.run_helper(config, "init-store").stdout.strip())
            result = self.run_helper(
                config,
                "write-report",
                "--run-id",
                "run-1",
                "--summary",
                "Processed one reusable lesson.",
                "--lesson-family",
                "markdown-validation-contract",
                "--scope",
                "atom",
                "--prevention-target",
                "atom:documentation-validation",
                "--template-upstream-status",
                "draft-created",
                "--routing-rationale",
                "Documentation agents load the atom before editing Markdown.",
                "--recurrence-check",
                "new",
                "--enforce-routing",
            )
            report = Path(result.stdout.strip())
            self.assertEqual(report.parent, root / "reports")
            text = report.read_text(encoding="utf-8")
            self.assertIn("## Routing Decision", text)
            self.assertIn("Lesson family: `markdown-validation-contract`", text)

    def test_summarize_runs_counts_recurrence(self) -> None:
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
            self.run_helper(
                config,
                "finalize-note",
                "--file",
                str(note),
                "--status",
                "processed",
                "--rationale",
                "Repeated after prevention.",
                "--lesson-family",
                "markdown-validation-contract",
                "--scope",
                "atom",
                "--prevention-target",
                "atom:documentation-validation",
                "--recurrence-check",
                "duplicate-after-prevention",
            )
            rejected = root / "inbox" / "rejected.md"
            rejected.write_text(
                """---
id: "rejected"
status: "inbox"
---
# Rejected
""",
                encoding="utf-8",
            )
            self.run_helper(
                config,
                "finalize-note",
                "--file",
                str(rejected),
                "--status",
                "rejected",
                "--rationale",
                "Not reusable.",
            )
            result = self.run_helper(config, "summarize-runs", "--format", "json")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["processed_count"], 2)
            self.assertEqual(payload["duplicate_after_prevention_count"], 1)
            self.assertEqual(payload["missing_routing_count"], 0)
            self.assertEqual(payload["lesson_families"]["markdown-validation-contract"], 1)

            datetime_since = self.run_helper(
                config,
                "summarize-runs",
                "--since",
                "2026-06-01T12:00:00",
                check=False,
            )
            self.assertEqual(datetime_since.returncode, 2)
            self.assertIn("YYYY-MM-DD", datetime_since.stderr)

    def test_create_template_draft_uses_source_note_filename_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_config(directory)
            template_repo = directory / "templates"
            template_repo.mkdir()
            (template_repo / "templates.yml").write_text("templates: []\n", encoding="utf-8")
            source_note = directory / "private" / "note.md"
            result = self.run_helper(
                config,
                "create-template-draft",
                "--template-repo",
                str(template_repo),
                "--lesson-family",
                "markdown-validation-contract",
                "--source-note",
                str(source_note),
                "--proposed-template",
                "documentation-validation",
                "--candidate-rule",
                "Run markdownlint before completing Markdown documentation changes.",
                "--prevention-target",
                "atom:documentation-validation",
                "--routing-rationale",
                "Documentation agents load the atom before editing Markdown.",
                "--privacy-verdict",
                "clean",
                "--enforce-routing",
            )
            draft = Path(result.stdout.strip())
            text = draft.read_text(encoding="utf-8")
            self.assertIn("Source note: `note.md`", text)
            self.assertNotIn(str(source_note), text)

    def test_create_template_draft_requires_clean_privacy_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_config(directory)
            template_repo = directory / "templates"
            template_repo.mkdir()
            (template_repo / "templates.yml").write_text("templates: []\n", encoding="utf-8")
            result = self.run_helper(
                config,
                "create-template-draft",
                "--template-repo",
                str(template_repo),
                "--lesson-family",
                "blocked",
                "--source-note",
                "note.md",
                "--proposed-template",
                "docs",
                "--candidate-rule",
                "Run markdownlint before completing Markdown documentation changes.",
                "--prevention-target",
                "atom:docs",
                "--privacy-verdict",
                "blocked",
                "--routing-rationale",
                "Docs agents load this atom before editing documentation.",
                "--enforce-routing",
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("requires --privacy-verdict clean", result.stderr)
            self.assertFalse((template_repo / ".work").exists())

    def test_create_template_draft_rejects_private_candidate_rule(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self.write_config(directory)
            template_repo = directory / "templates"
            template_repo.mkdir()
            (template_repo / "templates.yml").write_text("templates: []\n", encoding="utf-8")
            result = self.run_helper(
                config,
                "create-template-draft",
                "--template-repo",
                str(template_repo),
                "--lesson-family",
                "private-path",
                "--source-note",
                "note.md",
                "--proposed-template",
                "docs",
                "--candidate-rule",
                "Do not hard-code 10.0.0.1 in docs.",
                "--prevention-target",
                "atom:docs",
                "--privacy-verdict",
                "needs-scrub",
                "--enforce-routing",
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("must be scrubbed", result.stderr)

    def test_create_template_draft_rejects_linux_local_candidate_rule(self) -> None:
        candidate_rules = [
            "Do not write /home/alice/vault into shared handoffs.",
            "Do not write /workspace/private-repo into shared handoffs.",
        ]
        for candidate_rule in candidate_rules:
            with self.subTest(candidate_rule=candidate_rule):
                with tempfile.TemporaryDirectory() as raw:
                    directory = Path(raw)
                    config = self.write_config(directory)
                    template_repo = directory / "templates"
                    template_repo.mkdir()
                    (template_repo / "templates.yml").write_text("templates: []\n", encoding="utf-8")
                    result = self.run_helper(
                        config,
                        "create-template-draft",
                        "--template-repo",
                        str(template_repo),
                        "--lesson-family",
                        "private-path",
                        "--source-note",
                        "note.md",
                        "--proposed-template",
                        "docs",
                        "--candidate-rule",
                        candidate_rule,
                        "--prevention-target",
                        "atom:docs",
                        "--privacy-verdict",
                        "clean",
                        "--enforce-routing",
                        check=False,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("must be scrubbed", result.stderr)
                    self.assertFalse((template_repo / ".work").exists())

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
